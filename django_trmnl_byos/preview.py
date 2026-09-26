"""Staff pages for previewing dashboards in a browser, with or without rendering."""

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .compose import compose
from .devices import PROFILES, get_profile
from .models import Dashboard, Device, Playlist, PlaylistItem, PluginInstance
from .tasks import enqueue_render


def selected(request):
    profile = get_profile(request.GET.get("profile") or request.POST.get("profile") or "og")
    orientation = request.GET.get("orientation") or request.POST.get("orientation") or "landscape"
    if orientation not in ("landscape", "portrait"):
        orientation = "landscape"
    return profile, orientation


@staff_member_required
def index(request):
    dashboards = list(Dashboard.objects.prefetch_related("cells__instance"))
    for board in dashboards:
        board.thumbnail = board.latest_render("og") or board.renders.first()
    devices = list(Device.objects.select_related("playlist", "last_render"))
    for item in devices:
        item.online = item.is_online()
    return render(
        request,
        "django_trmnl_byos/preview/index.html",
        {
            "dashboards": dashboards,
            "devices": devices,
            "instances": PluginInstance.objects.all(),
            "webhook_base": request.build_absolute_uri("/api/custom_plugins/"),
        },
    )


@staff_member_required
def device(request, pk):
    """Mirror of one device: what it shows now, what's next, and its health."""
    device = get_object_or_404(Device.objects.select_related("playlist", "last_render"), pk=pk)
    profile = device.get_profile()
    width, height = profile.css_size(device.orientation)
    now = timezone.localtime()
    items = []
    if device.playlist:
        playlist_items = device.playlist.items.select_related("dashboard")
        active = [item for item in playlist_items if item.is_active(now)]
        up_next = active[device.playlist_position % len(active)] if active else None
        for item in playlist_items:
            items.append(
                {
                    "item": item,
                    "active": item in active,
                    "next": item == up_next,
                    "render": item.dashboard.latest_render(device.profile, device.orientation),
                }
            )
    playlists = Playlist.objects.all()
    shared_with = []
    available = Dashboard.objects.all()
    if device.playlist:
        shared_with = list(device.playlist.devices.exclude(pk=device.pk))
        available = available.exclude(pk__in=device.playlist.items.values("dashboard"))
    next_wake = None
    if device.last_seen_at:
        refresh = device.reported_refresh_rate or device.refresh_rate
        next_wake = device.last_seen_at + timezone.timedelta(seconds=refresh)
    return render(
        request,
        "django_trmnl_byos/preview/device.html",
        {
            "device": device,
            "online": device.is_online(),
            "profile": profile,
            "width": width,
            "height": height,
            "items": items,
            "playlists": playlists,
            "shared_with": shared_with,
            "available": available,
            "log_entries": log_entries(device),
            "next_wake": next_wake,
            "server_url": request.build_absolute_uri("/").rstrip("/"),
        },
    )


def log_entries(device, limit=25):
    """Flatten the firmware's batched log posts into one row per entry, newest first."""
    entries = []
    for log in device.logs.all()[:20]:
        message = log.message if isinstance(log.message, dict) else {"message": str(log.message)}
        for entry in reversed(message.get("logs") or [message]):
            if not isinstance(entry, dict):
                entry = {"message": str(entry)}
            source = entry.get("source_path", "")
            if source and entry.get("source_line"):
                source = f"{source}:{entry['source_line']}"
            entries.append(
                {
                    "received": log.created_at,
                    "level": entry.get("level", ""),
                    "source": source,
                    "message": entry.get("message") or entry.get("raw") or "",
                    "wake_reason": entry.get("wake_reason", ""),
                    "raw": entry,
                }
            )
            if len(entries) >= limit:
                return entries
    return entries


@staff_member_required
def dashboard(request, pk):
    board = get_object_or_404(Dashboard, pk=pk)
    profile, orientation = selected(request)
    width, height = profile.css_size(orientation)
    image_width, image_height = profile.image_size(orientation)
    return render(
        request,
        "django_trmnl_byos/preview/dashboard.html",
        {
            "dashboard": board,
            "profile": profile,
            "profiles": PROFILES.values(),
            "profile_url": reverse("django_trmnl_byos:preview-dashboard", args=[board.pk]),
            "orientation": orientation,
            "width": width,
            "height": height,
            "image_width": image_width,
            "image_height": image_height,
            "scale": 1 / profile.pixel_ratio,
            "render": board.latest_render(profile.key, orientation),
            "html_url": reverse("django_trmnl_byos:preview-html", args=[board.pk])
            + f"?profile={profile.key}&orientation={orientation}",
        },
    )


@staff_member_required
@xframe_options_sameorigin
def dashboard_html(request, pk):
    board = get_object_or_404(Dashboard, pk=pk)
    profile, orientation = selected(request)
    return HttpResponse(compose(board, profile.key, orientation))


@staff_member_required
@require_POST
def dashboard_render(request, pk):
    from .rendering import render_dashboard

    board = get_object_or_404(Dashboard, pk=pk)
    profile, orientation = selected(request)
    try:
        result = render_dashboard(board, profile.key, orientation)
        messages.success(request, f"Rendered in {result.duration_ms} ms.")
    except Exception as error:
        messages.error(request, f"Render failed: {error}")
    url = reverse("django_trmnl_byos:preview-dashboard", args=[board.pk])
    return redirect(f"{url}?profile={profile.key}&orientation={orientation}")


def _items_in_order(playlist):
    return list(playlist.items.select_related("dashboard").order_by("order", "pk"))


@staff_member_required
@require_POST
def device_playlist(request, pk):
    """Edit a device's playlist from its page: pick the playlist, include/exclude,
    reorder, add or remove dashboards, render one, or choose what shows next."""
    device = get_object_or_404(Device.objects.select_related("playlist"), pk=pk)
    action = request.POST.get("action")
    back = redirect("django_trmnl_byos:preview-device", pk=device.pk)

    if action == "assign":
        playlist_id = request.POST.get("playlist")
        device.playlist = Playlist.objects.filter(pk=playlist_id).first() if playlist_id else None
        device.playlist_position = 0
        device.save(update_fields=["playlist", "playlist_position"])
        messages.success(request, f"{device.name} now uses {device.playlist or 'no playlist'}.")
        return back

    if action == "create":
        playlist = Playlist.objects.create(name=request.POST.get("name") or f"{device.name} playlist")
        if device.playlist:
            for item in _items_in_order(device.playlist):
                item.pk = None
                item.playlist = playlist
                item.save()
        device.playlist = playlist
        device.playlist_position = 0
        device.save(update_fields=["playlist", "playlist_position"])
        messages.success(request, f"Created {playlist} for {device.name} only.")
        return back

    playlist = device.playlist
    if playlist is None:
        messages.error(request, "Pick or create a playlist first.")
        return back

    if action == "add":
        dashboard = get_object_or_404(Dashboard, pk=request.POST.get("dashboard"))
        last = playlist.items.order_by("-order").first()
        PlaylistItem.objects.create(playlist=playlist, dashboard=dashboard, order=(last.order + 1) if last else 0)
        enqueue_render([dashboard.pk])
        messages.success(request, f"Added {dashboard} to {playlist}.")
        return back

    item = get_object_or_404(PlaylistItem, pk=request.POST.get("item"), playlist=playlist)

    if action == "toggle":
        item.enabled = not item.enabled
        item.save(update_fields=["enabled"])
        messages.success(request, f"{item.dashboard} is {'included' if item.enabled else 'excluded'}.")
    elif action == "remove":
        item.delete()
        messages.success(request, f"Removed {item.dashboard} from {playlist}.")
    elif action in ("up", "down"):
        items = _items_in_order(playlist)
        index = next(i for i, other in enumerate(items) if other.pk == item.pk)
        swap = index - 1 if action == "up" else index + 1
        if 0 <= swap < len(items):
            items[index], items[swap] = items[swap], items[index]
            for order, other in enumerate(items):
                if other.order != order:
                    other.order = order
                    other.save(update_fields=["order"])
    elif action == "render":
        enqueue_render([item.dashboard_id])
        messages.success(request, f"Rendering {item.dashboard}. It updates here in a few seconds.")
    elif action == "show_next":
        if not item.enabled:
            item.enabled = True
            item.save(update_fields=["enabled"])
        active = playlist.active_items()
        if item.pk not in [other.pk for other in active]:
            messages.error(request, f"{item.dashboard} is outside its scheduled time right now.")
            return back
        device.playlist_position = [other.pk for other in active].index(item.pk)
        device.save(update_fields=["playlist_position"])
        if item.dashboard.latest_render(device.profile, device.orientation) is None:
            enqueue_render([item.dashboard_id])
        messages.success(
            request,
            f"{item.dashboard} shows next: at the device's next check-in, or now if you press its button.",
        )
    else:
        messages.error(request, "Unknown action.")
    return back
