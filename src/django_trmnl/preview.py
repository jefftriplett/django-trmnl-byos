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
from .models import Dashboard, Device, PluginInstance


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
        "django_trmnl/preview/index.html",
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
    next_wake = None
    if device.last_seen_at:
        refresh = device.reported_refresh_rate or device.refresh_rate
        next_wake = device.last_seen_at + timezone.timedelta(seconds=refresh)
    return render(
        request,
        "django_trmnl/preview/device.html",
        {
            "device": device,
            "online": device.is_online(),
            "profile": profile,
            "width": width,
            "height": height,
            "items": items,
            "logs": device.logs.all()[:10],
            "next_wake": next_wake,
            "server_url": request.build_absolute_uri("/").rstrip("/"),
        },
    )


@staff_member_required
def dashboard(request, pk):
    board = get_object_or_404(Dashboard, pk=pk)
    profile, orientation = selected(request)
    width, height = profile.css_size(orientation)
    image_width, image_height = profile.image_size(orientation)
    return render(
        request,
        "django_trmnl/preview/dashboard.html",
        {
            "dashboard": board,
            "profile": profile,
            "profiles": PROFILES.values(),
            "profile_url": reverse("django_trmnl:preview-dashboard", args=[board.pk]),
            "orientation": orientation,
            "width": width,
            "height": height,
            "image_width": image_width,
            "image_height": image_height,
            "scale": 1 / profile.pixel_ratio,
            "render": board.latest_render(profile.key, orientation),
            "html_url": reverse("django_trmnl:preview-html", args=[board.pk])
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
    url = reverse("django_trmnl:preview-dashboard", args=[board.pk])
    return redirect(f"{url}?profile={profile.key}&orientation={orientation}")
