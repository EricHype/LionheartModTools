"""Parser/serializer for Lionheart's brace-delimited resource text format.

Grammar (inferred from extracted .txt / .InventoryItem / .Quest.txt / .can files):

    file       := TypeName NEWLINE "{" fields "}"
    fields     := (field)*
    field      := KEY "=" VALUE NEWLINE                  -- leaf field
                | KEY "=" TypeName NEWLINE "{" fields "}" -- nested object
    - Lines are tab-indented; indentation is cosmetic, nesting is brace-delimited.
    - KEY may contain spaces (even a trailing space before "="); VALUE may be empty.
    - "Array" is not special-cased: it's just a TypeName whose fields are a leading
      "Item Count=N" field followed by N fields that repeat the same key name.
    - All leaf values are kept as raw strings (no int/float coercion) so re-serializing
      an untouched tree reproduces the original bytes exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field as _field
from pathlib import Path
from typing import Union

Value = Union[str, "ResourceNode"]


@dataclass
class ResourceNode:
    type_name: str
    fields: list[tuple[str, Value]] = _field(default_factory=list)

    def get(self, key: str, default=None) -> Value | None:
        for k, v in self.fields:
            if k == key:
                return v
        return default

    def get_all(self, key: str) -> list[Value]:
        return [v for k, v in self.fields if k == key]

    def set(self, key: str, value: Value) -> None:
        for i, (k, _) in enumerate(self.fields):
            if k == key:
                self.fields[i] = (key, value)
                return
        self.fields.append((key, value))

    def items(self) -> list["ResourceNode"]:
        """Convenience for Array-style nodes: the child objects, in order, ignoring 'Item Count'."""
        return [v for k, v in self.fields if isinstance(v, ResourceNode)]

    def to_dict(self) -> dict:
        return {
            "type": self.type_name,
            "fields": [
                [k, v.to_dict() if isinstance(v, ResourceNode) else v]
                for k, v in self.fields
            ],
        }

    @staticmethod
    def from_dict(d: dict) -> "ResourceNode":
        return ResourceNode(
            type_name=d["type"],
            fields=[
                (k, ResourceNode.from_dict(v) if isinstance(v, dict) else v)
                for k, v in d["fields"]
            ],
        )

    def to_text(self) -> str:
        lines: list[str] = [self.type_name, "{"]
        _write_fields(self, lines, depth=1)
        lines.append("}")
        return "\r\n".join(lines) + "\r\n"


def _write_fields(node: ResourceNode, lines: list[str], depth: int) -> None:
    indent = "\t" * depth
    for key, value in node.fields:
        if isinstance(value, ResourceNode):
            lines.append(f"{indent}{key}={value.type_name}")
            lines.append(f"{indent}{{")
            _write_fields(value, lines, depth + 1)
            lines.append(f"{indent}}}")
        else:
            lines.append(f"{indent}{key}={value}")


def parse_resource_text(text: str) -> ResourceNode:
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Indentation is tabs only; strip it, but keep everything else (keys/values
    # can carry meaningful literal spaces, e.g. "Value if True =CConstant").
    lines = [l.lstrip("\t") for l in raw_lines]
    lines = [l for l in lines if l != ""]

    pos = 0

    def peek():
        return lines[pos] if pos < len(lines) else None

    def advance():
        nonlocal pos
        line = lines[pos]
        pos += 1
        return line

    def parse_object(type_name: str) -> ResourceNode:
        # Caller has already consumed this object's opening '{'.
        node = ResourceNode(type_name=type_name)
        while True:
            line = peek()
            if line is None:
                raise ValueError(f"Unexpected end of input inside {type_name!r} block")
            if line == "}":
                advance()
                return node
            if line == "{":
                raise ValueError(f"Unexpected '{{' with no preceding key= in {type_name!r} block")
            advance()
            if "=" not in line:
                raise ValueError(f"Expected key=value, got {line!r} in {type_name!r} block")
            key, _, value = line.partition("=")
            if peek() == "{":
                advance()
                node.fields.append((key, parse_object(value)))
            else:
                node.fields.append((key, value))

    if not lines:
        raise ValueError("Empty resource file")
    root_type = advance()
    if peek() != "{":
        raise ValueError(f"Expected '{{' after root type {root_type!r}, got {peek()!r}")
    advance()
    root = parse_object(root_type)
    if pos != len(lines):
        raise ValueError(f"Trailing content after root object: {lines[pos:pos + 5]!r}...")
    return root


def parse_resource_file(path) -> ResourceNode:
    raw = Path(path).read_bytes()
    # latin-1 is a lossless byte<->codepoint mapping: guarantees any byte
    # sequence round-trips exactly regardless of the file's real code page.
    return parse_resource_text(raw.decode("latin-1"))


def write_resource_file(node: ResourceNode, path) -> None:
    Path(path).write_bytes(node.to_text().encode("latin-1"))


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) != 2:
        print("usage: python resource_format.py <path-to-resource-file>")
        raise SystemExit(1)
    parsed = parse_resource_file(sys.argv[1])
    print(json.dumps(parsed.to_dict(), indent=2))
