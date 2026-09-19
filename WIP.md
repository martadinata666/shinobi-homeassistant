# Shinobi ⟷ Home Assistant — Work In Progress

Goal: a full-featured Home Assistant custom integration for **Shinobi NVR**.

Target Shinobi API: https://docs.shinobi.video/api

---

## Real-time push notifications — ✅ DONE, no new Shinobi code needed

Initial ask was "build a notification module for Shinobi." Investigation
found Shinobi already has a purpose-built, zero-config channel for exactly
this — so the actual deliverable is entirely on the HA side
(`socket_client.py`), not a new Shinobi server module.

**What already exists in Shinobi (verified by reading source, not assumed):**
- `libs/socketio.js:737-743` — the `detector_on` socket command, whose own
  comment states the intent: *"Subscribe to detection events only (no
  viewer registration, no stream rooms). Used by mobile clients for
  push/local notifications."* It just does
  `cn.join('DETECTOR_'+d.ke+d.id)`.
- `libs/events/utils.js:978-986` — every triggered event (motion or object
  detection) broadcasts to that room:
  `s.tx({f:'detector_trigger', id, ke, time, details, doObjectDetection},
  DETECTOR_${ke}${mid})`. No snapshot/image is included — confirms the
  "use the time provided to get a snapshot frame if needed" instruction was
  necessary, not optional.
- Auth requires **both** `ke` and `uid` to match a DB row
  (`streamConnectionAuthentication` in `libs/socketio.js`), even for API-key
  auth — confirmed by querying the live DB directly
  (`SELECT ke,uid,code,details FROM API WHERE code=...`) and finding
  `uid: <uid>` tied to our configured key, with `details.auth_socket:
  "1"` (required — auth is rejected otherwise).
- Found `GET /{api_key}/api/{ke}/get/{code}` (`libs/webPaths/apiKeys.js`)
  resolves `uid` from `s.auth`'s own session when called with our own key as
  both the URL prefix and `code` — so the integration can resolve its own
  uid automatically at startup; the user never has to look this up or enter
  it anywhere.

**HA-side implementation (`custom_components/shinobi/socket_client.py`):**
- `ShinobiSocketClient` — one persistent `python-socketio` `AsyncClient`
  connection per config entry, using Shinobi's own socket.io server
  (`socket.io: ^4.8.0` in `package.json`, current-gen protocol, no version
  shim needed).
- Connect → `f:'init'` with `{ke, uid, auth: api_key}` → on `init_success`,
  emit `detector_on` for every currently-known monitor id.
- New monitors get subscribed automatically: the coordinator's own
  add-listener mechanism (already built for dynamic entity discovery) also
  calls `socket_client.async_ensure_subscribed()` on every refresh; a
  private `_subscribed` set makes re-subscription of already-joined
  monitors a no-op.
- On `detector_trigger`, fires `hass.bus.async_fire("shinobi_detector_trigger",
  {...})` with monitor_id, time, reason, confidence, matrices,
  do_object_detection, and a best-effort `snapshot_url`.
- **Snapshot lookup only runs when `details.reason == "object"`** — the
  same Shinobi-native convention already established for the object-count
  sensor (see below), not run for every bare ONVIF motion ping (no
  matrices, fires far more often, a snapshot wouldn't be useful anyway).
  Searches Shinobi's timelapse-frame index in a
  `±DETECTOR_SNAPSHOT_WINDOW_MINUTES` (20 min) window around the event's own
  `time` — reusing the exact same `async_get_timelapse_frame` /
  `timelapse_frame_url` methods already built for the media browser.
- Security-relevant finding, documented in the code: once authenticated,
  this connection is treated like any other dashboard session and receives
  *everything* broadcast to `GRP_{ke}` — including `users_online`, which
  carries **other users' plaintext credentials** (webdav/B2/etc.
  passwords), plus disk usage and raw FFMPEG stderr. The client only ever
  acts on — and only ever logs — `init_success`/`detector_trigger`; every
  other message type is dropped completely unread and unlogged, specifically
  to avoid leaking those credentials into HA's log file.
- `manifest.json` requirements: `python-socketio>=5.11.0` (already present
  in the HA container as a transitive dependency, but declared explicitly
  since custom components must declare their own direct deps).

**Verified fully live, three layers deep:**
1. Raw socket.io test client (`socketio.AsyncClient`, no HA involved):
   connected → `f:init` → received `init_success` → emitted `detector_on`
   for `CAM1` → fired a synthetic event via
   `GET /{apiKey}/motion/{ke}/{id}?data=<json>` (same injection technique
   used for the object-count sensor) → received the exact
   `detector_trigger` broadcast in real time, byte-for-byte matching what
   `libs/events/utils.js` sends.
2. Snapshot-lookup window sized empirically against real data: zero-width
   and even ±5min windows around a real event found nothing; ±20min found
   a real frame ~13 min prior — set the constant from that evidence, not a
   guess.
3. End-to-end through the actual integration: subscribed to
   `shinobi_detector_trigger` over HA's own websocket API, fired a
   synthetic `reason:"object"` event on Shinobi, and received the HA bus
   event with `matrices` matching exactly what was injected. Separately
   confirmed a `snapshot_url: null` result was legitimate (no timelapse
   frame within ±20min of that specific timestamp — verified by querying
   the timelapse endpoint directly with the same window), not a bug in the
   lookup code, since the same code path found a real frame minutes earlier
   in test #2.

**Known limitations:**
- Snapshot lookup is best-effort — on a monitor with a sparse timelapse
  capture interval (this dev server: ~15min), most `detector_trigger`
  events will legitimately get `snapshot_url: null`. A production setup
  wanting reliable notification images should configure a shorter
  timelapse-frame interval on monitors used for this.
- The socket connection subscribes to *new* monitor ids automatically but
  doesn't explicitly `detector_off` a deleted monitor's room — harmless
  (Shinobi simply stops emitting into a room for a monitor that's gone;
  Socket.IO room membership isn't a resource leak), just noted for
  completeness.
- No supervising/backoff beyond `python-socketio`'s own built-in
  reconnection (`reconnection=True`, 5s delay) — sufficient for this dev
  environment, not load-tested against a flaky connection.

---

## Shinobi-side fix: `libs/control/ptz.js` hang bug — ✅ FIXED UPSTREAM

`ptzControl()` had three early-`return` paths that never invoked its `callback`
parameter, which the HTTP route (`GET /{apiKey}/control/{ke}/{mid}/{direction}`)
depends on to call `res.end()`. Any of these left the request hanging until
client timeout instead of responding:
1. Monitor not active (`!s.group[ke] || !activeMonitors[id]`)
2. `monitorConfig.details.control !== "1"` (control disabled)
3. Axis-lock rejection (direction not allowed by `control_axis_lock`)

All three now build a `response` object and call `callback(response)` before
returning, matching the function's normal exit path. Deployed to the live dev
Shinobi (`/home/Shinobi/libs/control/ptz.js`, original backed up alongside as
`ptz.js.bak-<timestamp>`), restarted via `sudo pm2 restart camera`, and
verified:
- Control-disabled monitor: was a 15s+ hang → now `{"ok":false,"msg":"Control
  is not enabled"}` in **0.04s**
- Control-enabled monitor: unaffected, still a real ONVIF
  ContinuousMove→Stop round-trip returning `ok: true`

Our HA integration's client-side guard (checking `details.control` before
ever issuing the request — see PTZ section below) stays in place regardless,
since a self-hosted Shinobi elsewhere on a user's network may be running an
unpatched version.

---

## Verified Shinobi API surface (against live dev server <shinobi-host>:8080)

| Capability | Endpoint | Status |
|---|---|---|
| List monitors | `GET /{apiKey}/monitor/{ke}` | ✅ verified |
| Get one monitor | `GET /{apiKey}/monitor/{ke}/{mid}` | ✅ verified |
| Live stream (HLS) | `monitor.streams[]` → `/{apiKey}/hls/{ke}/{mid}/s.m3u8` | ✅ verified |
| Snapshot (JPEG) | `GET /{apiKey}/jpeg/{ke}/{mid}/s.jpg` | ✅ verified (image/jpeg) |
| MJPEG stream | `GET /{apiKey}/mjpeg/{ke}/{mid}` | ✅ available |
| Recordings list | `GET /{apiKey}/videos/{ke}[/{mid}]` | ✅ verified (35 clips) |
| Recording file | `GET /{apiKey}/videos/{ke}/{mid}/{filename}` | ✅ available |
| Events / detections | `GET /{apiKey}/events/{ke}[/{mid}]` | ✅ verified (onvif motion) |
| Mode control | `GET /{apiKey}/monitor/{ke}/{mid}/{start|record|stop|idle}` | ✅ available |
| Motion trigger | `GET /{apiKey}/motion/{ke}/{mid}` | ✅ available |
| PTZ (ONVIF) | `/{apiKey}/ptz/...` control module | ✅ available |
| Embeddable player page | `GET /{apiKey}/embed/{ke}/{mid}` | ✅ verified — full HTML page, client-side hls.js/flv.js, no `X-Frame-Options`/CSP blocking. Exposed as `embed_url` camera attribute. |
| MQTT outbound | event trigger w/ base64 snapshot + deep link | ✅ in Shinobi core |
| WebSocket (socket.io) | real-time monitor/event push | ✅ in Shinobi core |
| PTZ move/stop | `GET /{apiKey}/control/{ke}/{mid}/{direction}` | ✅ verified live against physical ONVIF camera (`zoom_in`, full SOAP ContinuousMove→Stop round-trip, `ok: true`). Gated by monitor detail `control === "1"`. |
| PTZ set home | `GET /{apiKey}/control/{ke}/{mid}/setHome` | ✅ available (same route, `direction=setHome`) |
| Videos, date-windowed | `GET /{apiKey}/videos/{ke}/{mid}?start=&end=&noLimit=1` | ✅ verified (39 clips over a 30-day window) |
| Timelapse frame lookup | `GET /{apiKey}/timelapse/{ke}/{mid}?start=&end=&limit=1` | ✅ verified — returns frame metadata (`filename`) for a time window |
| Timelapse frame image | `GET /{apiKey}/timelapse/{ke}/{mid}/{YYYY-MM-DD}/{filename}` | ✅ verified (image/jpeg) |
| Latest timelapse frame | `GET /{apiKey}/timelapse/{ke}/{mid}?limit=1` (no start/end) | ✅ verified — newest-first ordering confirmed, so this is the single most recent frame, not the oldest |
| Events, windowed + custom details | `GET /{apiKey}/events/{ke}/{mid}?start=&limit=` | ✅ verified |
| Synthetic event injection | `GET /{apiKey}/motion/{ke}/{mid}?data=<json>` | ✅ verified — `req.query.data` is assigned directly as the created event's `details`, so an arbitrary `matrices` array can be injected for testing without a real detector/MQTT setup |

### Gotchas found while implementing (all fixed)
- **Shinobi bug:** `libs/control/ptz.js`'s `ptzControl()` does a bare `return`
  (not calling `callback`) when `monitor.details.control !== "1"`. The HTTP
  route only calls `res.end()` from inside that callback, so a PTZ request to
  a control-disabled monitor **hangs until client timeout** instead of
  erroring. Confirmed: a `zoom_in` call to the disabled-control monitor timed
  out at 15s with zero response bytes. Our integration checks
  `monitor.details.control == "1"` client-side *before* ever issuing the
  request — both the `button` platform (only creates PTZ buttons for
  PTZ-enabled monitors) and the `shinobi.ptz` service (raises immediately,
  confirmed 0.25s fail vs a 15–20s hang).
- **Our bug (not Shinobi's):** `ShinobiClient._get_json` hand-built query
  strings by f-string concatenation. ISO-8601 date values contain `+`/`:`;
  passed unescaped in a raw URL string, aiohttp/yarl leaves `+` untouched,
  and Express's query parser then decodes a literal `+` as a space
  server-side — silently corrupting the date and making Shinobi return zero
  matches (no error, just an empty result set, which is what made this hard
  to notice). Fixed by switching to aiohttp's native `params=` dict on
  `session.get()`, which encodes correctly. Caught by testing the real
  30-day video window query end-to-end over HA's websocket API and seeing
  `0` days back before the fix vs. 4 correctly-bucketed days after.

Monitor object fields used: `ke, mid, name, status, mode, type, ext, streams[],
width, height, fps, snapshot`.
`status` values seen: `Watching`, `Idle`, `Disabled`, `Recording`.

---

## Feature matrix

Legend: ✅ done · 🟡 partial / planned · ⬜ not started · 🟠 out of scope for v1

### Known issue: HA stream dialog jitter
HA's built-in camera stream dialog runs `stream_source()` (Shinobi's HLS) through
HA's own `stream` component, which demuxes and re-muxes into a second HLS output
for the frontend player. That extra hop is where the jitter reported on the Pi 3B
comes from — confirmed by pulling Shinobi's raw HLS playlist directly: segments
land every ~2.00s with consistent `EXTINF` durations and an unbroken media
sequence, i.e. the source stream itself is not stuttering.

**Fix shipped:** `camera.<name>` now exposes an `embed_url` attribute
(`/{apiKey}/embed/{ke}/{mid}/fullscreen|jquery|gui`) — Shinobi's own embeddable
player page. It runs hls.js/flv.js client-side in the browser, so playback goes
straight from browser to Shinobi with **no HA-side re-mux**.

**Gotcha found & fixed:** the bare `/embed/{ke}/{mid}` URL (no addon segment)
renders HTTP 200 but the player never actually initializes — `bs5.embed.js`
references jQuery at module load time (`$('#monitors_live')`) and throws
immediately if it isn't loaded, since Shinobi only loads jQuery when the
`jquery` addon flag is present (see `web/pages/embed.ejs`). `embed_url` now
always appends `fullscreen|jquery|gui`. Verified: fetched the addon'd URL and
confirmed `jquery.min.js`, `bs5.embed.gui.css`, and the fullscreen CSS/JS all
appear in the response with correct absolute URLs.

Drop the attribute value straight into a Lovelace **Webpage** card:

```yaml
type: iframe
url: http://<shinobi-host>:8080/<api_key>/embed/<group_key>/CAM1/fullscreen|jquery|gui
aspect_ratio: 56%
```

`stream_source()` is kept as-is for casting and the media-browser
thumbnail-expand dialog, where the extra hop is less noticeable.

**Also fixed:** entity device_info set `via_device` pointing at a hub device
that was never registered — logged an HA deprecation warning ("will stop
working in 2025.12.0"). `__init__.py` now registers a proper hub device
(one per Shinobi server) via `device_registry.async_get_or_create`, with each
monitor's device linked to it. Verified clean logs after restart.

### Dynamic monitor discovery
Shinobi has no "monitor added" push notification — the coordinator just sees
new monitor IDs on its next poll. Originally, entity platforms only ever
called `async_add_entities()` once, at integration setup, so a monitor added
to Shinobi after HA was already running stayed invisible until the
integration was reloaded.

**Fixed:** `entity.py` now has `async_add_monitor_entities()`, used by all
five platforms (camera, binary_sensor, switch, sensor, button). It creates
entities for currently-known monitors immediately, then registers a
`coordinator.async_add_listener()` callback that re-diffs
`coordinator.data["monitors"]` against a `known_monitor_ids` set on *every*
coordinator refresh (default every 10s) and adds entities only for genuinely
new IDs — no polling of its own, no duplicate entities, no reload needed.

Verified against a real monitor (`CAM3`) added to the live dev server
mid-session:
- Got its full, correctly-gated 13-entity set (camera, motion, status, 2
  switches, 8 PTZ buttons — `control: 1` on this one) automatically
- Forced an extra coordinator refresh via `homeassistant.update_entity`
  (no HA/integration restart) and confirmed the entity count stayed at
  exactly 13 — the dedup against `known_monitor_ids` doesn't create
  duplicates on repeat refreshes

**Known limitation:** if an *existing* monitor's PTZ `control` flag is
toggled on/off after its entities were already created, the button platform
won't retroactively add/remove PTZ buttons for it without a reload — only
monitor IDs appearing/disappearing are picked up dynamically, not field
changes on a monitor that's already represented in HA.

### Stale monitor removal (deleted from Shinobi) — ✅ DONE
Originally, deleting a monitor in Shinobi just left its HA entities stuck on
`unavailable` forever (see previous section for why: `available` correctly
goes `False` once the monitor drops out of `coordinator.data`, but nothing
called `entity_registry`/`device_registry` removal). Symmetric fix, shipped:

`__init__.py`'s `_async_register_stale_monitor_cleanup()` runs on every
coordinator refresh. It diffs each per-monitor **device** in the registry
against the latest polled monitor IDs; anything no longer present gets
`device_registry.async_remove_device()`'d, which cascades to remove all of
that device's entities automatically. The now-stale ID is also discarded
from every platform's own "already added" tracker (`coordinator.
monitor_id_trackers`, a list of set-references each `async_add_monitor_
entities()` call registers) — otherwise, if a monitor is deleted and later
re-created with the same ID, every platform would still think it "already
handled" that ID and would never re-add entities for it. The hub device
(the Shinobi server itself) is explicitly exempted and never pruned.

Verified live: deleted `CAM3` from Shinobi
(`GET /{apiKey}/configureMonitor/{ke}/{id}/delete`, config only, recordings
kept) while HA kept running. One poll cycle later:
- All 13 of its entities were fully gone from `/api/states` (not just
  `unavailable` — actually removed)
- Device registry confirmed exactly 3 Shinobi devices remained: the hub +
  the 2 real monitors — `CAM3`'s device was gone, hub untouched
- The two remaining monitors' entities (13 for the PTZ one, 5 for the
  non-PTZ one) were completely unaffected — no collateral pruning

### Entities
- [x] ✅ **Camera entity** per monitor (HLS stream + JPEG snapshot)
- [x] ✅ **Binary sensor** — per-camera motion/occupancy (from events API)
- [x] ✅ **Switch** — enable/disable camera (mode start ↔ stop)
- [x] ✅ **Switch** — recording on/off (mode record)
- [x] ✅ **Sensor** — monitor status (Watching/Idle/Recording/Disabled)
- [x] ✅ **Dynamic discovery** — new monitors get entities within one poll
      cycle, no reload required (see above)
- [x] ✅ **Stale removal** — deleted monitors get their device/entities
      removed within one poll cycle (see above)
- [x] ✅ **Sensor — object counts** (`sensor.<monitor>_object_count`).
      Tallies `details.matrices[].tag` across events in a rolling lookback
      window (default 24h, `object_count_hours` option, min 1/max 168) —
      **but only for events where `details.reason == "object"`**. This
      isn't an arbitrary filter we invented: it's Shinobi's own convention.
      `libs/videos.js`'s `listOTags` (which builds the "Objects Found"
      column shown in `bs5.videosTable.js`) filters
      `row.details.reason === 'object'` before reading `details.matrices`,
      and Shinobi's reference OpenCV detector (`test/opencvMotionTest.js`)
      always sets `reason: 'object'` alongside `matrices` when reporting a
      detection. Events that carry a `matrices` array for some other reason
      (e.g. relayed external-detector zone events via MQTT, whose `reason` is
      the zone name — see `dropInEvents/mqtt.js`'s MQTT-relay handling) are
      intentionally excluded, matching what Shinobi itself counts as an
      "object" event rather than just anything with matrices.

      State = total qualifying detections; `counts_by_tag` attribute has the
      per-tag breakdown. A fresh windowed `/events` query per monitor can
      return a lot of rows, so this only recomputes every 30th coordinator
      poll (~5 min at the default 10s scan interval,
      `OBJECT_COUNT_RECOMPUTE_EVERY_N_POLLS`) plus immediately on the first
      poll after startup/reload — other polls serve the cached value.
      Row-capped at 500 regardless of window length.

      Real ONVIF motion events (what both dev cameras produce) carry no
      `matrices`/`reason: "object"` — only Shinobi's own object detector or
      a compatible custom detector populates it this way — so this reads 0
      on an unmodified dev setup. **Verified with injected data**: used
      `GET /{apiKey}/motion/{ke}/{id}?data=<json>` (a documented but
      undersung feature of the motion-trigger route — `req.query.data` is
      assigned directly as the event's `details`) to inject two events: one
      *without* `reason: "object"` (2 "person" + 1 "car") and one *with* it,
      matching the reference detector's shape (1 "person" + 2 "dog").
      Restarted HA (forces an immediate recompute on poll #1) and confirmed
      `sensor.tapo_c200c_object_count` read exactly `state: "3"`,
      `counts_by_tag: {"person": 1, "dog": 2}` — the first event's car and
      extra person were correctly excluded for lacking `reason: "object"`.
      (Note: firing two such synthetic triggers back-to-back on the same
      monitor silently dropped the second one — some Shinobi-side
      debounce/lock beyond the documented `detector_timeout`; a 2s gap
      wasn't enough either, several seconds was. Untriaged since it didn't
      block verification, but worth knowing if scripting test events.)
- [x] ✅ **Image entity — latest timelapse frame** (`image.<monitor>_
      latest_frame`). Distinct from the camera entity's live snapshot: shows
      Shinobi's most recently *captured* timelapse frame, not the current
      live frame. `state` is the frame's own timestamp (drives HA's
      image-changed detection); only bumps when the frame's filename
      actually changes, not on every poll (frames are captured every
      ~15 min in observed data, far less often than the default 10s scan
      interval). A bare `?limit=1` timelapse query (no start/end) is
      confirmed to return newest-first, so "latest frame" is a single cheap
      row lookup, fetched every poll (unlike the heavier object-count
      query). Correctly `unavailable` on `CAM2`, which genuinely
      has zero timelapse frames (verified: `/timelapse/.../CAM2`
      returns an empty list) — not a bug, there's nothing to show.
      Verified the image proxy serves real bytes:
      `/api/image_proxy/image.cam1_latest_frame?token=...` → 200,
      `image/jpeg`, byte-for-byte the same size as the source frame.
- [ ] 🟡 **Binary sensor** — per-object-type (person/car/…) presence, distinct
      from the count sensor above (would need its own timeout/reset logic
      per tag, similar to the motion binary sensor)
- [ ] 🟡 **Sensor** — server/detector performance (FPS, CPU) via health API

### Media & viewing
- [x] ✅ Snapshot proxying through HA (no direct Shinobi exposure)
- [x] ✅ **Media browser** — `media_source.py`: Server → Camera → Day → Clips.
      Verified live over HA's websocket API: root → 1 server node → 2 monitor
      nodes → day nodes with correct per-day clip counts (1/3/1/34 clips across
      4 days) → clip nodes with real timelapse-frame thumbnails → resolves to
      a direct, fetchable `video/mp4` URL (200, correct content-type).
      Day-bucketing is client-side over a 30-day rolling window (Shinobi has
      no "list distinct days" endpoint — `bs5.calendar.js` does the same
      thing); clips per day capped at 100. Deep history beyond 30 days isn't
      browsable in v1.
- [x] ✅ **Media browser** — event/snapshot thumbnails (via timelapse-frame
      lookup, see gotcha above; falls back to no thumbnail if the frame
      lookup comes up empty for a given clip)
- [ ] 🟠 Preview GIFs (not native to Shinobi)
- [ ] 🟡 Media casting (HLS URL to cast devices)

### Automation & control
- [x] ✅ **Service** — `set_mode` (start/record/stop/idle)
- [x] ✅ **Service** — `trigger_motion` (external motion trigger)
- [x] ✅ **Service** — `shinobi.ptz` (up/down/left/right/zoom_in/zoom_out/
      center/stopMove/setHome), guarded against the Shinobi hang bug above
- [x] ✅ **PTZ buttons** — `button.py`, one set of 8 per PTZ-enabled monitor
      (Up/Down/Left/Right/Zoom In/Zoom Out/Home/Set Home), only created for
      monitors with `details.control === "1"`. Verified live: exactly 8
      buttons created, all on `CAM1` (control enabled), none on
      `CAM2` (control disabled). Button press → real ONVIF
      ContinuousMove→Stop SOAP round-trip against the physical Tapo camera,
      confirmed `ok: true`.
- [x] ✅ Real-time motion via event polling coordinator
- [ ] 🟡 Real-time via WebSocket (socket.io) or MQTT (lower latency)
- [ ] 🟡 Notification recipe/blueprint (snapshot + clip + deep link)

### Integration plumbing
- [x] ✅ Config flow (UI setup: host, port, ssl, API key, group key)
- [x] ✅ Auto-discovery of monitors → entities
- [x] ✅ DataUpdateCoordinator (single poll, shared state)
- [x] ✅ Device registry (one HA device per monitor)
- [ ] 🟡 Options flow (poll interval, stream format, motion timeout)
- [ ] 🟡 Multi-instance (multiple Shinobi servers) — config-entry ready, untested
- [ ] 🟠 Companion Lovelace card (custom camera card)

---

## Deployment tasks (dev Raspberry Pi @ <shinobi-host>)  ✅ DONE

- [x] SSH connectivity confirmed (Raspberry Pi 3B / Debian 13 / arm64)
- [x] Home Assistant installed — **Docker container** `homeassistant`
      (`ghcr.io/home-assistant/home-assistant:stable`, host network, port **8123**,
      config mounted from the host, TZ set to local timezone)
- [x] `custom_components/shinobi` copied into HA config dir
- [x] HA onboarded (admin user) + integration added via config flow → <shinobi-host>:8080
- [x] Both monitors live in HA: `CAM1` (PTZ-enabled) + `CAM2`
      → 38 entities (2× camera/motion/status/enabled/recording + 8 PTZ
      buttons on CAM1 only)
- [x] Camera snapshots proxy through HA (`/api/camera_proxy/...` → 200 image/jpeg)
- [x] Motion binary sensor flips on real ONVIF events
- [x] `shinobi.trigger_motion` service round-trips to Shinobi (HTTP 200)
- [x] `shinobi.ptz` service + PTZ buttons verified against the physical camera
- [x] Media browser tree verified over HA's websocket API (browse + resolve)

### Access
- Home Assistant UI: `http://<ha-host>:8123` (credentials set during onboarding)
- Container mgmt: `sudo docker {logs|restart|stop} homeassistant`
- Redeploy module: `pscp -r custom_components/shinobi <user>@<shinobi-host>:<ha-config-path>/custom_components/` then restart container

---

## Overall feature completeness
The bulk of a full-featured NVR integration is done: live view, media
browsing, PTZ, dynamic monitor sync, object counts, and real-time push
notifications. The remaining gaps are downstream of Shinobi being
**capture-first** rather than **detection-first**: granular per-zone/per-object
stateful sensors and detector-performance metrics must be synthesized in the
integration rather than passed through.

## v1 scope (this pass)
Camera + motion binary sensor + status sensor + enable/record switches +
`set_mode`/`trigger_motion`/`ptz` services + PTZ buttons + media browser
(Server → Camera → Day → Clips), wired through a polling coordinator and UI
config flow. Everything marked 🟡 above is the backlog.
