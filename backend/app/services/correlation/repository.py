"""Incident persistence: identity-preserving sync and read queries.

Sync policy (one transaction per run):

* **Identity** is decided by overlap of *core* events, not by a fingerprint, so an
  incident keeps its id (and operator status) when late evidence extends it or
  when its anchor changes. Each new group takes the active incident with which it
  shares the most core events (ties: lowest id) if that incident is not already
  taken by another group.
* **Split**: if a group would take an incident already taken by a larger-overlap
  group, it becomes a new incident with ``lineage.split_from``.
* **Merge**: an existing incident whose core events now all belong to one other
  incident is kept with ``correlation_state = 'merged'`` and ``superseded_by_id``;
  its operator status stays on that row (history is never lost).
* **Obsolete**: an incident no longer produced (for example after a threshold
  change) is kept with ``correlation_state = 'obsolete'``.
* Unchanged incidents (same content hash) are not rewritten; changed ones have
  their computed fields and evidence links replaced. Operator fields
  (``status``, ``status_note``, ``status_updated_at``) are never written here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, case, delete, exists, func, insert, select
from sqlalchemy.orm import Session

from app.models.incidents import CorrelationRun, Incident, IncidentEvidence, IncidentFacet
from app.models.ingestion import Event
from app.models.quality import QualityIssue, QualityIssueEvidence
from app.services.correlation.explanations import RULE_VERSION, IncidentDraft
from app.services.correlation.scoring import SEVERITY_RANK

COMPUTED_FIELDS = (
    "category", "title", "severity", "severity_reason", "confidence", "confidence_level", "confidence_reason", "summary",
    "linkage_explanation", "observations", "inferences", "conflicts", "missing_information", "uncertainty", "recommended_actions",
    "details", "first_occurred_at", "last_occurred_at",
)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    merged: int = 0
    obsoleted: int = 0


def _values(d: IncidentDraft, run_id: int, now: datetime) -> dict[str, Any]:
    out = {name: getattr(d, name) for name in COMPUTED_FIELDS}
    out.update(
        fingerprint=d.fingerprint,
        severity_rank=SEVERITY_RANK[d.severity],
        event_count=len(d.members),
        rule_version=RULE_VERSION,
        correlation_state="active",
        superseded_by_id=None,
        last_run_id=run_id,
        content_sha256=d.content_hash(),
        updated_at=now,
    )
    return out


def _links(incident_id: int, d: IncidentDraft) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = [
        {"incident_id": incident_id, "event_pk": m.obs.pk, "external_event_id": m.obs.event_id, "submission_id": m.obs.submission_id,
         "role": m.role, "link_type": m.link_type, "is_core": m.core, "linkage_explanation": m.explanation,
         "linkage_confidence": m.linkage_confidence, "conflict": m.conflict}
        for m in d.members
    ]
    facets = set()
    for m in d.members:
        facets.add(("device", m.obs.device))
        facets.add(("source", m.obs.source))
        if m.obs.zone:
            facets.add(("zone", m.obs.zone))
    return evidence, [{"incident_id": incident_id, "kind": k, "value": v[:128]} for k, v in sorted(facets)]


def sync_incidents(db: Session, drafts: list[IncidentDraft], run_id: int, now: datetime) -> SyncResult:
    result = SyncResult()
    active = {i.id: i for i in db.scalars(select(Incident).where(Incident.correlation_state == "active"))}
    core_of: dict[int, set[int]] = defaultdict(set)
    owner: dict[int, int] = {}
    for incident_id, event_pk in db.execute(
        select(IncidentEvidence.incident_id, IncidentEvidence.event_pk)
        .join(Incident, Incident.id == IncidentEvidence.incident_id)
        .where(Incident.correlation_state == "active", IncidentEvidence.is_core.is_(True))
    ):
        core_of[incident_id].add(event_pk)
        owner[event_pk] = incident_id

    drafts = sorted(drafts, key=lambda d: (d.first_occurred_at, d.fingerprint))
    taken: dict[int, int] = {}  # incident id -> draft index
    split_from: dict[int, int] = {}
    # larger overlaps choose first so a split keeps the id with its larger part
    overlaps = [Counter(owner[pk] for pk in d.core_pks if pk in owner) for d in drafts]
    order = sorted(range(len(drafts)), key=lambda i: (-(overlaps[i].most_common(1)[0][1] if overlaps[i] else 0), i))
    target = [None] * len(drafts)
    for idx in order:
        for incident_id, _n in sorted(overlaps[idx].items(), key=lambda kv: (-kv[1], kv[0])):
            if incident_id not in taken:
                taken[incident_id] = idx
                target[idx] = incident_id
                break
        else:
            if overlaps[idx]:
                split_from[idx] = min(overlaps[idx], key=lambda k: (-overlaps[idx][k], k))

    new_ids: dict[int, int] = {}
    link_evidence, link_facets, replaced = [], [], []
    for idx, d in enumerate(drafts):
        incident_id = target[idx]
        if incident_id is not None:
            incident = active[incident_id]
            new_ids[idx] = incident_id
            if incident.content_sha256 == d.content_hash() and incident.rule_version == RULE_VERSION:
                incident.last_run_id = run_id
                result.unchanged += 1
                continue
            for name, value in _values(d, run_id, now).items():
                setattr(incident, name, value)
            replaced.append(incident_id)
            ev, fa = _links(incident_id, d)
            link_evidence += ev
            link_facets += fa
            result.updated += 1
        else:
            values = _values(d, run_id, now) | {"detected_at": now, "status": "open"}
            if idx in split_from:
                values["lineage"] = {"split_from": split_from[idx], "at": now.isoformat()}
            incident_id = db.scalar(insert(Incident).returning(Incident.id), [values])
            new_ids[idx] = incident_id
            ev, fa = _links(incident_id, d)
            link_evidence += ev
            link_facets += fa
            result.created += 1

    for start in range(0, len(replaced), 500):
        part = replaced[start : start + 500]
        db.execute(delete(IncidentEvidence).where(IncidentEvidence.incident_id.in_(part)))
        db.execute(delete(IncidentFacet).where(IncidentFacet.incident_id.in_(part)))
    db.flush()
    for start in range(0, len(link_evidence), 2000):
        db.execute(insert(IncidentEvidence), link_evidence[start : start + 2000])
    for start in range(0, len(link_facets), 2000):
        db.execute(insert(IncidentFacet), link_facets[start : start + 2000])

    # incidents that were not carried forward: merged into another, or obsolete
    new_owner = {pk: new_ids[i] for i, d in enumerate(drafts) for pk in d.core_pks}
    for incident_id, incident in active.items():
        if incident_id in taken:
            continue
        destinations = {new_owner.get(pk) for pk in core_of[incident_id]}
        if len(destinations) == 1 and None not in destinations:
            survivor = destinations.pop()
            incident.correlation_state, incident.superseded_by_id = "merged", survivor
            incident.lineage = (incident.lineage or {}) | {"merged_into": survivor, "at": now.isoformat()}
            result.merged += 1
        else:
            incident.correlation_state = "obsolete"
            incident.lineage = (incident.lineage or {}) | {"obsolete_since": now.isoformat()}
            result.obsoleted += 1
        incident.updated_at = now
        incident.last_run_id = run_id
    return result


# --------------------------------------------------------------------------- read side


@dataclass
class IncidentFilters:
    severities: list[str] | None = None
    statuses: list[str] | None = None
    categories: list[str] | None = None
    zones: list[str] | None = None
    devices: list[str] | None = None
    sources: list[str] | None = None
    event_id: str | None = None
    event_pk: int | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    states: list[str] | None = None


UNRESOLVED_STATUSES = ("open", "acknowledged", "investigating")
_resolved_last = case((Incident.status.in_(UNRESOLVED_STATUSES), 0), else_=1)

SORTS = {
    # unresolved (open / acknowledged / investigating) before resolved and false positives, then most severe, most recent
    "priority": [_resolved_last.asc(), Incident.severity_rank.desc(), Incident.last_occurred_at.desc(), Incident.id.desc()],
    "-severity": [Incident.severity_rank.desc(), Incident.last_occurred_at.desc(), Incident.id.desc()],
    "-last_occurred_at": [Incident.last_occurred_at.desc(), Incident.id.desc()],
    "first_occurred_at": [Incident.first_occurred_at.asc(), Incident.id.asc()],
    "-confidence": [Incident.confidence.desc(), Incident.id.desc()],
    "-id": [Incident.id.desc()],
}


def _facet(kind: str, values: list[str]) -> Any:
    return exists().where(IncidentFacet.incident_id == Incident.id, IncidentFacet.kind == kind, IncidentFacet.value.in_(values))


def _apply(stmt: Select, f: IncidentFilters) -> Select:
    stmt = stmt.where(Incident.correlation_state.in_(f.states or ["active"]))
    if f.severities:
        stmt = stmt.where(Incident.severity.in_(f.severities))
    if f.statuses:
        stmt = stmt.where(Incident.status.in_(f.statuses))
    if f.categories:
        stmt = stmt.where(Incident.category.in_(f.categories))
    if f.zones:
        stmt = stmt.where(_facet("zone", f.zones))
    if f.devices:
        stmt = stmt.where(_facet("device", f.devices))
    if f.sources:
        stmt = stmt.where(_facet("source", f.sources))
    if f.event_id:
        stmt = stmt.where(exists().where(IncidentEvidence.incident_id == Incident.id, IncidentEvidence.external_event_id == f.event_id))
    if f.event_pk is not None:
        stmt = stmt.where(exists().where(IncidentEvidence.incident_id == Incident.id, IncidentEvidence.event_pk == f.event_pk))
    if f.occurred_from:
        stmt = stmt.where(Incident.last_occurred_at >= f.occurred_from)
    if f.occurred_to:
        stmt = stmt.where(Incident.first_occurred_at < f.occurred_to)
    return stmt


def facets_for(db: Session, ids: list[int]) -> dict[int, dict[str, list[str]]]:
    out = {i: {"zone": [], "device": [], "source": []} for i in ids}
    if ids:
        for incident_id, kind, value in db.execute(
            select(IncidentFacet.incident_id, IncidentFacet.kind, IncidentFacet.value)
            .where(IncidentFacet.incident_id.in_(ids)).order_by(IncidentFacet.incident_id, IncidentFacet.kind, IncidentFacet.value)
        ):
            out[incident_id][kind].append(value)
    return out


def list_incidents(db: Session, f: IncidentFilters, page: int, page_size: int, sort: str) -> tuple[list[Incident], int, dict[int, dict[str, list[str]]]]:
    total = db.scalar(_apply(select(func.count(Incident.id)), f)) or 0
    rows = list(db.scalars(_apply(select(Incident), f).order_by(*SORTS[sort]).offset((page - 1) * page_size).limit(page_size)))
    return rows, total, facets_for(db, [r.id for r in rows])


def evidence_for(db: Session, incident_id: int, limit: int) -> tuple[list[tuple[IncidentEvidence, Event]], int]:
    total = db.scalar(select(func.count()).where(IncidentEvidence.incident_id == incident_id)) or 0
    rows = db.execute(
        select(IncidentEvidence, Event)
        .join(Event, Event.id == IncidentEvidence.event_pk)
        .where(IncidentEvidence.incident_id == incident_id)
        .order_by(Event.occurred_at, Event.id)
        .limit(limit)
    ).all()
    return [(link, event) for link, event in rows], total


def quality_issues_for(db: Session, incident_id: int, limit: int = 200) -> list[dict[str, Any]]:
    """Phase 3 findings touching the incident's events (computed on read; nothing is copied)."""
    rows = db.execute(
        select(QualityIssue.id, QualityIssue.issue_type, QualityIssue.subtype, QualityIssue.severity, QualityIssue.title,
               QualityIssueEvidence.external_event_id)
        .join(QualityIssueEvidence, QualityIssueEvidence.issue_id == QualityIssue.id)
        .join(IncidentEvidence, IncidentEvidence.event_pk == QualityIssueEvidence.event_pk)
        .where(IncidentEvidence.incident_id == incident_id)
        .order_by(QualityIssue.severity_rank.desc(), QualityIssue.id)
        .limit(limit)
    ).all()
    out: dict[int, dict[str, Any]] = {}
    for qid, itype, subtype, severity, title, eid in rows:
        item = out.setdefault(qid, {"id": qid, "issue_type": itype, "subtype": subtype, "severity": severity, "title": title, "event_ids": []})
        if eid and eid not in item["event_ids"]:
            item["event_ids"].append(eid)
    return list(out.values())


def summary(db: Session) -> dict[str, Any]:
    active = Incident.correlation_state == "active"

    def grouped(column: Any) -> list[dict[str, Any]]:
        return [{"value": v, "count": c} for v, c in db.execute(
            select(column, func.count()).where(active).group_by(column).order_by(func.count().desc(), column))]

    return {
        "total": db.scalar(select(func.count(Incident.id)).where(active)) or 0,
        "by_category": grouped(Incident.category),
        "by_severity": grouped(Incident.severity),
        "by_status": grouped(Incident.status),
        "by_confidence": grouped(Incident.confidence_level),
        "unresolved": db.scalar(select(func.count(Incident.id)).where(active, Incident.status.in_(UNRESOLVED_STATUSES))) or 0,
        "unresolved_by_severity": [{"value": v, "count": c} for v, c in db.execute(
            select(Incident.severity, func.count()).where(active, Incident.status.in_(UNRESOLVED_STATUSES))
            .group_by(Incident.severity).order_by(func.count().desc(), Incident.severity))],
        "inactive": db.scalar(select(func.count(Incident.id)).where(Incident.correlation_state != "active")) or 0,
        "last_run": db.scalars(select(CorrelationRun).order_by(CorrelationRun.id.desc()).limit(1)).first(),
    }


def set_status(db: Session, incident: Incident, status: str, note: str | None, now: datetime) -> Incident:
    incident.status = status
    incident.status_note = note
    incident.status_updated_at = now
    db.commit()
    return incident

