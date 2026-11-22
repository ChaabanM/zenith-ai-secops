"""G. identity and sensor-outcome contradictions; L. unresolved identity evidence.

Pairing rules are deliberately strict so that different people or vehicles at the
same gate are never compared:

* a badge use and a face match are provisionally paired for conflict analysis only
  when they happen at the same zone within ``identity_pair_window_seconds`` and the
  pairing is unambiguous (exactly one granted badge use and one face event in that
  window). The pairing is never presented as verified identity;
* a weak/uncertain face candidate is never called a conflict; it is reported as
  unresolved identity evidence (issue type ``uncorrelatable_event``).
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select

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


def _by_zone(rows: list[Any]) -> dict[str, tuple[list[float], list[Any]]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        if r.zone:
            grouped[r.zone].append(r)
    out = {}
    for zone, items in grouped.items():
        items.sort(key=lambda r: (r.occurred_at, r.id))
        out[zone] = ([r.occurred_at.timestamp() for r in items], items)
    return out


def _within(index: tuple[list[float], list[Any]] | None, t: float, window: float) -> list[Any]:
    if index is None:
        return []
    times, items = index
    lo, hi = bisect.bisect_left(times, t - window), bisect.bisect_right(times, t + window)
    return items[lo:hi]


class IdentityConflictRule(Rule):
    """G. A confident face match and the badge used at the same moment name different people."""

    rule_id = "SNS-002"
    issue_type = "conflicting_sensor_results"
    summary = "Face recognition and access control disagree about who is at the door."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        s = ctx.settings
        faces = ctx.db.execute(
            select(
                *EVENT_COLUMNS,
                payload_field("match_status").label("match_status"),
                payload_field("subject_id").label("subject"),
                payload_field("candidate_subject_id").label("candidate"),
                payload_field("match_score").label("score"),
            ).where(Event.source_type == "face_recognition", payload_field("match_status").in_(["matched", "uncertain"]))
        ).all()
        access = ctx.db.execute(
            select(*EVENT_COLUMNS, payload_field("holder_id").label("holder"), payload_field("credential_id").label("credential"))
            .where(Event.source_type == "access_control", payload_field("result") == "granted", payload_field("holder_id").is_not(None))
        ).all()
        access_index, face_index = _by_zone(access), _by_zone(faces)
        for face in sorted(faces, key=lambda r: r.id):
            if not face.zone:
                continue
            t = face.occurred_at.timestamp()
            score = face.score if face.score is not None else face.confidence
            confident = face.match_status == "matched" and face.subject and score is not None and score >= s.identity_min_face_score
            if confident:
                badges = _within(access_index.get(face.zone), t, s.identity_pair_window_seconds)
                other_faces = [f for f in _within(face_index.get(face.zone), t, s.identity_pair_window_seconds) if f.id != face.id]
                if len(badges) == 1 and not other_faces and badges[0].holder != face.subject:
                    yield self._conflict(face, badges[0], score)
            elif face.candidate:
                badges = _within(access_index.get(face.zone), t, s.unresolved_identity_window_seconds)
                mismatched = [b for b in badges if b.holder != face.candidate]
                if len(badges) == 1 and mismatched:
                    yield self._unresolved(face, mismatched[0], score)

    def _conflict(self, face: Any, badge: Any, score: float) -> IssueDraft:
        gap = (face.occurred_at - badge.occurred_at).total_seconds()
        draft = self.draft(
            f"{badge.id}:{face.id}",
            subtype="identity",
            severity="high",
            title=f"Badge of {badge.holder} used while face recognition matched {face.subject} at {face.zone}",
            description=(
                "The person recognised at the door is not the holder of the credential that opened it. Possible credential "
                "sharing or misuse, or a face-recognition error. Neither record is assumed to be correct."
            ),
            detection_reason=(
                f"At {face.zone}, credential {badge.credential} (holder {badge.holder}) was granted at {fmt_time(badge.occurred_at)} "
                f"({badge.device_id}) and {face.device_id} matched {face.subject} with score {score:.2f} {fmt_duration(gap)} apart. "
                "These observations were provisionally paired for conflict analysis because they were the only badge and "
                "face-recognition events within the time window. It is not verified that they concern the same person."
            ),
            occurred_from=min(face.occurred_at, badge.occurred_at),
            occurred_to=max(face.occurred_at, badge.occurred_at),
            confidence=round(min(0.9, score), 2),
            uncertainty="Pairing relies on time and place only (no shared identifier); a second person passing unseen would explain it.",
            details={"credential_id": badge.credential, "holder_id": badge.holder, "face_subject_id": face.subject, "match_score": score,
                     "seconds_apart": gap, "link_basis": "same zone, within the pairing window, unambiguous"},
            evidence=[event_evidence(badge, "badge_use"), event_evidence(face, "face_match")],
        )
        return add_facets(draft, [badge, face])

    def _unresolved(self, face: Any, badge: Any, score: float | None) -> IssueDraft:
        draft = self.draft(
            f"unresolved:{badge.id}:{face.id}",
            issue_type="uncorrelatable_event",
            subtype="contradictory_identifiers",
            severity="low",
            title=f"Unresolved identity at {face.zone}: weak face candidate {face.candidate} vs badge holder {badge.holder}",
            description=(
                "The evidence points to different people but the face match is too weak to call it a conflict. These "
                "observations cannot be reliably linked to one person, or separated, without more evidence."
            ),
            detection_reason=(
                f"{face.device_id} reported only an uncertain candidate ({face.candidate}, score {score if score is not None else 'n/a'}) "
                f"at {fmt_time(face.occurred_at)}, while {badge.device_id} granted {badge.holder} at {fmt_time(badge.occurred_at)} in the same zone."
            ),
            occurred_from=min(face.occurred_at, badge.occurred_at),
            occurred_to=max(face.occurred_at, badge.occurred_at),
            confidence=0.5,
            uncertainty="Identity is unresolved; the face candidate is below the confidence needed for a match.",
            missing_evidence="A confident identification (better image, second camera, or guard confirmation).",
            details={"face_candidate": face.candidate, "match_score": score, "holder_id": badge.holder},
            evidence=[event_evidence(face, "uncertain_face"), event_evidence(badge, "badge_use")],
        )
        return add_facets(draft, [face, badge])


class PerimeterVerificationRule(Rule):
    """G. A perimeter alarm is contradicted by the camera verifying the same section."""

    rule_id = "SNS-003"
    issue_type = "conflicting_sensor_results"
    summary = "A perimeter sensor alarm and the verifying camera disagree."

    ALARM_TYPES = ("fence_cut", "fence_climb", "beam_break")

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        window = timedelta(seconds=ctx.settings.perimeter_verification_window_seconds)
        verifications = ctx.db.execute(
            select(*EVENT_COLUMNS, payload_field("verification_result").label("result"))
            .where(Event.source_type == "cctv", payload_field("verification_result") == "no_activity")
            .order_by(Event.id)
        ).all()
        for cam in verifications:
            if not cam.zone:
                continue
            alarms = ctx.db.execute(
                select(*EVENT_COLUMNS)
                .where(
                    and_(
                        Event.zone == cam.zone,
                        Event.source_type == "perimeter_intrusion",
                        Event.occurred_at >= cam.occurred_at - window,
                        Event.occurred_at <= cam.occurred_at,
                        or_(Event.event_type.in_(self.ALARM_TYPES), Event.severity.in_(["high", "critical"])),
                    )
                )
                .order_by(Event.occurred_at.desc(), Event.id.desc())
                .limit(1)
            ).all()
            if not alarms:
                continue
            alarm = alarms[0]
            gap = (cam.occurred_at - alarm.occurred_at).total_seconds()
            draft = self.draft(
                f"{alarm.id}:{cam.id}",
                subtype="sensor_outcome",
                severity="high",
                title=f"{alarm.event_type.replace('_', ' ').capitalize()} alarm at {alarm.zone} not confirmed by camera",
                description=(
                    "The perimeter sensor reports an intrusion but the camera covering the section saw nothing. Either the "
                    "alarm is false (wind, wildlife, sensor fault) or the camera missed the intruder; neither can be assumed."
                ),
                detection_reason=(
                    f"{alarm.device_id} raised {alarm.event_type} ({alarm.severity}, confidence {alarm.confidence}) at "
                    f"{fmt_time(alarm.occurred_at)}; {fmt_duration(gap)} later {cam.device_id} reported verification result "
                    f"'no_activity' for the same zone."
                ),
                occurred_from=alarm.occurred_at,
                occurred_to=cam.occurred_at,
                confidence=0.8,
                uncertainty="Which sensor is right is unknown; physical verification is needed.",
                missing_evidence="Patrol or second-camera confirmation of the fence section.",
                details={"seconds_apart": gap, "window_seconds": window.total_seconds(), "link_basis": "same zone, camera verification after alarm"},
                evidence=[event_evidence(alarm, "perimeter_alarm"), event_evidence(cam, "camera_verification")],
            )
            yield add_facets(draft, [alarm, cam])
