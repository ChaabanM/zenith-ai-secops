"""Persistence and read queries for quality issues.

``sync_issues`` makes the stored issues equal to the findings of one analysis run,
keyed by fingerprint: new findings are inserted, changed ones updated (their
``first_detected_at`` is kept), unchanged ones only get ``last_detected_at``
refreshed, and findings that are no longer produced (because the evidence
changed) are removed. Re-running on unchanged data therefore changes nothing but
timestamps, and never duplicates issues.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, delete, exists, func, insert, select, update
from sqlalchemy.orm import Session

from app.models.ingestion import Event, RawSubmission
from app.models.quality import SEVERITY_RANK, QualityIssue, QualityIssueEvidence, QualityIssueFacet, QualityRun
from app.services.quality.schemas import IssueDraft

CHUNK = 500


def _chunks(items: list[Any], size: int = CHUNK) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _issue_values(d: IssueDraft, run_id: int, now: datetime, content: str) -> dict[str, Any]:
    events, submissions = d.evidence_counts()
    return {
        "fingerprint": d.fingerprint,
        "issue_type": d.issue_type,
        "subtype": d.subtype,
        "rule_id": d.rule_id,
        "rule_version": d.rule_version,
        "severity": d.severity,
        "severity_rank": SEVERITY_RANK[d.severity],
        "title": d.title[:300],
        "description": d.description,
        "detection_reason": d.detection_reason,
        "confidence": d.confidence,
        "uncertainty": d.uncertainty,
        "missing_evidence": d.missing_evidence,
        "details": d.details,
        "occurred_from": d.occurred_from,
        "occurred_to": d.occurred_to,
        "time_basis": d.time_basis,
        "event_count": events,
        "submission_count": submissions,
        "status": "open",
        "last_detected_at": now,
        "last_run_id": run_id,
        "content_sha256": content,
    }


def _links(issue_id: int, d: IssueDraft) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = [
        {"issue_id": issue_id, "position": i, "role": e.role, "event_pk": e.event_pk, "submission_id": e.submission_id,
         "external_event_id": e.external_event_id}
        for i, e in enumerate(d.evidence)
    ]
    facets = [{"issue_id": issue_id, "kind": kind, "value": value[:128]}
              for kind, values in (("device", d.devices), ("zone", d.zones), ("source", d.sources)) for value in sorted(values)]
    return evidence, facets


@dataclass
class SyncResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0


def sync_issues(db: Session, drafts: list[IssueDraft], run_id: int, now: datetime) -> SyncResult:
    result = SyncResult()
    by_fp: dict[str, IssueDraft] = {}
    for d in sorted(drafts, key=lambda d: d.fingerprint):
        by_fp.setdefault(d.fingerprint, d)  # fingerprints are unique per rule design; keep the first defensively
    existing = {fp: (pk, sha) for pk, fp, sha in db.execute(select(QualityIssue.id, QualityIssue.fingerprint, QualityIssue.content_sha256))}

    stale = [pk for fp, (pk, _) in existing.items() if fp not in by_fp]
    for part in _chunks(stale):
        db.execute(delete(QualityIssueEvidence).where(QualityIssueEvidence.issue_id.in_(part)))
        db.execute(delete(QualityIssueFacet).where(QualityIssueFacet.issue_id.in_(part)))
        db.execute(delete(QualityIssue).where(QualityIssue.id.in_(part)))
    result.removed = len(stale)

    new_rows, new_drafts, changed_rows, changed_drafts = [], [], [], []
    for fp, d in by_fp.items():
        content = d.content_hash()
        if fp not in existing:
            new_rows.append({**_issue_values(d, run_id, now, content), "first_detected_at": now})
            new_drafts.append(d)
        elif existing[fp][1] != content:
            changed_rows.append({"id": existing[fp][0], **_issue_values(d, run_id, now, content)})
            changed_drafts.append(d)
        else:
            result.unchanged += 1

    link_evidence, link_facets = [], []
    for rows, drafts_part in zip(_chunks(new_rows), _chunks(new_drafts), strict=True):
        ids = list(db.scalars(insert(QualityIssue).returning(QualityIssue.id, sort_by_parameter_order=True), rows))
        for pk, d in zip(ids, drafts_part, strict=True):
            ev, fa = _links(pk, d)
            link_evidence += ev
            link_facets += fa
    result.inserted = len(new_rows)

    if changed_rows:
        db.execute(update(QualityIssue), changed_rows)
        changed_ids = [r["id"] for r in changed_rows]
        for part in _chunks(changed_ids):
            db.execute(delete(QualityIssueEvidence).where(QualityIssueEvidence.issue_id.in_(part)))
            db.execute(delete(QualityIssueFacet).where(QualityIssueFacet.issue_id.in_(part)))
        for row, d in zip(changed_rows, changed_drafts, strict=True):
            ev, fa = _links(row["id"], d)
            link_evidence += ev
            link_facets += fa
    result.updated = len(changed_rows)

    for part in _chunks(link_evidence, 2000):
        db.execute(insert(QualityIssueEvidence), part)
    for part in _chunks(link_facets, 2000):
        db.execute(insert(QualityIssueFacet), part)
    db.execute(update(QualityIssue).where(QualityIssue.last_run_id != run_id).values(last_detected_at=now, last_run_id=run_id))
    return result


# --------------------------------------------------------------------------- read side


@dataclass
class IssueFilters:
    issue_types: list[str] | None = None
    severities: list[str] | None = None
    rule_ids: list[str] | None = None
    sources: list[str] | None = None
    devices: list[str] | None = None
    zones: list[str] | None = None
    event_id: str | None = None
    event_pk: int | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


SORTS = {
    "-severity": [QualityIssue.severity_rank.desc(), QualityIssue.occurred_to.desc(), QualityIssue.id.desc()],
    "-occurred_at": [QualityIssue.occurred_to.desc(), QualityIssue.id.desc()],
    "occurred_at": [QualityIssue.occurred_from.asc(), QualityIssue.id.asc()],
    "-id": [QualityIssue.id.desc()],
}


def _facet(kind: str, values: list[str]) -> Any:
    return exists().where(QualityIssueFacet.issue_id == QualityIssue.id, QualityIssueFacet.kind == kind, QualityIssueFacet.value.in_(values))


def _apply(stmt: Select, f: IssueFilters) -> Select:
    if f.issue_types:
        stmt = stmt.where(QualityIssue.issue_type.in_(f.issue_types))
    if f.severities:
        stmt = stmt.where(QualityIssue.severity.in_(f.severities))
    if f.rule_ids:
        stmt = stmt.where(QualityIssue.rule_id.in_(f.rule_ids))
    if f.sources:
        stmt = stmt.where(_facet("source", f.sources))
    if f.devices:
        stmt = stmt.where(_facet("device", f.devices))
    if f.zones:
        stmt = stmt.where(_facet("zone", f.zones))
    if f.event_id:
        stmt = stmt.where(exists().where(QualityIssueEvidence.issue_id == QualityIssue.id, QualityIssueEvidence.external_event_id == f.event_id))
    if f.event_pk is not None:
        stmt = stmt.where(exists().where(QualityIssueEvidence.issue_id == QualityIssue.id, QualityIssueEvidence.event_pk == f.event_pk))
    if f.occurred_from:  # overlap with [from, to)
        stmt = stmt.where(QualityIssue.occurred_to >= f.occurred_from)
    if f.occurred_to:
        stmt = stmt.where(QualityIssue.occurred_from < f.occurred_to)
    return stmt


def list_issues(db: Session, f: IssueFilters, page: int, page_size: int, sort: str) -> tuple[list[QualityIssue], int, dict[int, dict[str, list[str]]]]:
    total = db.scalar(_apply(select(func.count(QualityIssue.id)), f)) or 0
    rows = list(db.scalars(_apply(select(QualityIssue), f).order_by(*SORTS[sort]).offset((page - 1) * page_size).limit(page_size)))
    return rows, total, facets_for(db, [r.id for r in rows])


def facets_for(db: Session, ids: list[int]) -> dict[int, dict[str, list[str]]]:
    out: dict[int, dict[str, list[str]]] = {i: {"device": [], "zone": [], "source": []} for i in ids}
    if ids:
        for issue_id, kind, value in db.execute(
            select(QualityIssueFacet.issue_id, QualityIssueFacet.kind, QualityIssueFacet.value)
            .where(QualityIssueFacet.issue_id.in_(ids))
            .order_by(QualityIssueFacet.issue_id, QualityIssueFacet.kind, QualityIssueFacet.value)
        ):
            out[issue_id][kind].append(value)
    return out


def summary(db: Session) -> dict[str, Any]:
    by_type = [{"value": t, "count": c} for t, c in db.execute(
        select(QualityIssue.issue_type, func.count()).group_by(QualityIssue.issue_type).order_by(func.count().desc(), QualityIssue.issue_type))]
    by_severity = [{"value": s, "count": c} for s, c in db.execute(
        select(QualityIssue.severity, func.count()).group_by(QualityIssue.severity, QualityIssue.severity_rank).order_by(QualityIssue.severity_rank.desc()))]
    by_rule = [{"value": r, "count": c} for r, c in db.execute(
        select(QualityIssue.rule_id, func.count()).group_by(QualityIssue.rule_id).order_by(QualityIssue.rule_id))]
    facet = {}
    for kind in ("device", "zone", "source"):
        facet[kind] = [{"value": v, "count": c} for v, c in db.execute(
            select(QualityIssueFacet.value, func.count()).where(QualityIssueFacet.kind == kind)
            .group_by(QualityIssueFacet.value).order_by(func.count().desc(), QualityIssueFacet.value).limit(500))]
    last_run = db.scalars(select(QualityRun).order_by(QualityRun.id.desc()).limit(1)).first()
    return {
        "total": sum(x["count"] for x in by_type),
        "by_type": by_type,
        "by_severity": by_severity,
        "by_rule": by_rule,
        "devices": facet["device"],
        "zones": facet["zone"],
        "sources": facet["source"],
        "last_run": last_run,
    }


def evidence_for(db: Session, issue_id: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    total = db.scalar(select(func.count()).where(QualityIssueEvidence.issue_id == issue_id)) or 0
    rows = db.execute(
        select(QualityIssueEvidence, Event, RawSubmission)
        .outerjoin(Event, Event.id == QualityIssueEvidence.event_pk)
        .outerjoin(RawSubmission, RawSubmission.id == func.coalesce(QualityIssueEvidence.submission_id, Event.submission_id))
        .where(QualityIssueEvidence.issue_id == issue_id)
        .order_by(QualityIssueEvidence.position)
        .limit(limit)
    ).all()
    return [{"link": link, "event": event, "submission": sub} for link, event, sub in rows], total


def issues_for_event(db: Session, event_pk: int, limit: int = 50) -> list[QualityIssue]:
    return list(db.scalars(
        select(QualityIssue)
        .where(exists().where(QualityIssueEvidence.issue_id == QualityIssue.id, QualityIssueEvidence.event_pk == event_pk))
        .order_by(*SORTS["-severity"])
        .limit(limit)
    ))


def page_count(total: int, page_size: int) -> int:
    return math.ceil(total / page_size) if total else 0
