import json

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from . import layouts
from .models import Dashboard, DashboardCell, Device, DeviceLog, Playlist, PlaylistItem, PluginInstance, Render
from .plugins import registry
from .services import refresh_instance
from .tasks import dashboards_for_instance, enqueue_render


def image_tag(render, width=400):
    if not render:
        return "—"
    url = reverse("django_trmnl_byos:image", args=[render.pk, render.extension])
    return format_html('<img src="{}" width="{}" style="border:1px solid #333" alt="">', url, width)


class PluginInstanceForm(forms.ModelForm):
    plugin = forms.ChoiceField(choices=registry.choices)

    class Meta:
        model = PluginInstance
        fields = ["name", "plugin", "settings", "merge_variables", "refresh_interval"]


@admin.register(PluginInstance)
class PluginInstanceAdmin(admin.ModelAdmin):
    form = PluginInstanceForm
    list_display = ["name", "plugin", "refresh_interval", "data_refreshed_at", "has_error"]
    list_filter = ["plugin"]
    search_fields = ["name"]
    readonly_fields = ["webhook", "plugin_help", "data_refreshed_at", "last_error", "updated_at"]
    actions = ["refresh_now"]

    @admin.display(boolean=True, description="Error")
    def has_error(self, obj):
        return bool(obj.last_error)

    @admin.display(description="Webhook URL")
    def webhook(self, obj):
        if not obj.pk:
            return "Save first to get a webhook URL."
        return format_html("<code>/api/custom_plugins/{}</code>", obj.uuid)

    @admin.display(description="Default settings")
    def plugin_help(self, obj):
        if not obj.pk or obj.plugin not in registry:
            return "Pick a plugin and save; its default settings will be filled in."
        plugin = obj.get_plugin()
        rows = format_html_join(
            "",
            "<tr><td><code>{}</code></td><td><code>{}</code></td><td>{}</td></tr>",
            (
                (key, json.dumps(value), plugin.help.get(key, ""))
                for key, value in plugin.default_settings.items()
            ),
        )
        return format_html(
            "<p>{}</p><table><tr><th>Setting</th><th>Default</th><th>Meaning</th></tr>{}</table>",
            plugin.description,
            rows,
        )

    def save_model(self, request, obj, form, change):
        if not obj.settings and obj.plugin in registry:
            obj.settings = dict(registry.get(obj.plugin).default_settings)
        super().save_model(request, obj, form, change)
        enqueue_render(dashboards_for_instance(obj))

    @admin.action(description="Refresh data now (polling plugins)")
    def refresh_now(self, request, queryset):
        for instance in queryset:
            if not instance.get_plugin().polls:
                continue
            if refresh_instance(instance):
                self.message_user(request, f"Refreshed {instance}.")
            else:
                self.message_user(request, f"{instance}: {instance.last_error}", messages.ERROR)


class CellFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        layout = self.data.get("layout") or self.instance.layout
        if layout != layouts.FLUID:
            return
        placements = []
        for form in self.forms:
            data = getattr(form, "cleaned_data", None)
            if not data or data.get("DELETE") or not data.get("instance"):
                continue
            placement = layouts.Placement(data["col"], data["row"], data["col_span"], data["row_span"])
            errors = layouts.placement_errors(placement)
            if errors:
                raise ValidationError(f"{data['instance']}: " + "; ".join(errors))
            placements.append((str(data["instance"]), placement))
        errors = layouts.overlap_errors(placements)
        if errors:
            raise ValidationError(errors)


class DashboardCellInline(admin.TabularInline):
    model = DashboardCell
    formset = CellFormSet
    extra = 1
    fields = ["instance", "position", "col", "row", "col_span", "row_span", "show_title_bar"]


@admin.register(Dashboard)
class DashboardAdmin(admin.ModelAdmin):
    list_display = ["name", "layout", "cell_count", "preview", "updated_at"]
    list_filter = ["layout"]
    inlines = [DashboardCellInline]
    fieldsets = [
        (None, {"fields": ["name", "layout"]}),
        ("Screen", {"fields": ["backdrop", "dark_mode", "no_bleed", "extra_screen_classes"]}),
    ]

    @admin.display(description="Cells")
    def cell_count(self, obj):
        return obj.cells.count()

    @admin.display(description="Preview")
    def preview(self, obj):
        url = reverse("django_trmnl_byos:preview-dashboard", args=[obj.pk])
        return format_html('<a href="{}">Preview</a>', url)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.touch()
        enqueue_render([form.instance.pk])


class PlaylistItemInline(admin.TabularInline):
    model = PlaylistItem
    extra = 1
    fields = ["dashboard", "order", "enabled", "refresh_rate", "start_time", "end_time", "weekdays"]


@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    list_display = ["name", "is_default"]
    inlines = [PlaylistItemInline]


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "friendly_id",
        "profile",
        "playlist",
        "enabled",
        "last_seen_at",
        "battery_voltage",
        "rssi",
        "firmware_version",
    ]
    list_editable = ["playlist", "enabled"]
    list_filter = ["profile", "enabled", "playlist"]
    search_fields = ["name", "friendly_id", "mac_address"]
    readonly_fields = [
        "api_key",
        "friendly_id",
        "showing",
        "last_seen_at",
        "battery_voltage",
        "rssi",
        "firmware_version",
        "reported_model",
        "reported_refresh_rate",
        "created_at",
    ]
    fieldsets = [
        (None, {"fields": ["name", "mac_address", "enabled", "playlist"]}),
        ("Display", {"fields": ["profile", "orientation", "refresh_rate", "showing"]}),
        ("Credentials", {"fields": ["api_key", "friendly_id"]}),
        (
            "Telemetry",
            {
                "fields": [
                    "last_seen_at",
                    "battery_voltage",
                    "rssi",
                    "firmware_version",
                    "reported_model",
                    "reported_refresh_rate",
                    "created_at",
                ]
            },
        ),
    ]

    @admin.display(description="Showing now")
    def showing(self, obj):
        return image_tag(obj.last_render)


@admin.register(Render)
class RenderAdmin(admin.ModelAdmin):
    list_display = ["dashboard", "profile", "orientation", "created_at", "duration_ms", "fingerprint"]
    list_filter = ["profile", "orientation", "dashboard"]
    fields = ["dashboard", "profile", "orientation", "created_at", "duration_ms", "fingerprint", "picture", "html"]
    readonly_fields = fields

    @admin.display(description="Image")
    def picture(self, obj):
        return image_tag(obj, width=800)

    def has_add_permission(self, request):
        return False


@admin.register(DeviceLog)
class DeviceLogAdmin(admin.ModelAdmin):
    list_display = ["device", "created_at"]
    list_filter = ["device"]
    readonly_fields = ["device", "message", "created_at"]

    def has_add_permission(self, request):
        return False
