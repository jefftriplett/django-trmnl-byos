# TRMNL notes

Research notes for building a Django server that drives TRMNL dashboards.
Gathered 2026-09-26 from docs.trmnl.com/go, trmnl.com/developers, the
design-system and fluid-mashups blog posts, Framework docs v3.3, and the
firmware README.

Handy: every docs page has a Markdown twin (append `.md`), and the full index
is at https://docs.trmnl.com/go/llms.txt.

---

## 1. How TRMNL works

- **The device polls the server; the server never contacts the device.** It
  wakes, calls `GET /api/display`, downloads the image named in `image_url`,
  draws it, and sleeps for `refresh_rate` seconds.
- The server renders HTML to an image, and the JSON response points at that
  image. TRMNL keeps only the latest render per plugin, with no history.
- Hardware: ESP32 (C3/S3/C5), 1800–6000 mAh LiPo, 7.5"–10.3" EPD panels.
  Firmware is open source (GPL-3): https://github.com/usetrmnl/firmware
- There are four DIY setups:
  1. **Default**: their device, their firmware, their server.
  2. **BYOD**: your device, their firmware, their server (paid license).
  3. **BYOD/S**: your device, their firmware (possibly modded), your server.
  4. **BYOS**: their device pointed at your server. This is the one we are
     building.
- To point a device at your server, set a custom base URL in the WiFi
  captive portal.

### Device API contract (what a BYOS server must implement)

Source: firmware README "Web Server Endpoints" and docs `diy/byos`.

**`GET /api/setup`** swaps the device's MAC address for an API key and a
friendly ID, which the device stores.

```
headers: ID: XX:XX:XX:XX:XX:XX          # MAC address
200 → {"status":200,"api_key":"…","friendly_id":"917F0B",
       "image_url":"…/setup-logo.bmp","filename":"empty_state"}
404 → {"status":404,"api_key":null,"friendly_id":null,"image_url":null,"filename":null}
```

**`GET /api/display`** returns the next screen and advances the playlist.

```
headers:
  ID: <mac>
  Access-Token: <api_key>
  Refresh-Rate: 1800
  Battery-Voltage: 4.1
  FW-Version: 2.1.3
  RSSI: -69
  # newer firmware and BYOS servers also send/use Width, Height, Model, etc.
  # (not in the README; check Terminus's API doc before relying on them)

→ {"status":0,               # 202 if the device has no owner yet
   "image_url":"https://…/img.bmp|png",
   "filename":"2024-09-20T00:00:00",   # device uses this to skip redraws
   "refresh_rate":"1800",              # a string, in seconds
   "update_firmware":false,"firmware_url":null,
   "reset_firmware":false}
fail → {"status":500,"error":"Device not found"}
```

- `filename` works as a cache key: if it hasn't changed, the device doesn't
  redraw.
- Return `update_firmware: true` plus `firmware_url` when `FW-Version` is
  behind.

**`POST /api/log`**: the device posts diagnostics here when something goes
wrong. The body is JSON, and the docs don't define its schema ("tbd").

TRMNL's hosted API also has `GET /api/current_screen`, which returns the
current screen without advancing the playlist (used for mirroring and the
Chrome extension).

### Image formats

The firmware reads BMP3 and PNG natively (FW ≥ 1.5.2). ImageMagick recipes:

```sh
# 1-bit BMP3 (the classic OG format; must match exactly: 800x480 1-bit sRGB 2c)
magick in.png -monochrome -colors 2 -depth 1 -strip bmp3:out.bmp
# 1-bit PNG, dithered
magick in.png -dither FloydSteinberg -remap pattern:gray50 -depth 1 -strip png:out.png
# 2-bit PNG (OG on FW 1.6.0+, 4 grays)
magick in.png -colorspace Gray -dither FloydSteinberg -posterize 4 -alpha off -depth 2 -define png:compression-level=9 -strip png:out.png
# 4-bit PNG (16 grays, e.g. TRMNL X)
magick in.png -colorspace Gray -dither FloydSteinberg -posterize 16 -alpha off -depth 4 -define png:compression-level=9 -strip png:out.png
```

For framework-styled screens, prefer `-dither None`. The framework already
paints its own grays as bit patterns, so dithering them again adds noise.

### BYOD/S architecture advice (from TRMNL)

- **Plugin**: immutable definition (name, icon, form fields).
- **PluginSetting**: one configured instance or connection of a plugin. A
  single plugin can have many instances.
- **Playlist**: ordered PluginSettings (drag-and-drop in their UI), plus a
  **Scheduler** that sets when each item shows (time windows, conditions).
- "Next in queue" is the core problem. `/api/display` advances the playlist.

### Existing BYOS implementations (from the docs matrix)

| Impl | Stack | Notes |
|---|---|---|
| **Terminus** (flagship) | Ruby/Hanami | every feature 🟢; reference for API behavior |
| LaraPaper | PHP/Laravel | nearly full; no sensors |
| Inker | TypeScript | nearly full |
| BYOS Next.js | Next.js | no plugins/sensors/tests |
| BYOS FastAPI | Python | partial, unmaintained |
| **BYOS Django** | Python/Django | Devices, JSON API, image previews, Docker only; **not maintained**; marked inactive on /developers |
| BYOS Phoenix | Elixir | minimal, inactive |

For TLS, put a reverse proxy (e.g. Caddy) in front of the server. Most
implementations serve plain HTTP.

---

## 2. Private plugins (the hosted-platform model, worth mirroring)

- The **template language is Liquid** (Shopify). `{{ var }}` holds merge
  variables. Liquid filters handle formatting, e.g. `money_with_currency`.
- **Shared markup** is prepended to every layout before rendering, which
  makes it the place for shared CSS/JS. TRMNL also adds a custom tag:
  ```liquid
  {% template say_hello %}Hello there, {{ name }}.{% endtemplate %}
  {% render "say_hello", name: "General Kenobi" %}
  ```
- **Each plugin has four layouts:** full, half_horizontal, half_vertical,
  and quadrant. Mashups compose them.
- Data can arrive by **polling** (the server fetches a URL) or by
  **webhook** (you push the data):
  - `POST https://trmnl.com/api/custom_plugins/<plugin_setting_uuid>`
    with body `{"merge_variables": {...}}`. A `GET` on the same URL returns
    the current variables.
  - `"merge_strategy": "deep_merge"` merges nested keys.
    `"merge_strategy": "stream"` with `"stream_limit": N` appends to
    top-level arrays and caps their length.
  - Limits: 12 req/hr and 2 KB (TRMNL+ raises these to 30/hr and 5 KB).
    Going over returns `429`.
- **Recipes** are community plugins you can install or fork. There's a
  public Recipes API and Categories API.
- For charts, include Highcharts or Chartkick in the markup. The framework
  ships chart styling at `/framework/docs/3.3/chart`, plus `TRMNLCharts`.

This is a useful model for our design: a plugin instance holds merge
variables (from a webhook, polling, or Python code), and a template renders
them into each layout size.

---

## 3. Design system / Framework

Docs: https://trmnl.com/framework (versioned; **v3.3.2** is current, released 2026-09-18).
Releases: https://trmnl.com/framework/releases

- The philosophy is "sameness on purpose," like Shopify Polaris: plugins
  should look native. Less is more.
- **Assets** (pin a version; `latest` moves):
  - `https://trmnl.com/css/latest/plugins.css` (≈17.8 MB unminified, 14.7 MB
    min, 258 KB brotli) and `https://trmnl.com/js/latest/plugins.js`.
  - Themes: `themes/dark-theme.css`, `black-and-yellow-theme.css`,
    `white-and-red-theme.css`.
  - Each release has a downloadable zip for self-hosting. Fonts load from
    `/fonts` on whatever host serves the CSS.
  - Font bundles: **TRMNL** (TRMNL12/16/21, the default pixel fonts) and
    **Classic** (NicoPups/NicoClean/BlockKie). High-density screens use
    Inter Variable.
  - Self-hosting matters for us. Headless renders shouldn't download 17 MB
    of CSS every time.
- `<body class="environment trmnl">` goes on the body.

### Required structure (on a custom stack, we write all of it)

```
Screen → (Mashup →) View → Layout (+ optional Title Bar)
```

```html
<div class="screen screen--og screen--md screen--1bit">
  <div class="view view--full">
    <div class="layout">…content…</div>
    <div class="title_bar">
      <img class="image image--adaptive" src="…icon.svg">
      <span class="title">Plugin</span>
      <span class="instance">Instance</span>
    </div>
  </div>
</div>
```

- On trmnl.com the platform supplies screen, mashup, and view, and the
  plugin author writes only `layout` + `title_bar`. **In BYOS, our server
  must generate the wrappers.** This is the most important point for our
  design: plugin templates should emit only layout + title_bar, and the
  renderer wraps them.
- Exactly one `layout` per `view`. `title_bar` is a sibling of `layout`,
  not a child.
- Inside a layout, use `columns` for many same-type items (the overflow
  engine handles them), or `grid` or `flex`.
- Common components: title, value (sizes xxsmall through peta), label,
  description, item, table, progress, chart, map, rich text (`.richtext >
  .content`), image (with dithering), divider, border, background patterns
  (`bg-gray-1..7` historically; now `bg--gray-NN`).

### Screen / device classes

- `screen--<device>` sets the device profile: `--screen-w/h`,
  `--pixel-ratio`, `--device-ui-scale`, `--gap-scale`, and more.
- Also set the **size class** (`screen--sm|md|lg`), **density**
  (`screen--density-2x` for hi-DPI), and **bit depth**
  (`screen--1bit|2bit|4bit`). Responsive prefixes match these classes, not
  the device class.

| Device | Classes | CSS px |
|---|---|---|
| TRMNL OG | `screen--og screen--md screen--1bit` | 800×480 |
| TRMNL OG (2-bit fw) | `screen--ogv2 screen--md screen--2bit` | 800×480 |
| **TRMNL X** | `screen--v2 screen--lg screen--density-2x screen--4bit` | 1040×780 @1.8 ratio (≈1872×1404 physical) |
| Kindle 2024 | `screen--amazon_kindle_2024 screen--sm screen--density-2x screen--4bit` | |
| reMarkable 2, Kobo Aura One, Seeed E1003 | `screen--remarkable_paper_2`, `screen--kobo_aura_one`, `screen--seeed_e1003` (lg) | |
| custom panel | `screen--byod_custom` or compile with `$custom-devices` | |

Other screen modifiers: `screen--portrait`, `screen--no-bleed` (no
padding), `screen--dark-mode`, `screen--backdrop` (patterned background
behind mashup views), scale and text-scale modifiers (xxsmall = 66%, useful
for dense fluid mashups), and themes (`screen--theme-*`).

### Responsive grammar

`size:orientation:bit-depth:utility`, e.g. `md:portrait:4bit:value--large`.

- The size prefixes `sm:`, `md:`, `lg:` are mobile-first and progressive.
- `portrait:` is the only orientation prefix, because landscape is the
  default.
- `1bit:`, `2bit:`, `4bit:` target one exact depth each and are not
  progressive. They apply only to color and typography utilities.
- `--base` modifiers reset a size at a breakpoint, e.g.
  `title--small lg:title--base`.
- **Container query units:** `.layout` is a query container, so
  `w--[50cqw]` and `h--[80cqh]` scale with the *cell* rather than the
  screen. This is the key tool for content that must look right in any
  fluid-mashup cell.
- The data-attribute engines also take responsive suffixes:
  `data-overflow-max-cols(-lg|-portrait|-lg-portrait)`, `data-clamp-*`.

### Framework runtime (`plugins.js`), which matters for rendering

Runs after page load: images → index widths → value formatting
(`data-value-format`) → fit value (`data-fit-value`) → grid/column gap
fixes → overflow (columns, "and N more") → clamp → table overflow
(`data-table-limit`) → content limiter → pixel-perfect fonts (idle time).

- `window.TRMNL_PLUGINS_READY` goes `true` when a pass settles, so **the
  screenshotter must wait for it**.
- `terminalize()` returns a Promise and should be re-run after injecting
  content. `executeTerminalize()` is a debounced version.
- The `trmnl:terminalize:stats` event reports what each step did. Set
  `window.__TRMNL_DEBUG__ = true` for logging.
- `window.__TRMNL_BUILD__` reports which `plugins.js` version loaded.

---

## 4. Fluid Mashups ⭐

Blog post (2026-07-13): https://trmnl.com/blog/fluid-mashups ·
demo: https://trmnl.com/m2 · help: https://help.trmnl.com/en/articles/10168132-mashups ·
markup: https://trmnl.com/framework/docs/3.3/mashup

The blog post is mostly a product announcement. The technical detail lives
in the Framework docs and release notes (3.1.x–3.3.x).

### Concept

- Before: **8 fixed layouts**. Their classes are `mashup--1x1`, `1Lx1R`,
  `1Tx1B`, `1Lx2R`, `2Lx1R`, `2Tx1B`, `1Tx2B`, and `2x2`. In these, a
  view's modifier (`view--half_vertical`, `view--quadrant`, …) sets its
  size.
- Now: **`mashup--3x3`**, a 9-slot grid that you carve into rectangles
  yourself. The blog counts **322 possible layouts**, e.g. 2/3 weather +
  1/3 agenda, or a central calendar surrounded by photos.
- In the TRMNL product, fluid mashups are **only available on TRMNL X and
  X-sized devices**: reMarkable, Kobo Aura One, Boox Nova Air C, Seeed
  E1003. That limit reflects screen size, not the CSS. The classes work
  anywhere.

### Markup

```html
<div class="screen screen--v2 screen--lg screen--density-2x screen--4bit">
  <div class="mashup mashup--3x3">
    <div class="mashup-cell mashup-cell--col-1 mashup-cell--col-span-2
                mashup-cell--row-1 mashup-cell--row-span-3">
      <div class="view view--full">
        <div class="layout">…</div>
        <div class="title_bar">…</div>
      </div>
    </div>
    <div class="mashup-cell mashup-cell--col-3 mashup-cell--row-1">
      <div class="view view--quadrant"><div class="layout">…</div></div>
    </div>
    …
  </div>
</div>
```

- Placement classes: `mashup-cell--col-{1..3}`, `--col-span-{1..3}`,
  `--row-{1..3}`, `--row-span-{1..3}`.
- **Keep start + span ≤ 4.** If a cell overflows, CSS grid adds an
  auto-sized 4th column or row, and the whole mashup re-lays out.
- Nine `mashup-cell`s with no placement classes fill the grid left to
  right, top to bottom.
- **A view inside a cell always fills the cell,** whatever its `view--*`
  class, and `w--*`/`h--*` on the view do nothing. Size the content inside
  instead, using container query units and responsive classes.
  - The `view--*` class still selects which of the plugin's four templates
    to render. A reasonable mapping from cell span to template: 3×3 or 2×2+
    → full; wide (col-span > row-span) → half_horizontal; tall → 
    half_vertical; 1×1 → quadrant.
- **Every cell gets a compact title bar**, whatever its size, and the
  layout shrinks to fit it.
- Each cell draws its own frame and surface on 1-, 2-, and 4-bit screens,
  in dark mode, with themes, and with `screen--backdrop`.
- **As of 3.3.2, TRMNL renders each `.mashup-cell` as a self-contained
  `iframe`,** and the CSS now handles cells without a parent. This fixes
  the old problem where two instances bound to the same `#my-chart` ID
  collided. Our renderer should either render one iframe per cell or
  require plugins to use per-instance IDs.
- For dense grids, `screen--scale-xxsmall` (66%) works well. Non-regular
  scales switch text to Inter Variable so scaled glyphs stay crisp.

### Implications for our server

- Store a mashup as a list of cells, where each cell is a plugin instance
  plus (col, row, col_span, row_span). Validate that start + span ≤ 4 and
  that cells don't overlap.
- For each cell, choose the template size (full/half_*/quadrant) from its
  span and render that template.
- Wrap everything in screen, mashup, cell, and view. Take the screen
  classes from the target device's profile.
- Since cells run up to 3×3 on a 1040×780 X, a 1×1 cell is about
  330×240 CSS px. Plugin templates must survive that, which is what
  container units, `data-fit-value`, and clamp are for.

---

## 5. Review of `byos_django-git/` (usetrmnl/byos_django)

It's small: about 600 lines across models, views, admin, a Channels
consumer, and a Monaco live preview. Django 5.2, Pipenv, SQLite, Channels
and Daphne, Playwright (Firefox), and Wand (ImageMagick). The last commits
only bump dependencies. Upstream marks it unmaintained.

### What it does

- `Device` (MAC, api_key, friendly_id, owner user, refresh_rate,
  last_seen), `DeviceLog` (JSON), `Screen` (raw HTML plus a rendered BMP
  stored as a `BinaryField`), and `APIKey`.
- `/api/setup/` auto-creates a device for an unknown MAC. The admin then
  pairs it to a user.
- `/api/display/` serves the **newest** `Screen` for the device, falling
  back to the Rover image.
- `/api/log` stores the posted body.
- `/api/v1/generate_screen` (Bearer key) takes raw HTML, renders it
  synchronously, and returns base64.
- `/api/v1/media/<friendly>-<id>.bmp?api_key=…` serves the image.
- `/preview` is a Monaco editor that sends HTML over a websocket and gets
  back a live-rendered BMP.
- `trmnl/plugins.py` defines `BasePlugin`/`StaticHTMLPlugin`, but nothing
  uses them.

### Gaps and problems

- **There are no playlists, plugins, data sources, templates, scheduling,
  or mashups.** A "screen" is one static HTML blob, and the device always
  shows the most recent one.
- **Only 800×480 1-bit BMP is supported.** Viewport and dithering are
  hardcoded, so TRMNL X (1040×780, 4-bit PNG, 1.8 pixel ratio) and fluid
  mashups are out of reach without rewriting the renderer.
- **It screenshots without waiting for `TRMNL_PLUGINS_READY`.** It calls
  `set_content` and captures immediately, so overflow, clamp, and
  fit-value may not have run, and fonts and images may not have loaded.
- **Rendering is synchronous in the request and admin save path.**
  Playwright launches per render and there's no job queue.
- The Wand pipeline dithers the entire screenshot (posterize + FS +
  gray50). That fights the framework's own gray patterns.
- Framework assets come from `usetrmnl.com/.../latest` (the old domain),
  unpinned, and fetched at every render.
- Security and quality issues:
  - Keys are generated with `random.choices` instead of `secrets`.
  - The device api_key travels in the image URL query string.
  - The display view ignores the Battery-Voltage, RSSI, and FW-Version
    headers.
  - `preview` does `open("templates/base.html")`, which depends on the
    working directory.
  - The consumer returns a 1-tuple, which the JS papers over with `[0]`.
  - `tests.py` is empty.
- URLs are inconsistent: `api/setup/` and `api/display/` have trailing
  slashes, but `api/log` doesn't, while the firmware calls `/api/setup`.
  This works only through `APPEND_SLASH` redirects. I haven't verified
  whether firmware follows them.

### Worth keeping or borrowing

- The shape of setup, display, and log, plus auto-provisioning (an
  unknown MAC creates a device, which an admin claims).
- The docker-compose pattern: a separate `playwright run-server`
  container reached over `PW_SERVER` websocket.
- The live preview idea (editor → websocket → rendered image).
- Using Django admin as the dashboard to start with.

---

## 6. Recommendation: build new; borrow from byos_django, don't fork it

Most of byos_django's value is ~150 lines of device-API views, and those
are easy to re-derive from the firmware README (section 1). Everything a
dashboard server needs is absent: plugins, data, templates, playlists,
mashups, multiple device sizes, and background rendering. The rendering
core would need rewriting for TRMNL X and fluid mashups anyway. Forking
would mean inheriting an unmaintained layout and deleting most of it.

A new design, possibly as a reusable app (`django-trmnl`) plus a thin
example project:

- **Models**
  - `DeviceModel`/profile: screen classes, width, height, pixel ratio, bit
    depth, format.
  - `Device`: MAC, `secrets`-generated key, friendly ID, profile, refresh
    rate, battery/RSSI/firmware telemetry.
  - `Plugin`: a Python class registry, like byos_django's unused
    `BasePlugin` but real. It fetches data, returns context, and ships
    templates for the four sizes.
  - `PluginInstance`: config plus cached merge variables, fed by webhook,
    polling, or code.
  - `Playlist`/`PlaylistItem`: order, schedule windows, duration.
  - `Mashup` with `MashupCell` rows: col, row, spans, instance.
  - `Render`: image file, filename or hash, created time.
- **Rendering pipeline** (background task, e.g. Django 6's tasks framework
  or a simple worker):
  1. Render the templates.
  2. Wrap them in screen, mashup, cell, and view.
  3. Serve pinned, self-hosted framework CSS/JS.
  4. Screenshot with Playwright (Chromium) at the profile's viewport and
     `device_scale_factor`, after waiting for
     `window.TRMNL_PLUGINS_READY === true`.
  5. Convert with ImageMagick per bit depth (1-bit BMP/PNG, 2- or 4-bit
     PNG).
  6. Store the file and use a content hash as `filename`, so the device
     skips unchanged redraws.
- `/api/display` just picks the next playlist item's latest render and
  never renders inline.
- **Open decision: Liquid or Django templates for plugin markup.**
  - Liquid (via `python-liquid`) lets us paste TRMNL recipes and
    private-plugin markup nearly unchanged, including
    `{% template %}`/`{% render %}`.
  - Django templates are more natural for Python-defined plugins.
  - Could support both, per plugin.
  - Note: `{# #}` comments in Django templates are single-line only. Use
    `{% comment %}` for anything that wraps.
- Terminus (Ruby) is the reference for the finer points of API behavior:
  extra headers, sleep mode, firmware updates, and model/size negotiation.
  Check it before finalizing `/api/display`.

---

## 7. TRMNL API coverage (checked 2026-09-26)

Sources: https://docs.trmnl.com/go/private-api/introduction and https://docs.trmnl.com/go/public-api/introduction

### Private API (device API key, `Access-Token` header)

| Endpoint | Us | Notes |
|---|---|---|
| `GET /api/setup` | ✅ | |
| `GET /api/display` (advances the playlist) | ✅ | `refresh_rate` is an integer (matches TRMNL's spec). No firmware updates or special functions. |
| `GET /api/current_screen` (current screen, no advance) | ❌ | For mirrors/BYOD clients; easy to add from `Device.last_render`. |
| `POST /api/log` | ✅ | |
| Plugin Data API ("Plugin Merge" into a private plugin) | ❌ | |
| Account API (`Authorization: Bearer user_…`; devices, playlists, plugin settings) | 🟡 | No REST API; the same jobs are done in the admin and the `/trmnl/` pages. |

### Public API (no auth)

| Endpoint | Us | Notes |
|---|---|---|
| `GET /api/models` | 🟡 | We hardcode 4 profiles matching TRMNL's `og_png`, `og_plus` and `v2`, plus our own 400x300. |
| `GET /api/palettes` | ❌ | Needed for color panels (e.g. BWRY). |
| `GET /recipes.json`, `/recipes/{id}.json` | ❌ | Community plugins; Liquid support would let us import them. |
| `GET /api/categories` | — | Marketplace only. |

Also supported: the private plugin webhook `POST/GET /api/custom_plugins/{uuid}` (replace, `deep_merge`, `stream`).
