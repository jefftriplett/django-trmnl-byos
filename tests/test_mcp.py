"""The MCP server at /mcp: who may connect, and what the TRMNL tools do."""

import datetime as dt
import json
import typing

import pytest
from django.urls import Resolver404, resolve
from django_mcpz.oauth.models import AccessToken, Client

from django_trmnl_byos import mcp_tools
from django_trmnl_byos.models import PluginInstance
from django_trmnl_byos.services import MERGE_STRATEGIES

from .conftest import fake_render

pytestmark = pytest.mark.django_db

PROTOCOL_VERSION = "2026-07-28"
TOKEN = "sekret"
TOOLS = {
    "list_devices",
    "list_dashboards",
    "list_playlists",
    "list_plugin_instances",
    "get_plugin_instance",
    "update_plugin_data",
    "queue_render",
}


@pytest.fixture(autouse=True)
def shared_token(settings):
    settings.MCP_AUTH_TOKEN = TOKEN


@pytest.fixture
def queued(monkeypatch):
    """Record enqueue_render calls instead of queueing django-q2 tasks."""
    calls = []
    monkeypatch.setattr("django_trmnl_byos.tasks.enqueue_render", lambda ids: calls.append(sorted(set(ids))))
    monkeypatch.setattr(mcp_tools, "enqueue_render", lambda ids: calls.append(sorted(set(ids))))
    return calls


def mcp_post(client, method, params=None, *, token=TOKEN, path="/mcp"):
    params = dict(params or {})
    params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers = {"MCP-Protocol-Version": PROTOCOL_VERSION, "Mcp-Method": method}
    if "name" in params:
        headers["Mcp-Name"] = params["name"]
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        path,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
        content_type="application/json",
        headers=headers,
    )


def call(client, name, arguments=None):
    response = mcp_post(client, "tools/call", {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


def call_error(client, name, arguments=None):
    result = mcp_post(client, "tools/call", {"name": name, "arguments": arguments or {}}).json()["result"]
    assert result["isError"] is True, result
    return result["content"][0]["text"]


def oauth_token(user):
    client = Client.objects.create(client_id="test", kind=Client.Kind.REGISTERED)
    _, value = AccessToken.create(
        lifetime=dt.timedelta(hours=1), client=client, user=user, resource="http://testserver/mcp"
    )
    return value


# --- routing and auth ------------------------------------------------------------


def test_served_at_mcp_without_a_trailing_slash(client):
    assert resolve("/mcp").url_name == "mcp"
    response = mcp_post(client, "tools/list")
    assert response.status_code == 200
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == TOOLS


def test_the_trailing_slash_is_not_the_mcp_server(client):
    try:
        assert resolve("/mcp/").url_name != "mcp"
    except Resolver404:
        pass
    assert mcp_post(client, "tools/list", path="/mcp/").status_code != 200


@pytest.mark.parametrize("token", [None, "", "wrong"])
def test_bad_credentials_get_the_oauth_challenge(client, token):
    response = mcp_post(client, "tools/list", token=token)
    assert response.status_code == 401
    assert (
        'resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"' in response["WWW-Authenticate"]
    )


def test_an_empty_token_setting_does_not_open_the_endpoint(client, settings):
    settings.MCP_AUTH_TOKEN = ""
    assert mcp_post(client, "tools/list", token=None).status_code == 401
    assert mcp_post(client, "tools/list", token="").status_code == 401


def test_discovery_documents(client):
    resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert resource["resource"] == "http://testserver/mcp"
    assert resource["authorization_servers"] == ["http://testserver/oauth"]
    assert client.get("/.well-known/oauth-authorization-server/oauth").status_code == 200


def test_staff_connect_through_oauth_without_a_shared_token(client, settings, django_user_model):
    settings.MCP_AUTH_TOKEN = ""
    staff = django_user_model.objects.create_user(username="staff", password="pw", is_staff=True)
    assert mcp_post(client, "tools/list", token=oauth_token(staff)).status_code == 200


def test_non_staff_oauth_users_are_turned_away(client, django_user_model):
    member = django_user_model.objects.create_user(username="member", password="pw")
    assert mcp_post(client, "tools/list", token=oauth_token(member)).status_code == 403


def test_tool_annotations(client):
    tools = {tool["name"]: tool for tool in mcp_post(client, "tools/list").json()["result"]["tools"]}
    for name in ("list_devices", "list_dashboards", "list_playlists", "list_plugin_instances", "get_plugin_instance"):
        assert tools[name]["annotations"] == {"readOnlyHint": True}, name
    assert tools["update_plugin_data"]["annotations"]["destructiveHint"] is True


def test_merge_strategy_enum_matches_the_service():
    assert set(typing.get_args(mcp_tools.MergeStrategy)) == set(MERGE_STRATEGIES)


# --- tools -------------------------------------------------------------------------


def test_list_devices_reports_telemetry_and_the_current_render(client, device, single):
    render = fake_render(single)
    device.last_render = render
    device.battery_voltage = 4.1
    device.save()

    [row] = call(client, "list_devices")["devices"]
    assert row["playlist"] == "Main"
    assert row["telemetry"]["battery_voltage"] == 4.1
    assert row["current_render"]["dashboard"] == "Single"
    assert row["current_render"]["image_url"] == f"http://testserver/api/images/{render.pk}.{render.extension}"


def test_list_dashboards_shows_cells_and_renders(client, device, single, fluid):
    fake_render(single)
    dashboards = {row["name"]: row for row in call(client, "list_dashboards")["dashboards"]}
    assert [cell["instance"] for cell in dashboards["Fluid"]["cells"]] == ["Note", "Clock"]
    [target] = dashboards["Single"]["renders"]
    assert (target["profile"], target["orientation"]) == ("og", "landscape")
    assert target["latest"]["dashboard"] == "Single"
    assert dashboards["Fluid"]["renders"][0]["latest"] is None


def test_list_playlists(client, device):
    [playlist] = call(client, "list_playlists")["playlists"]
    assert playlist["devices"] == ["TRMNL"]
    assert [item["dashboard"] for item in playlist["items"]] == ["Single", "Fluid"]
    assert playlist["items"][1]["refresh_rate"] == 300


def test_list_and_get_plugin_instances(client, single):
    instances = {row["name"]: row for row in call(client, "list_plugin_instances")["plugin_instances"]}
    assert instances["Note"]["dashboards"] == ["Single"]

    by_name = call(client, "get_plugin_instance", {"instance": "Note"})
    by_uuid = call(client, "get_plugin_instance", {"instance": instances["Note"]["uuid"]})
    assert by_name == by_uuid
    assert by_name["settings"]["title"] == "Hi"


def test_unknown_instance_is_a_tool_error(client):
    assert "list_plugin_instances" in call_error(client, "get_plugin_instance", {"instance": "nope"})


def test_update_plugin_data_merges_like_the_webhook_and_queues_renders(client, single, queued):
    instance = PluginInstance.objects.create(
        name="Hook", plugin="markup", merge_variables={"sensor": {"humidity": 40}, "temps": [1, 2]}
    )
    single.cells.create(instance=instance, position=1)

    result = call(
        client,
        "update_plugin_data",
        {"instance": "Hook", "merge_variables": {"sensor": {"temperature": 42}}, "merge_strategy": "deep_merge"},
    )
    assert result["merge_variables"] == {"sensor": {"humidity": 40, "temperature": 42}, "temps": [1, 2]}
    assert result["queued_dashboards"] == [single.pk]
    assert queued == [[single.pk]]

    call(
        client,
        "update_plugin_data",
        {"instance": "Hook", "merge_variables": {"temps": [3, 4]}, "merge_strategy": "stream", "stream_limit": 3},
    )
    instance.refresh_from_db()
    assert instance.merge_variables["temps"] == [2, 3, 4]


def test_update_plugin_data_enforces_the_size_limit(client, settings, message, queued):
    settings.DJANGO_TRMNL_BYOS = {**getattr(settings, "DJANGO_TRMNL_BYOS", {}), "WEBHOOK_MAX_BYTES": 50}
    message_text = call_error(
        client, "update_plugin_data", {"instance": "Note", "merge_variables": {"text": "x" * 100}}
    )
    assert message_text == "Merged payload too large"
    assert queued == []


def test_update_plugin_data_rejects_unknown_strategies(client, message):
    text = call_error(
        client, "update_plugin_data", {"instance": "Note", "merge_variables": {}, "merge_strategy": "explode"}
    )
    assert "Invalid arguments" in text


def test_queue_render(client, device, single, queued):
    result = call(client, "queue_render", {"dashboard_id": single.pk})
    assert result["queued"] is True
    assert result["targets"] == [{"profile": "og", "orientation": "landscape"}]
    assert queued == [[single.pk]]


def test_queue_render_for_a_dashboard_no_device_shows(client, single, queued):
    result = call(client, "queue_render", {"dashboard_id": single.pk})
    assert result["queued"] is False
    assert result["note"]


def test_queue_render_unknown_dashboard(client):
    assert "list_dashboards" in call_error(client, "queue_render", {"dashboard_id": 999})
