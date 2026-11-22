/** Plain-language names for quality issue types (for managers, not engineers). */
export const ISSUE_TYPE_LABELS: Record<string, string> = {
  duplicate_submission: "Duplicate submission",
  invalid_record: "Malformed / incomplete record",
  delayed_event: "Delayed event",
  out_of_order_arrival: "Out-of-order arrival",
  timestamp_conflict: "Conflicting timestamps",
  location_conflict: "Conflicting locations",
  conflicting_sensor_results: "Conflicting sensor results",
  repeated_device_events: "Repeated device alerts",
  event_spike: "Event spike",
  device_offline: "Device offline",
  device_degraded: "Device degraded",
  sensor_failure: "Sensor failure",
  device_recovered: "Device recovered",
  device_health_warning: "Device health warning",
  low_confidence_detection: "Low-confidence detection",
  uncorrelatable_event: "Cannot be reliably linked",
};

export const ISSUE_TYPE_HINTS: Record<string, string> = {
  duplicate_submission: "The same event ID was submitted more than once.",
  invalid_record: "Rejected record: the observation is unusable.",
  delayed_event: "Arrived long after it happened.",
  out_of_order_arrival: "A device's events arrived in the wrong order.",
  timestamp_conflict: "Times that cannot all be true.",
  location_conflict: "One vehicle in two places at once.",
  conflicting_sensor_results: "Sensors disagree about the same thing.",
  repeated_device_events: "One device repeating the same alert.",
  event_spike: "Unusual jump in event volume.",
  device_offline: "Device stopped reporting (reported outage).",
  device_degraded: "Device working but unhealthy.",
  sensor_failure: "Device reported a sensor fault.",
  device_recovered: "Device back online after an outage.",
  device_health_warning: "Health metrics outside normal range.",
  low_confidence_detection: "Weak evidence; needs corroboration.",
  uncorrelatable_event: "Missing identifiers or context to link it.",
};

export const ROLE_LABELS: Record<string, string> = {
  stored_event: "Stored event",
  original_submission: "Original submission",
  duplicate_submission: "Repeated submission",
  invalid_submission: "Rejected submission",
  delayed_event: "Delayed event",
  late_arrival: "Arrived late",
  occurred_later_but_arrived_first: "Arrived first (occurred later)",
  inconsistent_event: "Inconsistent event",
  radar_measurement: "Radar measurement",
  violation_record: "Violation record",
  earlier_observation: "Earlier observation",
  later_observation: "Later observation",
  first_observation: "First observation",
  contradicting_observation: "Contradicting observation",
  badge_use: "Badge use",
  face_match: "Face match",
  uncertain_face: "Uncertain face match",
  perimeter_alarm: "Perimeter alarm",
  camera_verification: "Camera verification",
  repeated_event: "Repeated alert",
  spike_event: "Event in spike",
  offline_report: "Offline report",
  recovery_report: "Recovery report",
  fault_report: "Fault report",
  degradation_report: "Degradation report",
  health_report: "Health report",
  low_confidence_event: "Low-confidence detection",
  unlinkable_event: "Unlinkable observation",
  unlocated_event: "Event without location",
  uncorroborated_report: "Uncorroborated report",
};

export function issueTypeLabel(type: string): string {
  return ISSUE_TYPE_LABELS[type] ?? type;
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role.replace(/_/g, " ");
}
