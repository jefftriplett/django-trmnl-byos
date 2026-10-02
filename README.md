# django-trmnl

Drive [TRMNL](https://trmnl.com) e-ink dashboards from Django. This is a
BYOS ("bring your own server") implementation: point a TRMNL at it and it
serves screens built from plugins, playlists, and **fluid mashups**.

- **Device API** the firmware speaks: `/api/setup`, `/api/display`, `/api/log`
  (with or without a trailing slash). Unknown devices are auto-provisioned,
  and battery, RSSI, and firmware version are recorded on every check-in.
- **Plugins** in Python: message, clock, month calendar, weather (Open-Meteo,
  no key needed), custom markup, and JSON polling. Custom markup can use
  Django templates or **Liquid**. Liquid supports trmnl.com-style
  `{% template %}` partials, so private plugins and recipes port over nearly
  unchanged.
- **TRMNL-compatible webhook**: `POST /api/custom_plugins/<uuid>` with
  `merge_variables`, plus the `deep_merge` and `stream` strategies.
- **Dashboards**: full screen, the 8 fixed mashups, and the fluid
  `mashup--3x3` grid. Place any plugin on any rectangle of the 3×3 grid.
- **Device profiles**: TRMNL OG (1-bit BMP), OG 2-bit (PNG), and TRMNL X
  (1872×1404 4-bit PNG), in landscape or portrait.
- **Renderer**: headless Chromium via Playwright, pinned to TRMNL Framework
  3.3.2. It waits for the framework runtime (`TRMNL_PLUGINS_READY`) before
  capturing, so overflow, clamping, and value fitting have finished. If the
  pixels haven't changed, the filename stays the same and the device skips
  the redraw. The Framework's CSS, JS, fonts and icons are served from a local
  copy (`manage.py trmnl_fetch_assets`, run at Docker build time), so renders
  never download them.
- **Playlists** with ordering, per-item refresh rates, time windows
  (including windows that cross midnight), and weekdays.
- **Staff preview pages** show the live HTML next to the rendered device
  image, for any profile and orientation.
- **MCP server** at `/mcp` (django-mcpz), so Claude and other MCP clients can
  see devices, dashboards and playlists, update plugin data, and queue renders.

See [NOTES.md](NOTES.md) for the research behind it (device API contract, framework structure, fluid mashup rules).

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                                   # install
uv run playwright install --only-shell chromium   # one-time: Chromium's headless shell
uv run python manage.py migrate
uv run python manage.py trmnl_fetch_assets   # local copy of the Framework CSS/JS/fonts (optional; else fetched on first render)
uv run python manage.py createsuperuser
uv run python manage.py trmnl_demo        # demo plugins, dashboards, playlist + a simulated device
```

Run the web server and the render worker, each in its own terminal:

```sh
uv run python manage.py runserver 0.0.0.0:8000
uv run python manage.py qcluster          # django-q2: refreshes data + renders stale dashboards every minute
```

Then open:

| URL | What |
|---|---|
| http://localhost:8000/trmnl/ | Dashboards, devices (with what they're showing), webhook URLs |
| http://localhost:8000/trmnl/devices/1/ | Live mirror of one device: current screen, up next, health, logs |
| http://localhost:8000/trmnl/dashboards/1/?profile=x | Fluid mashup preview: live HTML + device image, "Render now" |
| http://localhost:8000/admin/ | Edit plugins, dashboards (cells inline), playlists, devices |

## Point a real TRMNL at it

Your computer and the TRMNL must be on the same network, and the computer
must stay awake while the TRMNL is using it.

1. Start the server on all interfaces (`runserver 0.0.0.0:8000`) and keep
   `qcluster` running.
2. Find the computer's LAN IP, e.g. `ipconfig getifaddr en0` on macOS.
   Check that another device can open `http://<ip>:8000/trmnl/`. If it
   can't, allow Python through the macOS firewall.
3. Put the TRMNL into setup mode: **hold the button on the back for more
   than 5 seconds.** That clears its WiFi, and it shows setup instructions.
4. On your phone, join the WiFi network the TRMNL broadcasts. Its setup
   portal opens.
5. Pick your office WiFi. In the portal's custom server option, enter
   `http://<ip>:8000` (plain http, no trailing slash). Save.
6. The device connects and calls the server. It shows up at `/trmnl/`
   either way:
   - A fresh device calls `/api/setup`.
   - A device already registered with trmnl.com keeps its old API key and
     goes straight to `/api/display`. django-trmnl adopts it when its MAC
     address is new.
7. In the admin, set the device's **profile** to match the hardware:
   **TRMNL OG (1-bit)**, **OG (2-bit, firmware 1.6+)**, or **TRMNL X**.
   Assign a playlist if it didn't get the default one.
8. While trying things out, set the device's refresh rate to 60–300
   seconds. A **short press** of the button also wakes it for the next
   screen right away.

Open `/trmnl/devices/<id>/` to see the device's current screen, what's up
next, and its battery, WiFi signal, firmware, and recent logs. The page
refreshes itself every 30 seconds.

To go back to trmnl.com, repeat steps 3–5 and clear the custom server.

## Test without a device

`trmnl_demo` prints a simulated TRMNL X's credentials. Use them to act as
the firmware:

```sh
curl http://localhost:8000/api/display \
  -H 'ID: AA:BB:CC:00:00:01' -H 'Access-Token: <token from trmnl_demo>' \
  -H 'Battery-Voltage: 4.1' -H 'RSSI: -61' -H 'FW-Version: 1.6.2'
# → {"status": 0, "image_url": "http://localhost:8000/api/images/<id>.png", "filename": "...", "refresh_rate": "900", ...}
```

Each call advances the playlist. Open `image_url` to see what the device
would draw.

Push data to the demo's Liquid "Tasks" plugin (its URL is on `/trmnl/` and
printed by `trmnl_demo`):

```sh
curl -X POST http://localhost:8000/api/custom_plugins/<uuid> -H 'Content-Type: application/json' \
  -d '{"merge_variables": {"tasks": [{"name": "Try the webhook", "due": "Now"}]}, "merge_strategy": "stream", "stream_limit": 5}'
```

Render to files without the server:

```sh
uv run python manage.py trmnl_render --profile x --output renders/
```

## Working on the web UI

The TRMNL screens use TRMNL's own framework and aren't touched by this. Our
web pages (`/trmnl/…`) are styled with Tailwind through
[django-tailwind-cli](https://github.com/django-commons/django-tailwind-cli),
with no Node needed:

- Source: `django_trmnl_byos/tailwind/preview.css`
- Built file: `django_trmnl_byos/static/django_trmnl_byos/preview.css` (committed
  and shipped with the package, so host projects don't need Tailwind)

```sh
uv run python manage.py tailwind watch   # rebuild on template changes
uv run python manage.py tailwind build   # minified build before committing
```

## Running the tests

```sh
uv run pytest            # fast suite (no browser)
uv run pytest -m render  # real Chromium renders of OG and TRMNL X
```

## Fluid mashups

Create a dashboard with layout **Fluid (3 x 3 grid)** and give each cell a
column, row, and spans. Every start + span must be ≤ 4, and cells can't
overlap; the admin checks both. The cell's shape picks which template the
plugin renders:

| Cell shape | Template |
|---|---|
| 2×2 or larger | `full` |
| wider than tall | `half_horizontal` |
| taller than wide | `half_vertical` |
| 1×1 | `quadrant` |

The framework docs say fluid mashups are meant for X-sized screens (TRMNL X,
reMarkable, Kobo Aura One, E1003). They also render fine on an OG, as the
demo shows.

## Writing a plugin

```python
from django_trmnl_byos.plugins import Plugin, registry

@registry.register
class HelloPlugin(Plugin):
    key = "hello"
    name = "Hello"
    template_name = "myapp/hello.html"      # gets size, settings, data, trmnl, and merge variables
    default_settings = {"who": "world"}
    polls = False                           # True + fetch(instance) -> dict for polled data
```

Templates emit only the plugin's `layout` (and optionally its own
`title_bar`). The server adds the screen, mashup, and view wrappers. Use
`{{ trmnl.dom_id }}` for element IDs, so two copies of a plugin in one
mashup don't collide.

## Settings

Put overrides in `DJANGO_TRMNL_BYOS = {...}`. See `django_trmnl_byos/conf.py` for
every option and its default.

| Setting | Default |
|---|---|
| `FRAMEWORK_CSS_URL` / `FRAMEWORK_JS_URL` | TRMNL Framework 3.3.2 (pinned) |
| `ASSET_CACHE_DIR` | `BASE_DIR / "trmnl_assets"`: the local copy of the Framework CSS, JS, fonts and icons that renders are served from |
| `AUTO_PROVISION` | `True`: unknown MACs get created on `/api/setup` |
| `DEFAULT_PROFILE` | `"og"` |
| `DEFAULT_REFRESH_RATE` | `900` seconds |
| `RENDER_INLINE` | `False`: render inside `/api/display` when no image exists (dev convenience) |
| `BASE_URL` | build image URLs from the request; set this behind a proxy |
| `PLAYWRIGHT_WS_ENDPOINT` | launch Chromium locally; or connect to `playwright run-server` |
| `WEBHOOK_MAX_BYTES` | 64 KB |

In the `config/` project, the environment variables `TRMNL_RENDER_INLINE`,
`TRMNL_BASE_URL`, `PLAYWRIGHT_WS_ENDPOINT`, `TIME_ZONE`, `ALLOWED_HOSTS`,
and `SECRET_KEY` set these. `MCP_ENABLED` and `MCP_AUTH_TOKEN` control the
[MCP server](#mcp-server).

## MCP server

The `config/` project serves an [MCP](https://modelcontextprotocol.io) server
at **`/mcp`**, built on [django-mcpz](https://django-mcpz.readthedocs.io/). Use
that exact URL: there is no trailing slash, and `/mcp/` is not redirected,
because MCP clients POST and a POST can't follow a redirect.

| Tool | What it does |
|------|--------------|
| `list_devices` | Devices with telemetry (battery, RSSI, firmware, last seen) and the render each is showing |
| `list_dashboards` | Dashboards, the plugin in each cell, and the latest render per profile/orientation |
| `list_playlists` | Playlists and their items, time windows and weekdays |
| `list_plugin_instances` / `get_plugin_instance` | Plugin instances, their settings and current data |
| `update_plugin_data` | Same as posting to the instance's webhook (`replace`, `deep_merge`, `stream`), then queues renders |
| `queue_render` | Queue a render of a dashboard for every profile that shows it |

**Who can connect.** Every request needs a credential; there is no open mode.

- **Staff, via OAuth:** Claude Code, Claude.ai and ChatGPT sign in through
  `/oauth/authorize` (django-mcpz's authorization server) with a staff
  account. Add `https://<your host>/mcp` as a custom connector and approve it.
  Non-staff accounts are refused.
- **A shared token:** set `MCP_AUTH_TOKEN` and send
  `Authorization: Bearer <token>`. Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Leaving it
  empty turns token access off; it never opens the endpoint.

```sh
claude mcp add --transport http trmnl https://<your host>/mcp \
  --header "Authorization: Bearer $MCP_AUTH_TOKEN"
```

Set `MCP_ENABLED=false` to stop routing `/mcp` and `/oauth/`.

**In your own project**, install the `mcp` extra and add the tools to your own
django-mcpz server, with whatever auth you choose:

```python
from django_mcpz.server import MCPServer
from django_trmnl_byos.mcp_tools import register_tools

server = MCPServer(name="my-trmnl", version="1.0", auth=my_auth)
register_tools(server)
urlpatterns = [path("mcp", server), ...]
```

## Using it in your own project

```python
INSTALLED_APPS = [..., "django_trmnl_byos"]
urlpatterns = [..., path("", include("django_trmnl_byos.urls"))]   # must be at the root: firmware calls /api/display
```

## Security notes

The device API authenticates with the per-device `Access-Token`. Image and
webhook URLs use unguessable UUIDs, the same model trmnl.com uses. Auto
provisioning means anything that can reach the server can register a
device, which is fine on a home network. Turn off `AUTO_PROVISION`, or put
the server behind a VPN or proxy, before exposing it more widely. Preview
pages require a staff login. `/mcp` requires `MCP_AUTH_TOKEN` or a staff
OAuth sign-in (see [MCP server](#mcp-server)).

## License

MIT
