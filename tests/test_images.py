import io

import pytest
from PIL import Image

from django_trmnl_byos.devices import MODEL_ALIASES, PROFILES
from django_trmnl_byos.images import encode_bmp, encode_palette_png, encode_png, placeholder, to_device_image


def gradient(width, height):
    return Image.linear_gradient("L").resize((width, height))


@pytest.mark.parametrize("bits", [1, 2, 4])
def test_png_is_grayscale_at_requested_depth(bits):
    data = encode_png(gradient(803, 20), bits)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert (data[24], data[25]) == (bits, 0)  # IHDR bit depth, colour type 0 (grayscale)
    image = Image.open(io.BytesIO(data))
    image.load()
    assert image.size == (803, 20)
    assert len(set(image.convert("L").tobytes())) == 2**bits


def test_bmp_matches_trmnl_og_format():
    data = encode_bmp(gradient(800, 480))
    assert data[:2] == b"BM"
    assert int.from_bytes(data[14:18], "little") == 40  # BITMAPINFOHEADER, i.e. BMP3
    assert int.from_bytes(data[28:30], "little") == 1  # 1 bit per pixel
    assert len(data) == 48062  # same size TRMNL's ImageMagick guide produces


def test_to_device_image_sizes():
    screenshot = io.BytesIO()
    gradient(1872, 1404).save(screenshot, format="PNG")
    data = to_device_image(screenshot.getvalue(), PROFILES["x"])
    assert Image.open(io.BytesIO(data)).size == (1872, 1404)
    portrait = to_device_image(screenshot.getvalue(), PROFILES["og"], "portrait")
    assert Image.open(io.BytesIO(portrait)).size == (480, 800)


def test_placeholder_formats():
    assert placeholder(PROFILES["og"], "landscape", "Hi", ["there"])[:4] == b"\x89PNG"
    assert placeholder(PROFILES["x"], "landscape", "Hi", ["there"])[:4] == b"\x89PNG"


def test_palette_png_snaps_to_panel_inks():
    profile = PROFILES["color_400x300"]
    image = Image.new("RGB", (400, 300), "white")
    image.paste((0, 0, 0), (0, 0, 100, 300))
    image.paste((250, 220, 20), (100, 0, 200, 300))  # yellowish -> yellow ink
    image.paste((200, 30, 40), (200, 0, 300, 300))  # reddish -> red ink
    data = encode_palette_png(image, profile.palette)
    assert (data[24], data[25]) == (2, 3)  # IHDR: 2-bit, colour type 3 (indexed)
    decoded = Image.open(io.BytesIO(data))
    decoded.load()
    assert decoded.size == (400, 300)
    rgb = decoded.convert("RGB")
    assert [rgb.getpixel((x, 150)) for x in (50, 150, 250, 350)] == [
        (0, 0, 0),
        (255, 255, 0),
        (255, 0, 0),
        (255, 255, 255),
    ]


def test_colour_profile_device_image_and_placeholder():
    profile = PROFILES["color_400x300"]
    screenshot = io.BytesIO()
    Image.new("RGB", (400, 300), (255, 0, 0)).save(screenshot, format="PNG")
    data = to_device_image(screenshot.getvalue(), profile)
    assert data[25] == 3
    assert Image.open(io.BytesIO(data)).convert("RGB").getpixel((0, 0)) == (255, 0, 0)
    assert placeholder(profile, "landscape", "Hi", ["there"])[25] == 3


def test_note4c_model_maps_to_colour_profile():
    assert MODEL_ALIASES["zectrix_note4c"] == "color_400x300"
