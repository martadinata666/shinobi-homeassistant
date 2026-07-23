# Shinobi NVR — Home Assistant Integration

A custom [Home Assistant](https://www.home-assistant.io/) integration for the
[Shinobi](https://shinobi.video/) open-source NVR.

See [WIP.md](WIP.md) for the full feature-parity matrix and roadmap.

## Features (v0.1.0)

- **Camera** entity per monitor — live HLS stream + JPEG snapshot, proxied through HA
- **Image** entity per monitor — Shinobi's latest *captured* timelapse frame (distinct
  from the camera's live snapshot)
- **Binary sensor** (motion) per monitor — derived from Shinobi's detection/ONVIF events
- **Sensor** (status) per monitor — `Watching` / `Recording` / `Idle` / `Disabled`
- **Sensor** (object count) per monitor — total detections over a rolling lookback
  window, tallied by tag (person/car/etc.) from event detection data
- **Switches** per monitor — *Enabled* (start ↔ stop) and *Recording* (record ↔ watch)
- **Media browser** — Shinobi appears as a source in HA's Media Browser:
  Camera → Day → Clips, with thumbnails and direct playback
- **PTZ buttons** — Up/Down/Left/Right/Zoom In/Zoom Out/Home/Set Home, created
  automatically for any monitor with PTZ control enabled in Shinobi
- **Services**
  - `shinobi.set_mode` — set a monitor to `start` / `record` / `stop` / `idle`
  - `shinobi.trigger_motion` — fire an external motion trigger
  - `shinobi.ptz` — move a PTZ camera (same directions as the buttons)
- **UI config flow** with auto-discovery of monitors, options for poll interval &
  motion reset timeout, and multi-server support.
- **Dynamic monitor discovery** — add a camera in Shinobi and it gets entities
  in HA automatically within one poll cycle, no restart or reload needed
- **Stale monitor removal** — delete a camera in Shinobi and its device +
  entities are removed from HA within one poll cycle, not left `unavailable`
- **Real-time push notifications** — `shinobi_detector_trigger` HA event fires
  instantly on motion/object detection, over Shinobi's own socket.io server
  (no MQTT broker to configure), with a best-effort snapshot for object
  detections

## Requirements

- Home Assistant 2024.1.0 or newer
- A reachable Shinobi server and an API Key — see
  [Creating an API Key](#creating-an-api-key) below for exactly which
  permissions it needs
- The Group Key (`ke`) the API Key belongs to

## Installation

### Manual
Copy the `custom_components/shinobi` folder into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

### HACS (custom repository)
Add this repository as a custom integration repository in HACS, install, restart.

## Creating an API Key

Create a dedicated API Key for Home Assistant rather than reusing an existing
one, so its permissions can be scoped to exactly what the integration uses.
In Shinobi: **Account → API**. Every permission below was confirmed against
the actual route handlers in Shinobi's source, not assumed from the
permission's name:

| Permission | Needed for |
|---|---|
| **Get Monitors** | Required for the integration to load at all, and to discover new/removed cameras |
| **Control Monitors** | Enabled/Recording switches, `set_mode` service, PTZ buttons + `ptz` service, `trigger_motion` service |
| **Watch Stream** | Camera entity's live view — both HA's built-in stream dialog and the `embed_url` attribute |
| **Watch Snapshot** | Camera entity's still picture |
| **Watch Videos** | Despite the name, also gates detection events (Motion binary sensor + Object Count sensor both read Shinobi's events endpoint), the Media Browser's recordings list, and the Latest Frame image entity (both read Shinobi's timelapse-frame index) |
| **Auth via Socket** | *Optional* — real-time push notifications only. Without it, Shinobi rejects the socket.io connection and the integration silently falls back to polling only (a warning is logged, nothing breaks) |
| **Create API Keys** | *Optional, non-obvious* — real-time push notifications only. Shinobi's "get one API key's own details" endpoint is gated behind this permission even for a read-only lookup of your own key; the integration calls it once at startup to resolve the key's own `uid`, which Shinobi's socket.io handshake requires alongside the key itself |

Not needed for anything the integration does: Delete Videos, Edit Monitors,
Get Alarms, Edit Alarms, Get Logs.

## Setup

1. In Shinobi, grab your **API key** (Account → API) and your **Group key** (`ke`).
2. In Home Assistant: **Settings → Devices & Services → Add Integration → Shinobi NVR**.
3. Enter host, port (default `8080`), API key, and Group key.
4. Every monitor Shinobi returns for that Group Key is created as a device
   automatically — no per-camera setup step.

## Displaying cameras

Each monitor becomes a standard Home Assistant `camera` entity: live view
through HA's built-in stream dialog, a JPEG snapshot as the entity picture,
and casting support.

### Smoother live view (avoiding stream jitter)

The camera's built-in "live" dialog in HA plays `stream_source()` (Shinobi's HLS)
through HA's own `stream` component, which re-muxes it into a second HLS output
for the frontend. On modest hardware (e.g. a Raspberry Pi) that extra hop can look
jittery even though Shinobi's own output is smooth.

Every `camera.<name>` entity exposes an **`embed_url`** attribute — Shinobi's own
embeddable player page. It runs hls.js/flv.js directly in your browser, so there's
no re-muxing on the HA side at all.

```
http://[SHINOBI HOST]:[PORT]/[API KEY]/embed/[GROUP KEY]/[MONITOR ID]/fullscreen|jquery|gui
```

- `jquery` — **required**. The embed player script references jQuery at load
  time and never starts without it; Shinobi only loads jQuery when this flag
  is present.
- `fullscreen` — adds a fullscreen toggle button, useful inside an iframe.
- `gui` — adds the stream-chrome CSS (timestamp overlay, unmute button).

If the embed page loads but the video never starts, and the server is reached
through a reverse proxy or a different hostname than Shinobi auto-detects, add
`relative` to the addon list and a `?host=` query parameter pointing at the
address the *browser* should use to reach Shinobi:

```
http://[SHINOBI HOST]:[PORT]/[API KEY]/embed/[GROUP KEY]/[MONITOR ID]/fullscreen|jquery|gui|relative?host=http://[SHINOBI HOST]:[PORT]/
```

## Keeping the camera list in sync

Shinobi has no push notification for "a monitor was added" or "a monitor was
deleted", so this is handled by polling — the following happens
automatically, without restarting Home Assistant or reloading the
integration:

- **New monitor added in Shinobi** — gets its full set of entities (camera,
  image, sensors, switches, and PTZ buttons if applicable) within one poll
  cycle (default 10 seconds).
- **Monitor deleted in Shinobi** — its device and every entity belonging to
  it are removed from Home Assistant within one poll cycle. They are not
  merely marked `unavailable`; the device disappears from Settings → Devices.

One case is *not* automatic: changing a setting on a monitor that Home
Assistant already knows about — most notably, turning PTZ **Control** on or
off for an existing monitor. Since Home Assistant only decides whether to
create PTZ buttons the first time it sees a given monitor id, toggling
Control later does not retroactively add or remove them. To pick up that
kind of in-place change, reload the integration:

- **Settings → Devices & Services → Shinobi NVR → ⋮ → Reload**.

## Building a camera dashboard card

A clean per-camera card — embedded live stream on top, a row of tiles for
status and controls below — is a `vertical-stack` with a **Webpage**
(`iframe`) card followed by a **Grid** of **Tile** cards. Use the camera's
own `embed_url` attribute for the iframe, and swap `your_camera_name` below
for the actual entity id slug shown in Developer Tools → States.

```yaml
type: vertical-stack
cards:
  - type: iframe
    url: >-
      http://[SHINOBI HOST]:[PORT]/[API KEY]/embed/[GROUP KEY]/[MONITOR ID]/fullscreen|jquery|gui
    aspect_ratio: 56%
  - type: grid
    columns: 2
    square: false
    cards:
      - type: tile
        entity: switch.your_camera_name_recording
      - type: tile
        entity: switch.your_camera_name_enabled
      - type: tile
        entity: sensor.your_camera_name_status
      - type: tile
        entity: binary_sensor.your_camera_name_motion
```

For several cameras, adding one card per camera by hand gets old fast. On an
editable dashboard, use the **⋮ menu → Edit in YAML** (view-level, not
per-card) and paste one `vertical-stack` block like the one above per camera.

## Media browser

Open **Media → Shinobi NVR** in Home Assistant to browse recordings by camera and
day. Day-bucketing is computed client-side over a rolling 30-day window (Shinobi
doesn't have a "list distinct days" endpoint — its own web UI does the same
thing), and each day is capped at 100 clips. Thumbnails come from Shinobi's
timelapse-frame index rather than a stored per-video snapshot, matching how
Shinobi's own media table (`bs5.videosTable.js`) sources them; a clip without a
nearby timelapse frame shows without a thumbnail rather than failing.

## PTZ

Any monitor with **Control** enabled in Shinobi's monitor settings (ONVIF PTZ)
gets a set of button entities: Up, Down, Left, Right, Zoom In, Zoom Out, Home,
and Set Home. A single press is a complete action — Shinobi handles the
continuous-move-then-auto-stop timing server-side, so there's no need to hold
the button down. The `shinobi.ptz` service exposes the same directions plus
`stopMove` (abort an in-progress move) for scripting/automations.

PTZ entities and the service both check the monitor's control flag before
issuing a command. Shinobi's own `/control/{ke}/{mid}/{direction}` endpoint has
a bug where it never responds at all (hangs until timeout) if control isn't
enabled on that monitor, instead of returning a clean error — this check exists
specifically to avoid tripping that.

## Object counts

`sensor.<name>_object_count` totals detections over a rolling lookback window
(default 24h, adjustable via the integration's **Options**). Its state is the
total; the `counts_by_tag` attribute breaks it down (e.g.
`{"person": 2, "car": 1}`). Only events with `details.reason == "object"` are
counted — that's Shinobi's own convention for "this event is an object
detection" (see `libs/videos.js`'s `listOTags`), not a filter we invented, so
events that happen to carry `matrices` for some other reason (e.g. relayed
zone-crossing events) aren't miscounted. Plain ONVIF motion events carry
neither, so the sensor reads 0 on a motion-only setup. Because a windowed
events query can return a lot of rows, this recomputes every ~5 minutes
rather than on every poll; other polls serve the last computed value.

## Latest-frame image

`image.<name>_latest_frame` shows Shinobi's most recently *captured*
timelapse frame — a periodic capture, not a live/current snapshot (that's
what the camera entity's picture already shows). Useful for a "last seen"
style dashboard tile. Stays `unavailable` on a monitor with no timelapse
frames yet.

## Real-time push notifications

Home Assistant fires a `shinobi_detector_trigger` event on the bus for every
motion/object detection, in real time — not on the polling interval. This
uses a mechanism already built into Shinobi for exactly this purpose:
`libs/socketio.js`'s `detector_on` socket command joins a per-monitor
notification room ("Used by mobile clients for push/local notifications,"
per its own comment) with nothing more than an authenticated connection —
no MQTT broker, no topic configuration, no extra setup in Shinobi's UI at
all. The integration connects, authenticates, and subscribes automatically
for every monitor (including ones added later).

Event data: `monitor_id`, `time`, `reason`, `confidence`, `matrices`
(per-detection tags/boxes), `do_object_detection`, and `snapshot_url`.
Shinobi's own broadcast doesn't include a snapshot, so for genuine object
detections (`reason == "object"`) the integration searches Shinobi's
timelapse-frame index around the event's own timestamp — the moment of
detection, not "right now." This is best-effort: on a monitor with a sparse
timelapse-capture interval, `snapshot_url` will often be `null`; configure a
shorter interval on monitors you want reliable notification images from.

Use it in an automation like:

```yaml
trigger:
  - platform: event
    event_type: shinobi_detector_trigger
    event_data:
      reason: object
condition:
  - condition: template
    value_template: "{{ 'person' in trigger.event.data.matrices | map(attribute='tag') | list }}"
action:
  - service: notify.mobile_app_your_phone
    data:
      message: "Person detected on {{ trigger.event.data.monitor_id }}"
      data:
        image: "{{ trigger.event.data.snapshot_url }}"
```

## Troubleshooting

- **Embed iframe loads but stays blank** — the `jquery` addon flag is
  missing from the URL. `embed_url` already includes it; this only matters
  if the URL was built by hand.
- **A recently added camera has no entities yet** — wait one poll cycle
  (default 10 seconds), or lower the polling interval in the integration's
  Options.
- **A camera's PTZ buttons didn't appear after enabling Control in Shinobi**
  — reload the integration (see
  [Keeping the camera list in sync](#keeping-the-camera-list-in-sync) above).
- **`shinobi_detector_trigger` never fires** — confirm the API Key has
  **Auth via Socket** enabled in Shinobi (Account → API); Home Assistant logs
  a warning at startup if it cannot resolve a uid for the key.
- **`snapshot_url` is often `null`** — expected on a monitor with a sparse
  timelapse-capture interval; Home Assistant only searches a window around
  the detection's own timestamp, it does not take a fresh snapshot.
  Configure a shorter timelapse interval on monitors used for notification
  images.

## How it works

The integration is `local_polling` for entity state: a single
`DataUpdateCoordinator` polls `/{apiKey}/monitor/{ke}` and
`/{apiKey}/events/{ke}` on an interval (default 10 s), shares one payload
across all entities, and derives motion state by comparing the newest event
timestamp against a configurable reset timeout. Snapshots and streams are
served from Shinobi's `jpeg` and `hls` endpoints. Real-time detection
notifications are supplementary and push-based over socket.io — see above.
