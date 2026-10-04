"""Device profiles: how a panel is rendered (CSS size, pixel ratio, bit depth, framework classes).

Framework reference: https://trmnl.com/framework/docs/3.3/devices
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceProfile:
    key: str
    name: str
    width: int  # CSS pixels, landscape
    height: int
    pixel_ratio: float
    bit_depth: int  # 1, 2 or 4
    size_class: str  # sm, md, lg
    screen_classes: tuple[str, ...]
    image_format: str  # "bmp" or "png"
    short_name: str = ""
    # For panels without a framework device class: set the screen size directly.
    override_size: bool = False
    # Colour panels: the RGB inks the panel can show. When set, images keep their
    # colour and are snapped to these inks (an indexed PNG) instead of grayscale.
    palette: tuple[tuple[int, int, int], ...] = ()

    def css_size(self, orientation="landscape"):
        if orientation == "portrait":
            return self.height, self.width
        return self.width, self.height

    def image_size(self, orientation="landscape"):
        width, height = self.css_size(orientation)
        return round(width * self.pixel_ratio), round(height * self.pixel_ratio)

    def style(self):
        """Inline CSS variables for panels the framework has no device class for."""
        if not self.override_size:
            return ""
        return f"--screen-w: {self.width}px; --screen-h: {self.height}px;"

    def classes(self, orientation="landscape"):
        classes = list(self.screen_classes)
        if orientation == "portrait":
            classes.append("screen--portrait")
        return classes

    @property
    def content_type(self):
        return "image/bmp" if self.image_format == "bmp" else "image/png"


PROFILES = {
    profile.key: profile
    for profile in [
        DeviceProfile(
            key="og",
            name="TRMNL OG (1-bit)",
            width=800,
            height=480,
            pixel_ratio=1,
            bit_depth=1,
            size_class="md",
            screen_classes=("screen--og", "screen--md", "screen--1bit"),
            # 1-bit PNG, like TRMNL's own og_png model (https://trmnl.com/api/models).
            image_format="png",
            short_name="OG",
        ),
        DeviceProfile(
            key="og_2bit",
            name="TRMNL OG (2-bit, firmware 1.6+)",
            width=800,
            height=480,
            pixel_ratio=1,
            bit_depth=2,
            size_class="md",
            screen_classes=("screen--ogv2", "screen--md", "screen--2bit"),
            image_format="png",
            short_name="OG 2-bit",
        ),
        DeviceProfile(
            key="x",
            name="TRMNL X (4-bit)",
            width=1040,
            height=780,
            pixel_ratio=1.8,
            bit_depth=4,
            size_class="lg",
            screen_classes=("screen--v2", "screen--lg", "screen--density-2x", "screen--4bit"),
            image_format="png",
            short_name="X",
        ),
        DeviceProfile(
            key="small_400x300",
            name="Small 400×300 (1-bit, e.g. Zectrix Note 4)",
            width=400,
            height=300,
            pixel_ratio=1,
            bit_depth=1,
            size_class="sm",
            screen_classes=("screen--sm", "screen--1bit"),
            # Firmware BMP support is 800x480 only (lib/trmnl/src/bmp.cpp), so other sizes need PNG.
            image_format="png",
            short_name="400×300",
            override_size=True,
        ),
        DeviceProfile(
            key="color_400x300",
            name="Colour 400×300 (black/white/yellow/red, e.g. Zectrix Note 4C)",
            width=400,
            height=300,
            pixel_ratio=1,
            bit_depth=2,
            size_class="sm",
            # 1-bit classes so the framework draws its grays as black/white
            # patterns; a BWYR panel has no gray ink. Colours in the markup pass through.
            screen_classes=("screen--sm", "screen--1bit"),
            image_format="png",
            short_name="400×300 colour",
            override_size=True,
            # The firmware maps each palette entry to the nearest ink (GetBWYRPixel).
            palette=((0, 0, 0), (255, 255, 255), (255, 255, 0), (255, 0, 0)),
        ),
    ]
}

PROFILE_CHOICES = [(profile.key, profile.name) for profile in PROFILES.values()]

# Values a device may report in a ``Model`` header, mapped to profile keys.
MODEL_ALIASES = {
    "og": "og",
    "og_png": "og",
    "og_2bit": "og_2bit",
    "v2": "x",
    "x": "x",
    "trmnl_x": "x",
    "zectrix_note4": "small_400x300",
    "zectrix_note4c": "color_400x300",
}


def get_profile(key):
    return PROFILES.get(key) or PROFILES["og"]
