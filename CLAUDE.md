# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

django-trmnl is a BYOS ("bring your own server") backend for TRMNL e-ink devices. It has two parts:

- `src/django_trmnl`: a reusable Django app containing all the real code.
- `example/` + `manage.py`: a thin project for running it locally.

Background research lives in `NOTES.md`: the device API contract, TRMNL Framework structure, and fluid mashup rules.

## Commands

Local (uv; SQLite by default via `DATABASE_URL`):

```sh
uv sync                                   # installs the package + "dev" and "example" dependency groups
uv run playwright install chromium        # one-time; needed for rendering
uv run python manage.py migrate           # also creates the django-q2 "django-trmnl tick" schedule
uv run python manage.py trmnl_demo        # demo plugins/dashboards/playlist + a simulated device

uv run python manage.py runserver 0.0.0.0:8000   # 0.0.0.0 so devices on the LAN can reach it
uv run python manage.py qcluster                 # django-q2: every-minute tick + on-demand renders
uv run python manage.py trmnl_render 1 --profile x --output renders/   # render one dashboard to files

uv run pytest                              # fast suite; real-browser tests are deselected by default
uv run pytest -m render                    # Playwright/Chromium render tests
uv run pytest tests/test_api.py::test_display_rotates_playlist_and_records_telemetry   # single test
uvx ruff check .                           # lint (config in pyproject.toml)

uv run python manage.py tailwind build     # rebuild the staff UI CSS (django-tailwind-cli, no Node)
```

Docker (from jefftriplett/django-startproject: `justfile`, `compose.yml` with db/web/worker/tailwind/utility on Postgres):

```sh
just bootstrap      # .env from .env-dist, lock, build
just up             # web on :8000, worker = qcluster
just manage <cmd>   # e.g. just manage createsuperuser
just test           # pytest in the utility container; `just test-render` for Chromium tests
just demo / just render 1 --profile x
just lint           # prek (pre-commit) hooks
```

Targets Django 6.0 (`django>=6.0,<6.1`). pytest uses `example.settings` (see `pyproject.toml`). The example project is configured from the environment with `environs`; see `.env-dist`.

## Architecture

**Request flow (the firmware polls; the server never contacts the device):**

- `views.py` implements `/api/setup`, `/api/display` and `/api/log`, plus `/api/images/<uuid>.<ext>` and the TRMNL-compatible webhook `/api/custom_plugins/<uuid>`.
- `/api/display` authenticates a device with `authenticate`, `adopt` or `rekey`, then calls `services.next_item`. That advances the playlist, skipping items with no render yet, and returns the image URL of an existing `Render`.
- Rendering never happens in the request unless `RENDER_INLINE` is set.
- `django_trmnl.urls` must be included at the site root, because the firmware calls `<base url>/api/display`. The API routes accept an optional trailing slash with `re_path`, so they never redirect.

**Render pipeline:** `compose.py` → `rendering.py` → `images.py`.

- `compose.compose(dashboard, profile, orientation)` builds the full HTML document. Plugins emit only `layout` (+ optional `title_bar`) markup. The composer wraps it in the TRMNL Framework hierarchy `screen → (mashup →) view` using the device profile's classes, and adds title bars.
- `rendering.Renderer` screenshots that HTML with Playwright Chromium and waits for `window.TRMNL_PLUGINS_READY`.
- `images.to_device_image` converts the screenshot to the profile's format: a 1-bit BMP3, or a 1/2/4-bit grayscale PNG from a hand-written encoder.
- A `Render` row stores the bytes plus a content-hash `fingerprint`. The fingerprint is the `filename` sent to the device, so identical pixels don't cause a redraw.

**Core modules:**

- **`devices.py`:** `DeviceProfile`s (og, og_2bit, x, small_400x300) define the CSS size, pixel ratio, bit depth, framework screen classes and image format. `MODEL_ALIASES` maps the firmware `Model` header to a profile.
- **`layouts.py`:** the 8 fixed mashups plus the fluid `3x3` grid. `Placement` validation requires start + span ≤ 4 and no overlaps. `view_size_for_placement` picks which of a plugin's four templates (full/half_horizontal/half_vertical/quadrant) a fluid cell uses.
- **`plugins/`:** a `Registry` of `Plugin` subclasses. `default_settings` + `help` document each setting in the admin, and `clean_settings` validates them (called from `PluginInstance.clean`). Weather geocodes a ZIP or place name through Open-Meteo. The clock's timezone accepts IANA names, city names or aliases like "Central" (`resolve_timezone`). `builtin.py` holds message, clock, month_calendar, weather, markup and json_api. `polls = True` plus `fetch()` provides polled data. Otherwise data comes from `PluginInstance.merge_variables`, via the webhook or by hand. The `markup` plugin renders user markup with Django templates or Liquid (`render_markup`); Liquid supports trmnl.com-style `{% template %}` partials.
- **`models.py`:**
  - `PluginInstance` holds settings plus data. `Dashboard` owns `DashboardCell`s (a position for fixed layouts, col/row/spans for fluid).
  - `Playlist`/`PlaylistItem` add ordering, time windows that may cross midnight, and weekdays.
  - `Device` stores telemetry from request headers and `last_render`.
  - `Dashboard.needs_render` decides staleness: the dashboard or any cell's instance was updated after the render, or the shortest refresh interval has elapsed.
- **`services.run_once`:** refreshes due polling instances, then renders stale `(dashboard, profile, orientation)` targets for every enabled device.
- **`tasks.py` (django-q2):**
  - `tick` wraps `run_once`. It runs every minute from a `Schedule` created by a `post_migrate` hook.
  - `render_dashboard` renders one dashboard for every profile that shows it.
  - `enqueue_render` is called on webhook updates, admin saves and cache misses in `/api/display`. It does nothing if `django_q` isn't installed.
- **Staff UI:** `preview.py` plus `templates/django_trmnl/preview/` provide the dashboards/devices index, per-dashboard preview (live HTML iframe vs rendered image) and a self-refreshing per-device mirror. It's styled with Tailwind:
  - source: `src/django_trmnl/tailwind/preview.css`
  - built: `src/django_trmnl/static/django_trmnl/preview.css`, committed so host projects don't need Tailwind
  - The Tailwind source uses `source(none)` and only scans the preview templates.
  - TRMNL device screens use the TRMNL Framework CSS, not Tailwind.
- **`conf.py`:** settings come from `settings.DJANGO_TRMNL` (dict), with defaults in `conf.DEFAULTS`. The Framework CSS/JS are pinned to 3.3.2.

## Hard-won constraints (verified against firmware source and real hardware)

- **Never return `"status": 500` from `/api/display`.** The firmware treats it as a reset: it wipes WiFi, API key *and* custom server URL, and falls back to trmnl.app. Unknown devices get `202`.
- **Firmware BMP decoding only accepts exactly 800×480 1-bit.** Every other panel size must be served PNG.
- **The Framework scales `.screen` by `--pixel-ratio` itself.** The Playwright viewport is therefore the panel's *physical* pixels (`profile.image_size`) at device scale factor 1, not CSS size × DPR.
- **Playwright's sync API runs on its own thread in `Renderer`.** Otherwise Django raises `SynchronousOnlyOperation` for ORM calls made while the browser is open. Keep ORM work on the caller's thread.
- **Panels without a framework device class use `override_size=True`**, which sets `--screen-w/--screen-h` inline. The 400×300 profile works this way.
- The device posts its own error logs to `/api/log`. Release firmware prints only ESP-IDF WiFi logs over USB serial.
