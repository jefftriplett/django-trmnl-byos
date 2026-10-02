"""MCP tools for reading and managing a TRMNL setup, for django-mcpz servers.

The host project owns the server and its auth; this module only adds tools::

    from django_mcpz.server import MCPServer
    from django_trmnl_byos.mcp_tools import register_tools

    server = MCPServer(name=..., version=..., auth=...)
    register_tools(server)

Requires ``django-mcpz`` (and its ``msgspec``). Updating plugin data goes through
``services.update_merge_variables``, the same code as the webhook at
``/api/custom_plugins/<uuid>``, so both paths merge, validate and queue renders
identically.
"""

import uuid
from typing import Annotated, Any, Literal

import msgspec
from django.db.models import Q
from django.http import HttpRequest
from django.urls import reverse
from django_mcpz.server import ToolError

from . import conf
from .models import Dashboard, Device, Playlist, PluginInstance, Render
from .services import MergeError, render_targets, update_merge_variables
from .tasks import dashboards_for_instance, enqueue_render

# Must list services.MERGE_STRATEGIES; a test checks they match.
MergeStrategy = Literal["replace", "deep_merge", "stream"]

InstanceRef = Annotated[
    str, msgspec.Meta(description="A plugin instance's UUID (from list_plugin_instances) or its exact name.")
]


def _iso(value):
    return value.isoformat() if value else None


def _image_url(request: HttpRequest, render: Render) -> str:
    """Absolute URL of a render, as the device API serves it (honours TRMNL_BASE_URL)."""
    path = reverse("django_trmnl_byos:image", args=[render.pk, render.extension])
    base = conf.get("BASE_URL")
    return base.rstrip("/") + path if base else request.build_absolute_uri(path)


def _render_payload(request: HttpRequest, render: Render | None) -> dict | None:
    if render is None:
        return None
    return {
        "dashboard": render.dashboard.name,
        "dashboard_id": render.dashboard_id,
        "profile": render.profile,
        "orientation": render.orientation,
        "image_url": _image_url(request, render),
        "rendered_at": _iso(render.created_at),
        "duration_ms": render.duration_ms,
    }


def _get_instance(ref: str) -> PluginInstance:
    ref = ref.strip()
    query = Q(name=ref)
    try:
        query |= Q(uuid=uuid.UUID(ref))
    except ValueError:
        pass
    matches = list(PluginInstance.objects.filter(query)[:2])
    if not matches:
        raise ToolError(f"No plugin instance {ref!r}. Call list_plugin_instances to see them.")
    if len(matches) > 1:
        raise ToolError(f"More than one plugin instance is named {ref!r}; use its UUID.")
    return matches[0]


class InstanceParams(msgspec.Struct, kw_only=True):
    instance: InstanceRef


class UpdatePluginDataParams(msgspec.Struct, kw_only=True):
    instance: InstanceRef
    merge_variables: Annotated[
        dict[str, Any], msgspec.Meta(description="The data the plugin's template renders, as a JSON object.")
    ]
    merge_strategy: Annotated[
        MergeStrategy,
        msgspec.Meta(
            description=(
                "replace swaps the data; deep_merge merges nested objects into it; stream appends "
                "to lists, keeping the last stream_limit items."
            )
        ),
    ] = "replace"
    stream_limit: Annotated[
        int | None, msgspec.Meta(description="With merge_strategy stream: how many list items to keep.")
    ] = None


class DashboardParams(msgspec.Struct, kw_only=True):
    dashboard_id: Annotated[int, msgspec.Meta(description="The dashboard's id, from list_dashboards.")]


def register_tools(server) -> None:
    """Register the TRMNL tools on a django-mcpz ``server``."""

    @server.tool(
        description=(
            "List TRMNL devices: profile, orientation, playlist, whether each is online, its latest "
            "telemetry (battery voltage, Wi-Fi RSSI, firmware, reported model and refresh rate) and "
            "the render it is currently showing, with the image URL."
        ),
        read_only=True,
    )
    def list_devices(request: HttpRequest) -> dict:
        devices = Device.objects.select_related("playlist", "last_render__dashboard").order_by("name")
        return {
            "devices": [
                {
                    "id": device.pk,
                    "name": device.name,
                    "friendly_id": device.friendly_id,
                    "enabled": device.enabled,
                    "online": device.is_online(),
                    "profile": device.profile,
                    "orientation": device.orientation,
                    "playlist": device.playlist.name if device.playlist else None,
                    "refresh_rate": device.refresh_rate,
                    "telemetry": {
                        "battery_voltage": device.battery_voltage,
                        "rssi": device.rssi,
                        "firmware_version": device.firmware_version or None,
                        "reported_model": device.reported_model or None,
                        "reported_refresh_rate": device.reported_refresh_rate,
                        "last_seen_at": _iso(device.last_seen_at),
                    },
                    "current_render": _render_payload(request, device.last_render),
                }
                for device in devices
            ]
        }

    @server.tool(
        description=(
            "List dashboards: layout, the plugin instance in each cell, and the latest render for "
            "every device profile and orientation that shows it."
        ),
        read_only=True,
    )
    def list_dashboards(request: HttpRequest) -> dict:
        targets = render_targets()
        dashboards = Dashboard.objects.prefetch_related("cells__instance").order_by("name")
        results = []
        for dashboard in dashboards:
            renders = []
            for dashboard_id, profile, orientation in targets:
                if dashboard_id == dashboard.pk:
                    renders.append(
                        {
                            "profile": profile,
                            "orientation": orientation,
                            "latest": _render_payload(request, dashboard.latest_render(profile, orientation)),
                            "stale": dashboard.needs_render(profile, orientation),
                        }
                    )
            results.append(
                {
                    "id": dashboard.pk,
                    "name": dashboard.name,
                    "layout": dashboard.layout,
                    "cells": [
                        {
                            "instance": cell.instance.name,
                            "instance_uuid": str(cell.instance.uuid),
                            "plugin": cell.instance.plugin,
                            "view_size": cell.view_size(),
                        }
                        for cell in dashboard.ordered_cells()
                    ],
                    "renders": renders,
                }
            )
        return {"dashboards": results}

    @server.tool(
        description=(
            "List playlists and their items in order: dashboard, enabled, refresh rate, and the "
            "time window and weekdays each item is limited to."
        ),
        read_only=True,
    )
    def list_playlists(request: HttpRequest) -> dict:
        playlists = Playlist.objects.prefetch_related("items__dashboard", "devices")
        return {
            "playlists": [
                {
                    "id": playlist.pk,
                    "name": playlist.name,
                    "is_default": playlist.is_default,
                    "devices": [device.name for device in playlist.devices.all()],
                    "items": [
                        {
                            "dashboard": item.dashboard.name,
                            "dashboard_id": item.dashboard_id,
                            "order": item.order,
                            "enabled": item.enabled,
                            "refresh_rate": item.refresh_rate,
                            "start_time": item.start_time.strftime("%H:%M") if item.start_time else None,
                            "end_time": item.end_time.strftime("%H:%M") if item.end_time else None,
                            "weekdays": item.weekdays or None,
                        }
                        for item in sorted(playlist.items.all(), key=lambda item: item.order)
                    ],
                }
                for playlist in playlists
            ]
        }

    @server.tool(
        description=(
            "List plugin instances: name, UUID, plugin, refresh interval, when data was last refreshed, "
            "the last error, and which dashboards use each."
        ),
        read_only=True,
    )
    def list_plugin_instances(request: HttpRequest) -> dict:
        instances = PluginInstance.objects.prefetch_related("cells__dashboard").order_by("name")
        return {
            "plugin_instances": [
                {
                    "name": instance.name,
                    "uuid": str(instance.uuid),
                    "plugin": instance.plugin,
                    "refresh_interval": instance.refresh_interval,
                    "data_refreshed_at": _iso(instance.data_refreshed_at),
                    "last_error": instance.last_error or None,
                    "dashboards": sorted({cell.dashboard.name for cell in instance.cells.all()}),
                }
                for instance in instances
            ]
        }

    @server.tool(
        description="Get one plugin instance's settings and its current merge_variables (the data it renders).",
        read_only=True,
    )
    def get_plugin_instance(request: HttpRequest, params: InstanceParams) -> dict:
        instance = _get_instance(params.instance)
        return {
            "name": instance.name,
            "uuid": str(instance.uuid),
            "plugin": instance.plugin,
            "settings": instance.settings,
            "merge_variables": instance.merge_variables,
            "updated_at": _iso(instance.updated_at),
        }

    @server.tool(
        description=(
            "Update a plugin instance's data (merge_variables), exactly like a POST to its webhook at "
            "/api/custom_plugins/<uuid>, then queue renders of the dashboards that show it."
        ),
        read_only=False,
        destructive=True,
    )
    def update_plugin_data(request: HttpRequest, params: UpdatePluginDataParams) -> dict:
        instance = _get_instance(params.instance)
        try:
            update_merge_variables(instance, params.merge_variables, params.merge_strategy, params.stream_limit)
        except MergeError as error:
            raise ToolError(str(error)) from error
        return {
            "name": instance.name,
            "uuid": str(instance.uuid),
            "merge_variables": instance.merge_variables,
            "queued_dashboards": sorted(set(dashboards_for_instance(instance))),
        }

    @server.tool(
        description=(
            "Queue a render of a dashboard for every device profile that shows it. Renders run on the "
            "django-q2 worker; check list_dashboards for the new image."
        ),
        read_only=False,
        destructive=False,
        idempotent=True,
    )
    def queue_render(request: HttpRequest, params: DashboardParams) -> dict:
        dashboard = Dashboard.objects.filter(pk=params.dashboard_id).first()
        if dashboard is None:
            raise ToolError(f"No dashboard {params.dashboard_id}. Call list_dashboards to see them.")
        targets = [target for target in render_targets() if target[0] == dashboard.pk]
        enqueue_render([dashboard.pk])
        return {
            "dashboard": dashboard.name,
            "dashboard_id": dashboard.pk,
            "targets": [{"profile": profile, "orientation": orientation} for _, profile, orientation in targets],
            "queued": bool(targets),
            "note": None if targets else "No enabled device shows this dashboard, so there is nothing to render.",
        }
