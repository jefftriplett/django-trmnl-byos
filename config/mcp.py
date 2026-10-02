"""MCP server for this project, built on django-mcpz, routed at ``/mcp``.

The tools live in the reusable app (``django_trmnl_byos.mcp_tools``); this module
owns the server and who may use it. Every request needs a credential - there is
no open mode:

- the shared ``MCP_AUTH_TOKEN``, sent as ``Authorization: Bearer <token>``, for
  scripts and developer tools that send a pasted credential;
- an OAuth access token from ``django_mcpz.oauth`` held by a staff account, for
  clients that sign the user in through ``/oauth/authorize`` (Claude Code,
  Claude.ai, ChatGPT).

An empty ``MCP_AUTH_TOKEN`` turns the shared token off; it never opens the endpoint.
"""

import secrets
from http import HTTPStatus

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django_mcpz.oauth.auth import oauth_auth
from django_mcpz.server import MCPServer

from django_trmnl_byos import __version__
from django_trmnl_byos.mcp_tools import register_tools


def mcp_auth(request: HttpRequest) -> HttpResponse | None:
    """Accept the shared ``MCP_AUTH_TOKEN``, or an OAuth access token held by staff."""
    token = settings.MCP_AUTH_TOKEN
    if token:
        presented = request.headers.get("Authorization", "")
        # Compare as bytes: secrets.compare_digest raises TypeError on non-ASCII str.
        if secrets.compare_digest(presented.encode(), f"Bearer {token}".encode()):
            return None

    challenge = oauth_auth(request)
    if challenge is not None:
        # The challenge's resource_metadata is how clients find the authorization
        # server, so every 401 carries it.
        response = JsonResponse({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
        response.headers["WWW-Authenticate"] = challenge.headers["WWW-Authenticate"]
        return response
    if not request.user.is_staff:
        return JsonResponse(
            {"error": "forbidden", "detail": "Only staff accounts may connect."},
            status=HTTPStatus.FORBIDDEN,
        )
    return None


server = MCPServer(
    name="django-trmnl-byos",
    title="TRMNL",
    version=__version__,
    instructions=(
        "Read and manage a TRMNL e-ink setup. list_devices shows each device's telemetry and the "
        "render it is showing; list_dashboards and list_playlists show what devices display. To "
        "change what a dashboard shows, find the plugin instance with list_plugin_instances and "
        "call update_plugin_data (the same as posting to its webhook); that queues a re-render. "
        "queue_render re-renders a dashboard on demand. Renders run on a background worker, so "
        "check list_dashboards for the new image."
    ),
    auth=mcp_auth,
)
register_tools(server)
