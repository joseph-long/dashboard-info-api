# Dashboard info API

## Concepts

### Device

Each device has an identifier presented at enrollment and when requesting its dashboard info (MTA departures, display schedule, word of the day).

The last request timestamp is stored with the device row.

It also references one or more configurations.

### Display schedule

A device also carries an optional, device-wide display schedule: a commute-mode
window, a dim window (with a brightness level), and an off window, each expressed
as a `"HH:MM"` (24-hour) start/end pair evaluated in America/New_York. Any window
left unset (both bounds blank) is disabled. A window may wrap past midnight (e.g.
off from `23:00` to `06:00`).

This only matters for **self-luminous displays** — the waveshare-rgb-matrix LED
firmware evaluates these windows locally (against its own NTP clock) to flip
between the minutes-countdown and a fixed arrival time during commute mode, and to
dim/blank the panel outside viewing hours. ePaper devices have no backlight to dim
and never call `GET /public/devices/{device_id}/schedule` at all.

### Configuration

A configuration includes the following properties:

 - origin station
 - MTA service letter/number
 - direction (uptown/downtown)
 - (optional) destination station, destination walk time (min)

## Interactions

The web UI is only accessible on LAN and VPN and no user authentication / multi-tenancy infrastructure is required.

### List devices

A list of devices linking to the configuration page for each, the 'view dashboard' preview for each, a 'Delete' button for each, and a button to enroll a new device.

### Enroll new device

User takes identifier reported by device and enters into a web form.

New row is stored for this device.

A default configuration row is populated and pointed at the device identifier.

### Delete device

Removes the device row and its configurations (`DELETE /api/devices/{device_id}`).
Confirmed client-side before the request is sent; irreversible once it lands, since
a deleted device's id can simply be re-enrolled with none of its history.

### Configure device

All configurations that are used by this device through a join table are shown for editing in-page.

Only one configuration per service is allowed, i.e. `(device_id, service)` must be unique together.

Include a link to preview the dashboard in-browser.

### View dashboard

For a given device, show the next `max_departures` (query parameter, default = 3) departures in a format (two rows, index, service, terminus, minutes-to-arrive) inspired by MTA departures boards. Update with polling, as embedded clients would. With `debug=1`, additionally pretty-print the raw JSON the API returned below the board.

## API

Every route a device calls unauthenticated over the open internet lives under
`/public/devices/{device_id}/...` — the device id is a path segment, not a
header. This lets the reverse proxy in front of the app allow-list the whole
`/public/` prefix once; a new device-facing route never needs an infra change
to become reachable (see the `dashboard-info-api.nix` module in the infra repo).
Everything else (enrollment, configure pages, the dashboard preview, and
their backing APIs) is restricted to LAN/VPN by that same proxy.

The device is responsible for formatting/presentation. To show the next `max_departures` (query parameter, default = 3) departures, it needs the following information for each:

```json
{
  "service": "N",
  "terminus": "Coney Island–Stillwell Av",
  "arrives_at": "2026-09-13T17:08Z",
  "stops_at_destination": true,
  "reach_destination_at": "2026-09-13T17:42Z"
}
```

 - `service` - string - MTA service code as used by the GTFS data, e.g. `"6X"` for "6 express"
 - `terminus` - string - final destination of the train
 - `arrives_at` - ISO 8601 timestamp string - time of arrival at the configured origin station
 - `stops_at_destination` - boolean, optional - when a destination station is configured, this key is present and indicates whether the scheduled train is making its usual stop at that station (only trains heading in the configured direction are ever listed, so `false` really means the train skips the destination)
 - `reach_destination_at` - ISO 8601 timestamp string, optional - when a destination station is configured and `stops_at_destination` is `true`, this is the time to arrive at the destination (including any walk time after reaching the station)

This is `GET /public/devices/{device_id}/departures`; the device identifies itself
with its id in the URL, not a header. The resulting list of departures is the
union of matching departures for all the configurations.

When a device polls the API, the last request timestamp in its row is updated.

### `GET /public/devices/{device_id}/schedule`

Same no-auth-beyond-the-path-id shape as departures. Returns the device's raw
display schedule config, verbatim — not a computed "is commute mode active
right now" boolean, since the device already has an NTP-synced clock and can
evaluate the (possibly overnight-wrapping) windows itself:

```json
{
  "commute_start": "07:00",
  "commute_end": "09:30",
  "dim_start": "20:00",
  "dim_end": "23:00",
  "dim_brightness": 40,
  "off_start": "23:00",
  "off_end": "06:00"
}
```

Any/all fields are `null` if that window is unset. Set via
`PUT /api/devices/{device_id}/schedule` from the configure page (same shape, all
fields optional).

## Running

```sh
uv sync --extra dev
uv run dashboard-info-api            # serves http://0.0.0.0:8000 (override with --host/--port)
```

State (SQLite database + cached static GTFS data) lives in `./var`; override with
`DASHBOARD_STATE_DIR`. On first run the static GTFS data is fetched from the MTA in the
background so the configure page has stations to pick from.

The web UI is at `/`: enroll a device, configure it at `/devices/<id>/configure`,
and preview its board at `/devices/<id>/dashboard`. Devices poll
`GET /public/devices/<id>/departures?max_departures=N`.

## Development

```sh
uv run pytest                  # unit tests (src/dashboard_info/tests)
uv run pytest tests_e2e        # browser e2e tests (Playwright, Firefox)
```
