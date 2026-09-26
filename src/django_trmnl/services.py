"""Playlist rotation, data refreshes and the render loop."""

import logging

from django.db.models import F
from django.utils import timezone

from .models import Dashboard, Device, PluginInstance

logger = logging.getLogger(__name__)


def next_item(device, when=None):
    """Advance the device's playlist and return ``(item, render)``.

    Items without a render yet are skipped, so a device keeps showing real
    content while a new dashboard renders. Returns ``(item, None)`` when
    nothing active has been rendered, and ``(None, None)`` when nothing is
    active at all.
    """
    if device.playlist_id is None:
        return None, None
    items = device.playlist.active_items(when)
    if not items:
        return None, None
    profile = device.profile
    start = device.playlist_position % len(items)
    for offset in range(len(items)):
        index = (start + offset) % len(items)
        item = items[index]
        render = item.dashboard.latest_render(profile, device.orientation)
        if render:
            Device.objects.filter(pk=device.pk).update(playlist_position=index + 1)
            return item, render
    Device.objects.filter(pk=device.pk).update(playlist_position=F("playlist_position") + 1)
    return items[start], None


def refresh_instance(instance):
    """Fetch fresh data for a polling plugin. Errors are stored, not raised."""
    plugin = instance.get_plugin()
    instance.data_refreshed_at = timezone.now()
    try:
        instance.merge_variables = plugin.fetch(instance)
        instance.last_error = ""
    except Exception as error:
        logger.warning("Refreshing %s failed: %s", instance, error)
        instance.last_error = str(error)
        PluginInstance.objects.filter(pk=instance.pk).update(
            data_refreshed_at=instance.data_refreshed_at, last_error=instance.last_error
        )
        return False
    instance.save()
    return True


def render_targets():
    """Every (dashboard, profile, orientation) that an enabled device may show."""
    targets = set()
    devices = Device.objects.filter(enabled=True).exclude(playlist=None)
    for device in devices.select_related("playlist"):
        for item in device.playlist.items.filter(enabled=True):
            targets.add((item.dashboard_id, device.profile, device.orientation))
    return sorted(targets)


def run_once(renderer, force=False):
    """Refresh due plugin data, then render every stale target. Returns counts."""
    now = timezone.now()
    targets = render_targets()
    dashboard_ids = {dashboard_id for dashboard_id, _, _ in targets}
    refreshed = 0
    instances = PluginInstance.objects.filter(cells__dashboard_id__in=dashboard_ids).distinct()
    for instance in instances:
        if instance.refresh_due(now) and refresh_instance(instance):
            refreshed += 1

    rendered = 0
    failed = 0
    dashboards = Dashboard.objects.in_bulk(dashboard_ids)
    for dashboard_id, profile, orientation in targets:
        dashboard = dashboards[dashboard_id]
        if not force and not dashboard.needs_render(profile, orientation):
            continue
        try:
            renderer.render(dashboard, profile, orientation)
            rendered += 1
        except Exception:
            logger.exception("Rendering %s for %s/%s failed", dashboard, profile, orientation)
            failed += 1
    return {"targets": len(targets), "refreshed": refreshed, "rendered": rendered, "failed": failed}
