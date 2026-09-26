import hashlib
import secrets
import string
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from . import conf, layouts
from .devices import PROFILE_CHOICES, get_profile

ORIENTATION_CHOICES = [("landscape", "Landscape"), ("portrait", "Portrait")]
WEEKDAYS = "0123456"  # Monday = 0, like date.weekday()


def generate_api_key():
    return secrets.token_urlsafe(24)


def generate_friendly_id():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


class PluginInstance(models.Model):
    """One configured copy of a plugin, and the data it renders (TRMNL calls this a PluginSetting)."""

    name = models.CharField(max_length=100)
    plugin = models.CharField(max_length=50, help_text="Which plugin draws this instance.")
    settings = models.JSONField(default=dict, blank=True, help_text="Plugin options, as JSON.")
    merge_variables = models.JSONField(
        default=dict,
        blank=True,
        help_text="Data the templates render. Filled by the webhook, by polling, or by hand.",
    )
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    refresh_interval = models.PositiveIntegerField(
        default=15, help_text="Minutes between data refreshes and re-renders."
    )
    data_refreshed_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_error = models.TextField(blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        from .plugins import registry

        if self.plugin not in registry:
            raise ValidationError({"plugin": f"Unknown plugin {self.plugin!r}."})

    def get_plugin(self):
        from .plugins import registry

        return registry.get(self.plugin)

    def refresh_due(self, now=None):
        plugin = self.get_plugin()
        if not plugin.polls:
            return False
        if self.data_refreshed_at is None:
            return True
        now = now or timezone.now()
        return now - self.data_refreshed_at >= timedelta(minutes=self.refresh_interval)


class Dashboard(models.Model):
    """What one screen shows: a single plugin, a fixed mashup, or a fluid 3x3 mashup."""

    name = models.CharField(max_length=100)
    layout = models.CharField(max_length=10, choices=layouts.LAYOUT_CHOICES, default="1x1")
    backdrop = models.BooleanField(default=False, help_text="Patterned background behind mashup views.")
    dark_mode = models.BooleanField(default=False)
    no_bleed = models.BooleanField(default=False, help_text="Remove the screen's outer padding.")
    extra_screen_classes = models.CharField(
        max_length=200,
        blank=True,
        help_text="More classes for the screen element, e.g. screen--scale-xxsmall.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_fluid(self):
        return self.layout == layouts.FLUID

    def touch(self):
        Dashboard.objects.filter(pk=self.pk).update(updated_at=timezone.now())

    def ordered_cells(self):
        return self.cells.select_related("instance").order_by("position", "row", "col", "pk")

    def refresh_interval(self):
        intervals = [cell.instance.refresh_interval for cell in self.ordered_cells()]
        return timedelta(minutes=min(intervals)) if intervals else None

    def latest_render(self, profile_key, orientation="landscape"):
        return (
            self.renders.filter(profile=profile_key, orientation=orientation)
            .order_by("-created_at")
            .first()
        )

    def needs_render(self, profile_key, orientation="landscape", now=None):
        render = self.latest_render(profile_key, orientation)
        if render is None:
            return True
        if render.created_at < self.updated_at:
            return True
        cells = list(self.ordered_cells())
        if any(cell.instance.updated_at > render.created_at for cell in cells):
            return True
        interval = self.refresh_interval()
        now = now or timezone.now()
        return interval is not None and now - render.created_at >= interval


class DashboardCell(models.Model):
    dashboard = models.ForeignKey(Dashboard, related_name="cells", on_delete=models.CASCADE)
    instance = models.ForeignKey(PluginInstance, related_name="cells", on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField(
        default=1, help_text="Slot number in a fixed layout, in markup order."
    )
    col = models.PositiveSmallIntegerField(default=1, help_text="Fluid only: starting column (1-3).")
    row = models.PositiveSmallIntegerField(default=1, help_text="Fluid only: starting row (1-3).")
    col_span = models.PositiveSmallIntegerField(default=1, help_text="Fluid only: columns spanned.")
    row_span = models.PositiveSmallIntegerField(default=1, help_text="Fluid only: rows spanned.")
    show_title_bar = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "row", "col"]

    def __str__(self):
        return f"{self.dashboard}: {self.instance}"

    @property
    def placement(self):
        return layouts.Placement(self.col, self.row, self.col_span, self.row_span)

    def view_size(self):
        if self.dashboard.is_fluid:
            return layouts.view_size_for_placement(self.placement)
        slots = layouts.FIXED_LAYOUTS[self.dashboard.layout]
        return slots[min(self.position, len(slots)) - 1]

    def clean(self):
        if not self.dashboard_id:
            return
        if self.dashboard.is_fluid:
            errors = layouts.placement_errors(self.placement)
            if errors:
                raise ValidationError("; ".join(errors))
        else:
            slots = layouts.slot_count(self.dashboard.layout)
            if not 1 <= self.position <= slots:
                raise ValidationError(
                    {"position": f"The {self.dashboard.layout} layout has slots 1 to {slots}."}
                )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.dashboard.touch()

    def delete(self, *args, **kwargs):
        dashboard = self.dashboard
        result = super().delete(*args, **kwargs)
        dashboard.touch()
        return result


class Playlist(models.Model):
    name = models.CharField(max_length=100)
    is_default = models.BooleanField(
        default=False, help_text="New devices start on this playlist."
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Playlist.objects.exclude(pk=self.pk).update(is_default=False)

    def active_items(self, when=None):
        when = timezone.localtime(when or timezone.now())
        return [item for item in self.items.select_related("dashboard") if item.is_active(when)]


class PlaylistItem(models.Model):
    playlist = models.ForeignKey(Playlist, related_name="items", on_delete=models.CASCADE)
    dashboard = models.ForeignKey(Dashboard, related_name="playlist_items", on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    refresh_rate = models.PositiveIntegerField(
        null=True, blank=True, help_text="Seconds to show this item. Blank uses the device's rate."
    )
    start_time = models.TimeField(null=True, blank=True, help_text="Only show from this local time.")
    end_time = models.TimeField(null=True, blank=True, help_text="Only show until this local time.")
    weekdays = models.CharField(
        max_length=7,
        default=WEEKDAYS,
        blank=True,
        help_text="Days to show, as digits: 0 = Monday … 6 = Sunday.",
    )

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return f"{self.playlist}: {self.dashboard}"

    def is_active(self, when):
        if not self.enabled:
            return False
        if self.weekdays and str(when.weekday()) not in self.weekdays:
            return False
        now = when.time()
        if self.start_time and self.end_time and self.start_time > self.end_time:
            # A window that crosses midnight, e.g. 22:00-06:00.
            return now >= self.start_time or now < self.end_time
        if self.start_time and now < self.start_time:
            return False
        if self.end_time and now >= self.end_time:
            return False
        return True


class Device(models.Model):
    name = models.CharField(max_length=100, default="TRMNL")
    mac_address = models.CharField(max_length=17, unique=True)
    api_key = models.CharField(max_length=64, unique=True, default=generate_api_key)
    friendly_id = models.CharField(max_length=6, unique=True, default=generate_friendly_id)
    profile = models.CharField(max_length=20, choices=PROFILE_CHOICES, default="og")
    orientation = models.CharField(max_length=10, choices=ORIENTATION_CHOICES, default="landscape")
    playlist = models.ForeignKey(
        Playlist, related_name="devices", null=True, blank=True, on_delete=models.SET_NULL
    )
    enabled = models.BooleanField(default=True)
    refresh_rate = models.PositiveIntegerField(default=900, help_text="Seconds between wake-ups.")
    playlist_position = models.PositiveIntegerField(default=0, editable=False)
    last_render = models.ForeignKey(
        "Render", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )

    # Telemetry the firmware sends with each /api/display request.
    battery_voltage = models.FloatField(null=True, blank=True, editable=False)
    rssi = models.IntegerField(null=True, blank=True, editable=False)
    firmware_version = models.CharField(max_length=20, blank=True, editable=False)
    reported_model = models.CharField(max_length=50, blank=True, editable=False)
    reported_refresh_rate = models.PositiveIntegerField(null=True, blank=True, editable=False)
    last_seen_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.friendly_id})"

    def clean(self):
        self.mac_address = normalize_mac(self.mac_address)

    def get_profile(self):
        return get_profile(self.profile)

    def is_online(self, now=None):
        """Checked in within two refresh cycles (plus a minute of slack)."""
        if self.last_seen_at is None:
            return False
        refresh = self.reported_refresh_rate or self.refresh_rate
        now = now or timezone.now()
        return now - self.last_seen_at <= timedelta(seconds=refresh * 2 + 60)


class Render(models.Model):
    """A rendered image of a dashboard for one device profile and orientation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dashboard = models.ForeignKey(Dashboard, related_name="renders", on_delete=models.CASCADE)
    profile = models.CharField(max_length=20, choices=PROFILE_CHOICES)
    orientation = models.CharField(max_length=10, choices=ORIENTATION_CHOICES, default="landscape")
    image = models.BinaryField()
    fingerprint = models.CharField(max_length=64)
    html = models.TextField(blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.dashboard} [{self.profile}/{self.orientation}] {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def extension(self):
        return get_profile(self.profile).image_format

    @property
    def content_type(self):
        return get_profile(self.profile).content_type

    @property
    def filename(self):
        return f"{self.fingerprint}.{self.extension}"

    @staticmethod
    def fingerprint_for(data):
        return hashlib.sha256(data).hexdigest()[:16]

    @classmethod
    def prune(cls, dashboard, profile, orientation):
        keep = conf.get("KEEP_RENDERS")
        stale = cls.objects.filter(
            dashboard=dashboard, profile=profile, orientation=orientation
        ).order_by("-created_at")[keep:]
        cls.objects.filter(pk__in=[render.pk for render in stale]).exclude(
            pk__in=Device.objects.exclude(last_render=None).values("last_render")
        ).delete()


class DeviceLog(models.Model):
    device = models.ForeignKey(Device, related_name="logs", on_delete=models.CASCADE)
    message = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.device} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


def normalize_mac(value):
    return (value or "").strip().upper().replace("-", ":")
