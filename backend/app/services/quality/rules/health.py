"""J. offline and unhealthy devices, from device_health observations only.

A device_health event describes the *affected* device in ``device_id`` (and its
location); the *reporting* monitor is ``payload.monitor``. Its source_type is always
``device_health`` regardless of the affected device's own source or alias.
Absence of events is never treated as an outage: there is no documented heartbeat
contract for the sources, so only reported health states are used.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import select

from app.models.ingestion import Event
from app.services.quality.rules.base import (
    EVENT_COLUMNS,
    QualityContext,
    Rule,
    add_facets,
    event_evidence,
    fmt_duration,
    fmt_time,
    payload_field,
)
from app.services.quality.schemas import IssueDraft

OFFLINE, DEGRADED, FAULT, RECOVERED = "offline", "degraded", "fault", "recovered"


def health_state(row: Any) -> str | None:
    status = (row.status or "").lower()
    if row.event_type == "device_offline" or status == "offline":
        return OFFLINE
    if row.event_type == "sensor_failure" or status in ("fault", "failed", "failure"):
        return FAULT
    if row.event_type == "device_degraded" or status == "degraded":
        return DEGRADED
    if row.event_type == "device_recovered":
        return RECOVERED
    return None


class DeviceHealthRule(Rule):
    rule_id = "HLT-001"
    issue_type = "device_offline"
    summary = "A monitored device reported itself offline, degraded, failed or recovered."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        stmt = (
            select(
                *EVENT_COLUMNS,
                payload_field("status").label("status"),
                payload_field("diagnostic_code").label("code"),
                payload_field("monitor").label("monitor"),
                payload_field("cpu_temp_c").label("temp"),
                payload_field("packet_loss_pct").label("loss"),
                payload_field("last_contact_at").label("last_contact"),
            )
            .where(Event.source_type == "device_health")
            .order_by(Event.device_id, Event.occurred_at, Event.id)
        )
        group: list[Any] = []
        for row in ctx.stream(stmt):
            if group and group[0].device_id != row.device_id:
                yield from self._device(group, ctx)
                group = []
            group.append(row)
        if group:
            yield from self._device(group, ctx)

    def _device(self, rows: list[Any], ctx: QualityContext) -> Iterator[IssueDraft]:
        s = ctx.settings
        latest = rows[-1]
        latest_state = health_state(latest) or (latest.status or "unknown")
        current = {"latest_health_event": latest.event_id, "latest_status": latest_state, "as_of": latest.occurred_at.isoformat()}
        for i, row in enumerate(rows):
            state = health_state(row)
            reporter = row.monitor or "unknown monitor"
            base_details = {"affected_device_id": row.device_id, "reporting_monitor": row.monitor, "diagnostic_code": row.code,
                            "reported_status": row.status, "current_device_state": current}
            if state == OFFLINE:
                recovery = next((r for r in rows[i + 1:] if health_state(r) == RECOVERED or (r.status or "").lower() == "online"), None)
                evidence = [event_evidence(row, "offline_report")]
                if recovery:
                    downtime = (recovery.occurred_at - row.occurred_at).total_seconds()
                    evidence.append(event_evidence(recovery, "recovery_report"))
                    tail = f"It reported recovery at {fmt_time(recovery.occurred_at)} after {fmt_duration(downtime)} offline ({recovery.event_id})."
                    severity, missing, end = "high", None, recovery.occurred_at
                    details = {**base_details, "recovered": True, "recovered_at": recovery.occurred_at.isoformat(), "downtime_seconds": downtime}
                else:
                    tail = "No recovery has been reported in the data analysed; the device should be treated as still offline."
                    severity, end = "critical", row.occurred_at
                    missing = "No recovery report after the outage."
                    details = {**base_details, "recovered": False}
                yield self._make(
                    row, "device_offline", None, severity,
                    f"{row.device_id} offline" + ("" if recovery else " (no recovery reported)"),
                    "The device stopped working. Its observations are missing for the outage period; that gap must not be read as 'nothing happened'.",
                    f"{reporter} reported {row.device_id} offline at {fmt_time(row.occurred_at)} (code {row.code or 'n/a'}"
                    + (f", last contact {row.last_contact}" if row.last_contact else "") + f"). {tail}",
                    details, evidence, end, missing,
                )
            elif state == FAULT:
                yield self._make(
                    row, "sensor_failure", None, "high", f"Sensor failure on {row.device_id} ({row.code or 'unspecified'})",
                    "The device reports a sensor fault; its observations around this time may be missing or wrong.",
                    f"{reporter} reported status '{row.status}' with diagnostic code {row.code or 'n/a'} for {row.device_id} at {fmt_time(row.occurred_at)}.",
                    base_details, [event_evidence(row, "fault_report")],
                )
            elif state == DEGRADED:
                metrics = ", ".join(f"{k} {v}" for k, v in (("temperature", row.temp), ("packet loss %", row.loss)) if v is not None)
                yield self._make(
                    row, "device_degraded", None, "medium", f"{row.device_id} degraded ({row.code or 'unspecified'})",
                    "The device is working but unhealthy. Its output quality (and confidence values) may be reduced.",
                    f"{reporter} reported {row.device_id} as degraded at {fmt_time(row.occurred_at)} (code {row.code or 'n/a'}"
                    + (f"; {metrics}" if metrics else "") + ").",
                    base_details, [event_evidence(row, "degradation_report")],
                )
            elif state == RECOVERED:
                outage = next((r for r in reversed(rows[:i]) if health_state(r) == OFFLINE), None)
                evidence = [event_evidence(row, "recovery_report")]
                text = f"{reporter} reported {row.device_id} back online at {fmt_time(row.occurred_at)}"
                if outage:
                    evidence.insert(0, event_evidence(outage, "offline_report"))
                    text += f", {fmt_duration((row.occurred_at - outage.occurred_at).total_seconds())} after it was reported offline ({outage.event_id})."
                else:
                    text += "; no preceding offline report is present in the data."
                yield self._make(
                    row, "device_recovered", None, "informational", f"{row.device_id} recovered",
                    "The device is reporting normally again. Observations during the preceding outage remain missing.",
                    text, base_details, evidence, start=outage.occurred_at if outage else None,
                )
            else:
                warnings = []
                if row.temp is not None and float(row.temp) >= s.health_temperature_warning_c:
                    warnings.append(f"temperature {row.temp} C >= {s.health_temperature_warning_c} C")
                if row.loss is not None and float(row.loss) >= s.health_packet_loss_warning_pct:
                    warnings.append(f"packet loss {row.loss}% >= {s.health_packet_loss_warning_pct}%")
                if warnings:
                    yield self._make(
                        row, "device_health_warning", "metric_threshold", "low", f"Health warning on {row.device_id}: {'; '.join(warnings)}",
                        "Health metrics are outside the normal range although the device reports itself online.",
                        f"{reporter} heartbeat at {fmt_time(row.occurred_at)}: {'; '.join(warnings)}.",
                        base_details, [event_evidence(row, "health_report")],
                    )

    def _make(self, row: Any, issue_type: str, subtype: str | None, severity: str, title: str, description: str, reason: str,
              details: dict[str, Any], evidence: list[Any], end: Any = None, missing: str | None = None, start: Any = None) -> IssueDraft:
        draft = self.draft(
            f"{issue_type}:{row.id}",
            issue_type=issue_type,
            subtype=subtype,
            severity=severity,
            title=title,
            description=description,
            detection_reason=reason,
            occurred_from=start or row.occurred_at,
            occurred_to=end or row.occurred_at,
            confidence=1.0,
            missing_evidence=missing,
            details=details,
            evidence=evidence,
        )
        return add_facets(draft, [row])
