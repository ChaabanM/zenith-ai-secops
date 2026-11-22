# Event schema

Implementation: [`backend/app/schemas/events.py`](../backend/app/schemas/events.py) (input validation) and
[`backend/app/services/ingestion/records.py`](../backend/app/services/ingestion/records.py) (warnings, normalization).

```json
{
  "event_id": "evt-123",
  "source_type": "radar",
  "device_id": "radar-07",
  "event_type": "speed_violation",
  "occurred_at": "2026-01-15T10:12:04Z",
  "received_at": "2026-01-15T10:12:18Z",
  "location": { "latitude": 25.2048, "longitude": 55.2708, "zone": "Gate-A" },
  "severity": "high",
  "confidence": 0.94,
  "payload": {}
}
```

## Fields

| Field | Required | Type and rules |
|---|---|---|
| `event_id` | **mandatory** | String, 1–128 characters, no whitespace inside (surrounding whitespace is trimmed). Producer's identifier, stored unchanged. |
| `source_type` | **mandatory** | One of the 15 canonical sources or a documented alias (see below). Case, spaces, `-`, `/` and `.` are ignored. |
| `device_id` | **mandatory** | String, same rules as `event_id`. Numbers are rejected, not coerced. |
| `event_type` | **mandatory** | Free-text label, 1–128 characters. It is not limited to a fixed list, so new sensor event types need no schema change. |
| `occurred_at` | **mandatory** | ISO-8601 string **with a timezone offset** (`Z` or `±hh:mm`). Converted to UTC. |
| `received_at` | optional | Same format as `occurred_at`. |
| `location` | optional | Object with `latitude` (−90..90) and `longitude` (−180..180) given **together**, and/or a `zone` label. |
| `severity` | optional | `critical`, `high`, `medium`, `low`, `informational` (case-insensitive; `info` is accepted as an alias). |
| `confidence` | optional | JSON number from 0 to 1, bounds included. Strings, booleans, NaN and Infinity are rejected. |
| `payload` | optional | JSON object holding source-specific fields. Its contents are not validated, so the payload can be extended per source. |

Unknown top-level fields are ignored for normalization. They remain in the raw evidence, and the record gets the warning `unknown_fields_ignored`.

Rejected timestamps include `15/01/2026 10:12`, `2026-02-30T10:00:00Z`, `2026-01-15T10:12:04` (no offset, so the local time is ambiguous), date-only values, and epoch numbers.

## Invalid vs suspicious

| Situation | Outcome | Why |
|---|---|---|
| Missing mandatory field, wrong type, unparseable or offset-less timestamp, coordinates out of range, confidence outside 0–1, unknown source, payload not an object, malformed JSON | **invalid**: raw record kept, no normalized event | The record cannot be interpreted reliably. |
| `received_at` much later than `occurred_at` (delayed) | accepted; `delay_seconds` stored | A real observation that arrived late. This is a quality signal for Phase 3. |
| `received_at` earlier than `occurred_at` | accepted; warning `received_before_occurred`; negative `delay_seconds` | Structurally valid, but suggests clock skew. Kept as suspicious. |
| Low `confidence` | accepted, no warning | A valid but uncertain detection. Phase 3 rates it. |
| Missing optional field | accepted; stored as `null` (payload `{}`); warning such as `missing_received_at`, `missing_location`, `missing_coordinates`, `missing_zone`, `missing_severity`, `missing_confidence` | Unknown values are never guessed. |
| Alias used for `source_type` | accepted; warning `source_alias_normalized` | The canonical value is stored and the original spelling stays in the evidence. |
| Same `event_id` as an earlier valid submission | `duplicate` (`exact` or `conflicting`) | See [ingestion.md](ingestion.md#duplicate-handling-event-id-uniqueness-contract). |

## Sources and aliases

Implementation: [`backend/app/schemas/sources.py`](../backend/app/schemas/sources.py). The 15 canonical identifiers map one-to-one to Section 3 of the requirements.

| Canonical | Requirement source | Accepted aliases |
|---|---|---|
| `cctv` | CCTV | `video`, `video_analytics`, `camera`, `cctv_camera`, `surveillance_camera` |
| `anpr` | ANPR | `lpr`, `alpr`, `license_plate_recognition`, `number_plate_recognition` |
| `face_recognition` | Face recognition | `facial_recognition`, `frs`, `face` |
| `mobile_phone_detection` | Mobile-phone detection | `mobile_phone`, `phone_detection`, `mobile_phone_use`, `handheld_phone_detection` |
| `seatbelt_detection` | Seatbelt detection | `seatbelt`, `seat_belt`, `seat_belt_detection` |
| `traffic_violation` | Red-light / traffic violation | `red_light_violation`, `red_light`, `red_light_traffic_violation`, `traffic_enforcement` |
| `radar` | Radar | `speed_radar`, `speed_detection`, `radar_speed` |
| `access_control` | Access control | `acs`, `access`, `badge_reader` |
| `perimeter_intrusion` | Perimeter / intrusion sensors | `perimeter`, `intrusion`, `intrusion_sensor`, `perimeter_sensor`, `pids` |
| `device_health` | Device-health monitoring | `device_health_monitoring`, `health`, `system_health`, `nms` |
| `operator_report` | Operator reports | `operator`, `manual_report` |
| `emergency_alarm` | Emergency alarms | `emergency`, `panic_alarm`, `alarm` |
| `gps_fleet` | GPS / fleet | `gps`, `fleet`, `avl`, `fleet_tracking` |
| `network_security` | Network / security logs | `network_security_log`, `network`, `security_log`, `siem`, `firewall`, `ids` |
| `weather_environmental` | Weather / environmental sensors | `weather`, `environmental`, `environment`, `weather_sensor` |

`GET /events?source_type=` accepts the same aliases.

## Typical payloads (as produced by the generator)

| Source | Payload fields |
|---|---|
| anpr | `plate_number`, `plate_region`, `vehicle_make`, `vehicle_model`, `vehicle_colour`, `vehicle_type`, `read_status` (`read`/`partial`/`unreadable`), `lane`, `direction`, `image_ref` |
| radar | `track_id`, `speed_kph`, `speed_limit_kph`, `direction`, `lane`, `vehicle_length_m`, `camera_triggered` (radar never carries a plate) |
| traffic_violation | `violation_type`, plate + make/model/colour, `measured_speed_kph`, `radar_device_id`, `radar_track_id`, `signal_phase`, `evidence_images` |
| cctv | `observation_type`, `object_category`, `object_count`, `track_id` (local to the camera), optional `attributes` |
| face_recognition | `match_status` (`matched`/`no_match`/`uncertain`), `subject_id` (synthetic, or `null`), `match_score`, `face_quality`, `gallery` |
| access_control | `credential_id`, `holder_id`, `door_id`, `result`, `reason`, `direction` |
| device_health | `status`, `diagnostic_code`, `monitor`, plus metrics such as `cpu_temp_c`, `packet_loss_pct`, `last_contact_at`, `downtime_s` |
| gps_fleet | `vehicle_id`, `plate_number` (own fleet vehicle), `route`, `driver_id`, `speed_kph`, `heading_deg`, `hdop`, `ignition` |
| network_security | `src_ip`, `dst_ip`, `dst_port`, `protocol`, `username`, `auth_method`, `action`, `signature`, `rule_id` |
| weather_environmental | `temperature_c`, `humidity_pct`, `wind_speed_kph`, `visibility_m`, `rain_mm_h`, `pm10_ugm3` |
| operator_report | `operator_id`, `report_category`, `summary`, `priority`, optional `referenced_event_ids` |
| emergency_alarm | `alarm_type`, `panel_id`, `input_point`, `state` |
