"""Byte-exact deltas between a vanilla game resource and a modded one.

WHY
---
A mod that changes a shipped file has to ship the whole file, because the engine reads no
patch format -- so a 40-line edit to Crossroads.zax means redistributing 1.2 MB of the
publisher's map. Shipping a delta instead means a release carries only what the mod
author actually wrote, and reconstructs the rest from the copy the player already owns.

FORMAT
------
A delta is JSON:

    {"srcSha256": ..., "dstSha256": ..., "srcLen": N, "dstLen": M,
     "ops": [["c", offset, length], ["i", "<base64>"], ...]}

  ["c", offset, length]   copy `length` bytes from the source starting at `offset`
  ["i", base64]           append these literal bytes

Deliberately dumb to apply: the applier walks a byte array and never has to know anything
about lines, encodings or line endings. That matters because the applier is PowerShell,
where an off-by-one in line splitting would silently corrupt CRLF game data. All of the
subtlety lives here, in Python, where it is tested.

Matching is done line-wise for speed -- these are 20k-line text files and byte-level
matching would be far too slow -- but the emitted offsets are byte offsets, so the
format stays byte-oriented even though the search is not.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
from pathlib import Path

# Keep the terminator attached to its line so that joining is exactly concatenation.
# Splitting on \r\n vs \n separately would let a lone \n inside CRLF data resynchronise
# the two sides differently; this pattern cannot, because every byte lands in exactly
# one piece.
_LINE = re.compile(rb"[^\n]*\n|[^\n]+")


def _lines(data: bytes) -> list[bytes]:
    return _LINE.findall(data)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_delta(src: bytes, dst: bytes) -> dict:
    """Build a delta that turns `src` into `dst`."""
    src_lines, dst_lines = _lines(src), _lines(dst)

    # Byte offset of each source line, so equal-runs can be emitted as byte ranges.
    offsets, pos = [], 0
    for ln in src_lines:
        offsets.append(pos)
        pos += len(ln)
    offsets.append(pos)

    # autojunk would treat common lines ("}", a lone tab) as noise in files this size,
    # which wrecks the match quality on exactly the files that most need a small delta.
    sm = difflib.SequenceMatcher(None, src_lines, dst_lines, autojunk=False)

    ops: list[list] = []

    def emit_copy(off: int, length: int) -> None:
        if length <= 0:
            return
        if ops and ops[-1][0] == "c" and ops[-1][1] + ops[-1][2] == off:
            ops[-1][2] += length          # extend a contiguous copy
        else:
            ops.append(["c", off, length])

    def emit_insert(data: bytes) -> None:
        if not data:
            return
        if ops and ops[-1][0] == "i":
            ops[-1][1] = ops[-1][1] + data
        else:
            ops.append(["i", data])

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            emit_copy(offsets[i1], offsets[i2] - offsets[i1])
        elif tag in ("replace", "insert"):
            emit_insert(b"".join(dst_lines[j1:j2]))
        # 'delete' emits nothing

    encoded = [op if op[0] == "c" else ["i", base64.b64encode(op[1]).decode("ascii")]
               for op in ops]
    return {
        "srcSha256": sha256(src), "dstSha256": sha256(dst),
        "srcLen": len(src), "dstLen": len(dst), "ops": encoded,
    }


def apply_delta(src: bytes, delta: dict) -> bytes:
    """Reference implementation of the applier, and the check that the format is sound.

    The shipped applier is PowerShell; this exists so the generator can prove every delta
    round-trips before it is ever written to a release.
    """
    if sha256(src) != delta["srcSha256"]:
        raise ValueError("source does not match the delta's expected original")
    out = bytearray()
    for op in delta["ops"]:
        if op[0] == "c":
            _, off, length = op
            if off < 0 or off + length > len(src):
                raise ValueError("copy op runs past the end of the source")
            out += src[off:off + length]
        else:
            out += base64.b64decode(op[1])
    result = bytes(out)
    if sha256(result) != delta["dstSha256"]:
        raise ValueError("applying the delta did not reproduce the expected file")
    return result


def delta_stats(delta: dict) -> tuple[int, int]:
    """(bytes taken from the source, bytes carried literally in the delta)."""
    copied = sum(op[2] for op in delta["ops"] if op[0] == "c")
    literal = sum(len(base64.b64decode(op[1])) for op in delta["ops"] if op[0] == "i")
    return copied, literal


def write_delta(path: Path, delta: dict) -> None:
    path.write_text(json.dumps(delta, separators=(",", ":")), encoding="ascii",
                    newline="\n")
