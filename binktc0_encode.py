"""Encode RGB(A) to Granny's BinkTC0 texture format, by calling granny2.dll.

The counterpart to `binktc0_decode.py`. There is no hand-written encoder and there does not
need to be: `granny2.dll` exports the builder half of the texture API, so the game's own
codec does the work and the output is by construction what the engine expects.

    from binktc0_encode import encode_binktc0
    payload = encode_binktc0(width, height, rgb_bytes)   # rgb_bytes is w*h*3

**This module only works under a 32-bit interpreter.** `granny2.dll` is 32-bit; a 64-bit
python raises `OSError: [WinError 193] %1 is not a valid Win32 application` on load. On this
machine that is `%LOCALAPPDATA%\\Programs\\Python\\Python312-32\\python.exe`. The module
refuses to import under a 64-bit build rather than failing later with a confusing error.

Three things cost time when this was first done, all of them now handled here:

* **Feed RGBA8888, not RGB.** Three-byte input produces a payload nearly twice the size and
  decodes at the wrong hue, even though the texture's own `Layout` reports `BytesPerPixel=3`.
  `encode_binktc0` takes RGB and widens it for you.
* **The `granny_texture` struct layout is not in any doc we had.** It is declared below from
  the SDK headers, and then *checked* -- `Width`, `Height` and `ImageCount` are read back and
  compared to what was passed before any pointer inside the struct is dereferenced. A wrong
  layout otherwise shows up as a garbage pointer and a crash, or worse, a plausible-looking
  payload of nonsense.
* **Expect the payload to grow.** You are recompressing an image that has already been
  through the codec once. Observed on a 256x256 buckler skin: 13,304 bytes in, 16,580 out,
  a factor of 1.25. An earlier note in Lionheart Fixt predicted "several times larger" from
  a single 5x observation; 1.25x is also normal, so budget for growth but do not treat a
  small increase as a sign something went wrong.

**Verify by round-tripping, not by inspecting.** Decode the payload you just produced with
`binktc0_decode` and compare against the source pixels. A correct encode comes back with a
mean absolute error around 0.07 and a maximum of about 2 out of 255 -- effectively lossless.
If the error is large, or the image is hue-shifted, the input was RGB rather than RGBA.
"""
from __future__ import annotations

import ctypes
import struct
import sys
import zlib
from ctypes import POINTER, Structure, c_char_p, c_int32, c_uint8, c_void_p
from pathlib import Path

if struct.calcsize("P") * 8 != 32:
    raise ImportError(
        "binktc0_encode requires a 32-bit interpreter: granny2.dll is 32-bit and a 64-bit "
        "python raises WinError 193 loading it. Try "
        r"%LOCALAPPDATA%\Programs\Python\Python312-32\python.exe")

DLL = r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader\granny2.dll"


class PixelLayout(Structure):
    _fields_ = [("BytesPerPixel", c_int32),
                ("ShiftForComponent", c_int32 * 4),
                ("BitsForComponent", c_int32 * 4)]


class MipLevel(Structure):
    _fields_ = [("Stride", c_int32), ("PixelByteCount", c_int32), ("Pixels", POINTER(c_uint8))]


class TextureImage(Structure):
    _fields_ = [("MIPLevelCount", c_int32), ("MIPLevels", POINTER(MipLevel))]


class Variant(Structure):
    _fields_ = [("Type", c_void_p), ("Object", c_void_p)]


class Texture(Structure):
    """granny_texture, from the SDK headers. Verified at runtime, not trusted."""
    _fields_ = [("FromFileName", c_char_p),
                ("TextureType", c_int32),
                ("Width", c_int32),
                ("Height", c_int32),
                ("Encoding", c_int32),
                ("SubFormat", c_int32),
                ("Layout", PixelLayout),
                ("ImageCount", c_int32),
                ("Images", POINTER(TextureImage)),
                ("ExtendedData", Variant)]


BINK_ENCODING = 3


def encode_binktc0(width: int, height: int, rgb: bytes, dll_path: str = DLL) -> bytes:
    """RGB8 (width*height*3) -> a BinkTC0 payload, ready to splice into a .gr2."""
    assert len(rgb) == width * height * 3, \
        "expected %d bytes of RGB, got %d" % (width * height * 3, len(rgb))
    g = ctypes.WinDLL(dll_path)

    begin = getattr(g, "_GrannyBeginBinkTexture@16")
    begin.restype, begin.argtypes = c_void_p, [c_int32, c_int32, c_int32, c_int32]
    encode = getattr(g, "_GrannyEncodeImage@24")
    encode.restype, encode.argtypes = None, [c_void_p, c_int32, c_int32, c_int32, c_int32, c_void_p]
    end = getattr(g, "_GrannyEndTexture@4")
    end.restype, end.argtypes = POINTER(Texture), [c_void_p]

    rgba = bytearray(width * height * 4)
    for i in range(width * height):
        rgba[i * 4:i * 4 + 3] = rgb[i * 3:i * 3 + 3]
        rgba[i * 4 + 3] = 255
    buf = (c_uint8 * len(rgba)).from_buffer(rgba)

    builder = begin(width, height, 1, 0)
    assert builder, "GrannyBeginBinkTexture returned null"
    encode(builder, width, height, width * 4, 1, ctypes.byref(buf))
    tex = end(builder)
    assert tex, "GrannyEndTexture returned null"
    t = tex.contents

    # check the struct layout before dereferencing anything inside it
    assert (t.Width, t.Height) == (width, height), (
        "granny_texture layout is wrong: Width/Height read back as %s/%s, expected %s/%s"
        % (t.Width, t.Height, width, height))
    assert t.Encoding == BINK_ENCODING, "encoding came back as %s" % t.Encoding
    assert t.ImageCount >= 1 and t.Images, "no images returned"
    img = t.Images[0]
    assert img.MIPLevelCount >= 1 and img.MIPLevels, "no mip levels returned"
    lvl = img.MIPLevels[0]
    n = lvl.PixelByteCount
    assert 0 < n < 10 * 1024 * 1024, "implausible payload size %d" % n
    return bytes(bytearray(lvl.Pixels[:n]))


# --------------------------------------------------------------------------- CLI
def _read_png_rgb(path: Path) -> tuple[int, int, bytearray]:
    """Minimal 8-bit RGB PNG reader, so the CLI needs no third-party imaging library."""
    d = path.read_bytes()
    assert d[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    i, idat, w, h = 8, b"", None, None
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        tag, body = d[i + 4:i + 8], d[i + 8:i + 8 + ln]
        if tag == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", body[:10])
            assert (bd, ct) == (8, 2), "expected 8-bit RGB, got depth %d colour type %d" % (bd, ct)
        elif tag == b"IDAT":
            idat += body
        i += 12 + ln
    raw, stride = zlib.decompress(idat), w * 3
    out, prev, p = bytearray(), bytearray(stride), 0
    for _ in range(h):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        for k in range(stride):
            a = line[k - 3] if k >= 3 else 0
            b = prev[k]
            c = prev[k - 3] if k >= 3 else 0
            if f == 1:
                line[k] = (line[k] + a) & 255
            elif f == 2:
                line[k] = (line[k] + b) & 255
            elif f == 3:
                line[k] = (line[k] + ((a + b) >> 1)) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[k] = (line[k] + (a if (pa <= pb and pa <= pc) else (b if pb <= pc else c))) & 255
        out += line
        prev = line
    return w, h, out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        print("Usage: python binktc0_encode.py <in.png> <out.binktc0>")
        raise SystemExit(1)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    w, h, rgb = _read_png_rgb(src)
    payload = encode_binktc0(w, h, rgb)
    Path(dst).write_bytes(payload)
    print("%s  %dx%d -> %s  (%d bytes)" % (src.name, w, h, dst.name, len(payload)))
