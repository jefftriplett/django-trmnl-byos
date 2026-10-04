"""Turn screenshots into images the TRMNL firmware can draw.

Mirrors TRMNL's ImageMagick guide (https://docs.trmnl.com/go/diy/imagemagick-guide):
1-bit BMP3 for the OG, and 1/2/4-bit *grayscale* PNGs for newer firmware.
Colour profiles (``DeviceProfile.palette``) get an indexed PNG of their inks instead.

No dithering is applied: the framework already paints its grays as on/off
pixel patterns (1-bit) or flat tones (2/4-bit), so we only snap each pixel to
the nearest level.
"""

import io
import struct
import zlib

from PIL import Image, ImageDraw, ImageFont


def quantize(image, bits):
    """Grayscale image whose pixel values are level indexes 0..(2**bits - 1)."""
    levels = (1 << bits) - 1
    lut = [round(value * levels / 255) for value in range(256)]
    return image.convert("L").point(lut)


def encode_bmp(image):
    """1-bit BMP3 (40-byte BITMAPINFOHEADER), the classic TRMNL OG format."""
    indexes = quantize(image, 1)
    mono = indexes.point(lambda value: 255 if value else 0).convert("1", dither=Image.Dither.NONE)
    buffer = io.BytesIO()
    mono.save(buffer, format="BMP")
    return buffer.getvalue()


def _png_chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _pack_png(indexes, bits, colour_type, palette=b""):
    """PNG from an image of sample values (level or palette indexes) at ``bits`` per pixel."""
    width, height = indexes.size
    if bits == 8:
        packed = indexes.tobytes()
    else:
        # Pillow's palette packers write N-bit samples MSB-first, one padded row at a time.
        packed = Image.frombytes("P", indexes.size, indexes.tobytes()).tobytes("raw", f"P;{bits}")
    stride = (width * bits + 7) // 8
    raw = b"".join(
        b"\x00" + packed[row * stride : (row + 1) * stride] for row in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, bits, colour_type, 0, 0, 0)
    chunks = [_png_chunk(b"IHDR", header)]
    if palette:
        chunks.append(_png_chunk(b"PLTE", palette))
    chunks += [_png_chunk(b"IDAT", zlib.compress(raw, 9)), _png_chunk(b"IEND", b"")]
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def encode_png(image, bits):
    """Grayscale PNG at 1, 2 or 4 bits per pixel (PNG colour type 0)."""
    if bits not in (1, 2, 4, 8):
        raise ValueError(f"Unsupported bit depth: {bits}")
    return _pack_png(quantize(image, bits), bits, colour_type=0)


def encode_palette_png(image, palette):
    """Indexed PNG (colour type 3) snapping every pixel to the nearest ``palette`` ink.

    No dithering, for the same reason as the grayscale encoders: the framework's
    grays are already black/white patterns, and colours in the markup are flat.
    """
    if not 2 <= len(palette) <= 16:
        raise ValueError(f"Unsupported palette size: {len(palette)}")
    bits = 1 if len(palette) <= 2 else 2 if len(palette) <= 4 else 4
    flat = [channel for colour in palette for channel in colour]
    target = Image.new("P", (1, 1))
    # Pad with copies of the first ink so quantize can only pick real palette entries.
    target.putpalette(flat + flat[:3] * (256 - len(palette)))
    indexes = image.convert("RGB").quantize(palette=target, dither=Image.Dither.NONE)
    return _pack_png(indexes, bits, colour_type=3, palette=bytes(flat))


def encode_for_profile(image, profile):
    if profile.palette:
        return encode_palette_png(image, profile.palette)
    if profile.image_format == "bmp":
        return encode_bmp(image)
    return encode_png(image, profile.bit_depth)


def to_device_image(png_bytes, profile, orientation="landscape"):
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB" if profile.palette else "L")
    size = profile.image_size(orientation)
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return encode_for_profile(image, profile)


def placeholder(profile, orientation, heading, lines):
    """A plain status screen (setup, empty playlist, rendering…) drawn without a browser."""
    width, height = profile.image_size(orientation)
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    scale = height / 480
    heading_font = ImageFont.load_default(size=round(44 * scale))
    body_font = ImageFont.load_default(size=round(24 * scale))
    margin = round(48 * scale)
    draw.rectangle(
        [margin // 2, margin // 2, width - margin // 2, height - margin // 2],
        outline=0,
        width=max(2, round(3 * scale)),
    )
    y = margin * 2
    draw.text((margin, y), heading, font=heading_font, fill=0)
    y += round(80 * scale)
    for line in lines:
        draw.text((margin, y), line, font=body_font, fill=0)
        y += round(40 * scale)
    draw.text(
        (margin, height - margin * 2),
        "django-trmnl",
        font=body_font,
        fill=0,
    )
    return encode_for_profile(image, profile)
