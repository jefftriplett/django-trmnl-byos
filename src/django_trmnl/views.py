"""The device API (what TRMNL firmware calls), images, and the plugin webhook."""

import json
import logging

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from . import conf, images
from .devices import MODEL_ALIASES
from .models import Device, DeviceLog, Playlist, PluginInstance, Render, normalize_mac
from .services import next_item
from .tasks import dashboards_for_instance, enqueue_render

logger = logging.getLogger(__name__)

PLACEHOLDERS = {
    "welcome": ("Hello!", ["Device {friendly_id} is connected.", "Give it a playlist in the admin."]),
    "empty": ("Nothing scheduled", ["Device {friendly_id} has no active playlist items", "right now."]),
    "rendering": ("Rendering…", ["Your dashboard is being rendered.", "Run: manage.py qcluster"]),
    "disabled": ("Device disabled", ["Device {friendly_id} is disabled in the admin."]),
}


def absolute_url(request, path):
    base = conf.get("BASE_URL")
    if base:
        return base.rstrip("/") + path
    return request.build_absolute_uri(path)


def header_int(request, name):
    try:
        return int(float(request.headers[name]))
    except (KeyError, ValueError):
        return None


def header_float(request, name):
    try:
        return float(request.headers[name])
    except (KeyError, ValueError):
        return None


@require_GET
def setup(request):
    """Swap a MAC address for an API key and friendly ID."""
    not_found = {"status": 404, "api_key": None, "friendly_id": None, "image_url": None, "filename": None}
    mac = normalize_mac(request.headers.get("ID"))
    if not mac:
        return JsonResponse({**not_found, "message": "ID header is required."})

    device = Device.objects.filter(mac_address=mac).first()
    if device is None:
        if not conf.get("AUTO_PROVISION"):
            return JsonResponse({**not_found, "message": "Unknown device."})
        model = request.headers.get("Model", "").strip().lower()
        device = Device.objects.create(
            mac_address=mac,
            name=f"TRMNL {mac[-5:]}",
            profile=MODEL_ALIASES.get(model, conf.get("DEFAULT_PROFILE")),
            refresh_rate=conf.get("DEFAULT_REFRESH_RATE"),
            playlist=Playlist.objects.filter(is_default=True).first(),
            reported_model=model,
        )
        logger.info("Provisioned %s", device)

    return JsonResponse(
        {
            "status": 200,
            "api_key": device.api_key,
            "friendly_id": device.friendly_id,
            "image_url": placeholder_url(request, device, "welcome"),
            "filename": "welcome",
            "message": f"Device {device.friendly_id} added.",
        }
    )


def authenticate(request):
    token = request.headers.get("Access-Token")
    if not token:
        return None
    device = Device.objects.select_related("playlist").filter(api_key=token).first()
    mac = normalize_mac(request.headers.get("ID"))
    if device and mac and mac != device.mac_address:
        return None
    return device


def adopt(request):
    """Take in a device that was set up elsewhere (e.g. trmnl.com) and kept its API key.

    Switching a TRMNL to a custom server doesn't clear its stored key, so it
    skips /api/setup and calls /api/display with a token we've never seen. With
    AUTO_PROVISION on, a *new* MAC address is registered with that token. A
    known MAC presenting a different token is still refused.
    """
    token = request.headers.get("Access-Token", "")
    mac = normalize_mac(request.headers.get("ID"))
    if not conf.get("AUTO_PROVISION") or not token or not mac or len(token) > 64:
        return None
    if Device.objects.filter(mac_address=mac).exists() or Device.objects.filter(api_key=token).exists():
        return None
    model = request.headers.get("Model", "").strip().lower()
    device = Device.objects.create(
        mac_address=mac,
        api_key=token,
        name=f"TRMNL {mac[-5:]}",
        profile=MODEL_ALIASES.get(model, conf.get("DEFAULT_PROFILE")),
        refresh_rate=conf.get("DEFAULT_REFRESH_RATE"),
        playlist=Playlist.objects.filter(is_default=True).first(),
        reported_model=model,
    )
    logger.info("Adopted %s with its existing API key", device)
    return device


def rekey(request):
    """Accept a new API key from a known MAC that has never displayed a screen.

    Between /api/setup and its first /api/display, a device can end up holding a
    different key than the one we issued (a portal reset, older firmware, a
    retried setup). Until it has checked in successfully, trust the key it sends.
    """
    token = request.headers.get("Access-Token", "")
    mac = normalize_mac(request.headers.get("ID"))
    if not token or not mac or len(token) > 64 or Device.objects.filter(api_key=token).exists():
        return None
    device = Device.objects.filter(mac_address=mac, last_seen_at=None).first()
    if device is None:
        return None
    device.api_key = token
    device.save(update_fields=["api_key"])
    logger.info("Re-keyed %s to the API key it presented", device)
    return device


def record_telemetry(request, device):
    device.last_seen_at = timezone.now()
    device.battery_voltage = header_float(request, "Battery-Voltage")
    device.rssi = header_int(request, "RSSI")
    device.firmware_version = request.headers.get("FW-Version", "")[:20]
    device.reported_refresh_rate = header_int(request, "Refresh-Rate")
    if request.headers.get("Model"):
        device.reported_model = request.headers["Model"][:50]
    logger.info(
        "Display from %s: model=%s size=%sx%s fw=%s source=%s cached=%s",
        device.friendly_id,
        request.headers.get("Model", "?"),
        request.headers.get("Width", "?"),
        request.headers.get("Height", "?"),
        request.headers.get("FW-Version", "?"),
        request.headers.get("Update-Source", "?"),
        request.headers.get("Image-Cached", "?"),
    )
    Device.objects.filter(pk=device.pk).update(
        last_seen_at=device.last_seen_at,
        battery_voltage=device.battery_voltage,
        rssi=device.rssi,
        firmware_version=device.firmware_version,
        reported_refresh_rate=device.reported_refresh_rate,
        reported_model=device.reported_model,
    )


def display_response(image_url, filename, refresh_rate):
    return JsonResponse(
        {
            "status": 0,
            "image_url": image_url,
            "filename": filename,
            # An integer: the firmware and other clients decode it as a number (a string can parse as 0).
            "refresh_rate": int(refresh_rate),
            "reset_firmware": False,
            "update_firmware": False,
            "firmware_url": None,
            "special_function": "none",
        }
    )


@require_GET
def display(request):
    """Advance the device's playlist and point it at the next image."""
    device = authenticate(request) or adopt(request) or rekey(request)
    if device is None:
        token = request.headers.get("Access-Token", "")
        logger.warning(
            "Display refused: ID=%r token=%s… (%d chars) headers=%s",
            request.headers.get("ID"),
            token[:4],
            len(token),
            sorted(key for key in request.headers if key.lower() not in {"cookie", "authorization"}),
        )
        # Never answer 500 here: TRMNL firmware treats status 500 as "reset", which
        # wipes the device's WiFi, API key *and custom server URL*, sending it back to
        # trmnl.app. 202 ("not registered") just shows the device ID and polls again.
        return JsonResponse(
            {
                "status": 202,
                "image_url": None,
                "filename": None,
                "refresh_rate": 60,
                "reset_firmware": False,
                "update_firmware": False,
                "firmware_url": None,
                "special_function": "none",
                "message": "Device not recognised. Check the django-trmnl admin.",
            }
        )
    record_telemetry(request, device)

    if not device.enabled:
        return display_placeholder(request, device, "disabled")
    if device.playlist_id is None:
        return display_placeholder(request, device, "welcome")

    item, render = next_item(device)
    if item is None:
        return display_placeholder(request, device, "empty")
    if render is None and conf.get("RENDER_INLINE"):
        from .rendering import render_dashboard

        render = render_dashboard(item.dashboard, device.profile, device.orientation)
    if render is None:
        enqueue_render([item.dashboard_id])
        return display_placeholder(request, device, "rendering", refresh_rate=60)

    Device.objects.filter(pk=device.pk).update(last_render=render)
    return display_response(
        absolute_url(request, reverse("django_trmnl:image", args=[render.pk, render.extension])),
        render.filename,
        item.refresh_rate or device.refresh_rate,
    )


@require_GET
def current_screen(request):
    """What the device is showing right now, without advancing its playlist.

    For mirrors and BYOD clients (Kindle, Kobo, a browser extension). Matches
    trmnl.com's ``/api/current_screen`` and ``/api/display/current``. It doesn't
    record telemetry: the caller isn't necessarily the device.
    """
    device = authenticate(request)
    if device is None:
        return JsonResponse({"status": 404, "error": "Device not found"})
    render = device.last_render
    if render is None:
        image_url = placeholder_url(request, device, "welcome" if device.playlist_id is None else "rendering")
        filename = None
        rendered_at = None
    else:
        image_url = absolute_url(request, reverse("django_trmnl:image", args=[render.pk, render.extension]))
        filename = render.filename
        rendered_at = render.created_at.isoformat()
    return JsonResponse(
        {
            "status": 200,
            "refresh_rate": int(device.refresh_rate),
            "image_url": image_url,
            "filename": filename,
            "rendered_at": rendered_at,
        }
    )


def placeholder_url(request, device, state):
    profile = device.get_profile()
    path = reverse("django_trmnl:placeholder", args=[device.friendly_id, state, profile.image_format])
    return absolute_url(request, path)


def display_placeholder(request, device, state, refresh_rate=None):
    return display_response(
        placeholder_url(request, device, state),
        f"placeholder-{state}-{device.profile}",
        refresh_rate or device.refresh_rate,
    )


@csrf_exempt
@require_http_methods(["POST"])
def log(request):
    """Store diagnostics the firmware posts when something goes wrong."""
    device = authenticate(request)
    if device is None:
        return JsonResponse({"status": 404, "message": "Device not found"}, status=404)
    body = request.body.decode("utf-8", errors="replace")
    try:
        message = json.loads(body)
    except json.JSONDecodeError:
        message = {"raw": body}
    DeviceLog.objects.create(device=device, message=message)
    return JsonResponse({"status": 200, "message": "Log received"})


@require_GET
def image(request, pk, extension):
    render = get_object_or_404(Render, pk=pk)
    if extension != render.extension:
        raise Http404
    response = HttpResponse(bytes(render.image), content_type=render.content_type)
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@require_GET
def placeholder(request, friendly_id, state, extension):
    device = get_object_or_404(Device, friendly_id=friendly_id)
    if state not in PLACEHOLDERS:
        raise Http404
    profile = device.get_profile()
    if extension != profile.image_format:
        raise Http404
    heading, lines = PLACEHOLDERS[state]
    data = images.placeholder(
        profile, device.orientation, heading, [line.format(friendly_id=friendly_id) for line in lines]
    )
    return HttpResponse(data, content_type=profile.content_type)


def deep_merge(existing, incoming):
    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def stream_merge(existing, incoming, limit):
    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, list) and isinstance(merged.get(key), list):
            value = merged[key] + value
        if isinstance(value, list) and limit:
            value = value[-limit:]
        merged[key] = value
    return merged


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request, uuid):
    """TRMNL-compatible private plugin webhook: POST merge_variables, GET them back."""
    instance = get_object_or_404(PluginInstance, uuid=uuid)
    if request.method == "GET":
        return JsonResponse({"merge_variables": instance.merge_variables})

    if len(request.body) > conf.get("WEBHOOK_MAX_BYTES"):
        return JsonResponse({"error": "Payload too large"}, status=413)
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Body must be JSON"}, status=400)
    variables = payload.get("merge_variables") if isinstance(payload, dict) else None
    if not isinstance(variables, dict):
        return JsonResponse({"error": "Send an object under merge_variables"}, status=400)

    strategy = payload.get("merge_strategy", "replace")
    if strategy == "deep_merge":
        instance.merge_variables = deep_merge(instance.merge_variables or {}, variables)
    elif strategy == "stream":
        try:
            limit = int(payload.get("stream_limit") or 0)
        except (TypeError, ValueError):
            return JsonResponse({"error": "stream_limit must be a number"}, status=400)
        instance.merge_variables = stream_merge(instance.merge_variables or {}, variables, limit)
    elif strategy == "replace":
        instance.merge_variables = variables
    else:
        return JsonResponse({"error": f"Unknown merge_strategy {strategy!r}"}, status=400)

    if len(json.dumps(instance.merge_variables)) > conf.get("WEBHOOK_MAX_BYTES"):
        return JsonResponse({"error": "Merged payload too large"}, status=413)
    instance.save(update_fields=["merge_variables", "updated_at"])
    enqueue_render(dashboards_for_instance(instance))
    return JsonResponse({"message": "Updated", "merge_variables": instance.merge_variables})

