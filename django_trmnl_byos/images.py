"""Turn screenshots into images the TRMNL firmware can draw.

Mirrors TRMNL's ImageMagick guide (https://docs.trmnl.com/go/diy/imagemagick-guide):
1-bit BMP3 for the OG, and 1/2/4-bit *grayscale* PNGs for newer firmware.

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


def encode_png(image, bits):
    """Grayscale PNG at 1, 2 or 4 bits per pixel (PNG colour type 0)."""
    if bits not in (1, 2, 4, 8):
        raise ValueError(f"Unsupported bit depth: {bits}")
    width, height = image.size
    indexes = quantize(image, bits)
    if bits == 8:
        packed = indexes.tobytes()
    else:
        # Pillow's palette packers write N-bit samples MSB-first, one padded row at a time.
        packed = Image.frombytes("P", image.size, indexes.tobytes()).tobytes("raw", f"P;{bits}")
    stride = (width * bits + 7) // 8
    raw = b"".join(
        b"\x00" + packed[row * stride : (row + 1) * stride] for row in range(height)
    )

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, bits, 0, 0, 0, 0)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", header),
            chunk(b"IDAT", zlib.compress(raw, 9)),
            chunk(b"IEND", b""),
        ]
    )


def to_device_image(png_bytes, profile, orientation="landscape"):
    image = Image.open(io.BytesIO(png_bytes)).convert("L")
    size = profile.image_size(orientation)
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    if profile.image_format == "bmp":
        return encode_bmp(image)
    return encode_png(image, profile.bit_depth)


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
    if profile.image_format == "bmp":
        return encode_bmp(image)
    return encode_png(image, profile.bit_depth)
