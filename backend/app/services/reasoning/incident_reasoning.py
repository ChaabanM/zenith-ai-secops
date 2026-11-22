"""Structured reasoning for one incident: priority breakdown plus statements by kind.

Reuses the incident's stored Phase 4 explanation fields (nothing is re-derived),
classifying each into observed / inference / conflict / missing / recommendation
and attaching the evidence the statement rests on.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.incidents import Incident, IncidentEvidence
from app.models.ingestion import Event
from app.models.quality import QualityIssue, QualityIssueEvidence
from app.schemas.briefing import EvidenceRef, Finding, FindingConfidence, IncidentReasoning, PriorityFactor, PrioritizedIncident
from app.services.correlation import repository as incident_repo
from app.services.reasoning.configuration import ReasoningSettings
from app.services.reasoning.prioritization import score_incident

ANOMALY_TYPES = ("event_spike", "repeated_device_events")


def incident_reasoning(db: Session, incident: Incident, s: ReasoningSettings) -> IncidentReasoning:
    reference = db.scalar(select(func.max(Event.occurred_at))) or datetime.now(UTC)
    facets = incident_repo.facets_for(db, [incident.id])[incident.id]
    links = db.execute(
        select(IncidentEvidence, Event.event_id).join(Event, Event.id == IncidentEvidence.event_pk)
        .where(IncidentEvidence.incident_id == incident.id).order_by(Event.occurred_at, Event.id)
    ).all()
    anomalies = db.scalar(
        select(func.count(func.distinct(QualityIssueEvidence.event_pk))).join(QualityIssue, QualityIssue.id == QualityIssueEvidence.issue_id)
        .where(QualityIssueEvidence.event_pk.in_([link.event_pk for link, _ in links]), QualityIssue.issue_type.in_(ANOMALY_TYPES))
    ) or 0
    scored = score_incident(incident, facets["source"], reference, anomalies, s)
    core = [EvidenceRef(event_pk=link.event_pk, event_id=eid) for link, eid in links if link.is_core]
    context = [EvidenceRef(event_pk=link.event_pk, event_id=eid) for link, eid in links if not link.is_core]
    conflicting = [EvidenceRef(event_pk=link.event_pk, event_id=eid) for link, eid in links if link.conflict]
    level = incident.confidence_level
    n = 0

    def f(basis: str, statement: str, reasoning: str, evidence: list[EvidenceRef], conf_level: str = level, **kw) -> Finding:
        nonlocal n
        n += 1
        return Finding(id=f"incident-{incident.id}-{n}", category="incident", basis=basis, statement=statement, reasoning=reasoning,
                       confidence=FindingConfidence(level=conf_level, score=incident.confidence if conf_level == level else None,
                                                    basis="incident confidence (heuristic)" if conf_level == level else "stored record"),
                       evidence=evidence[:25], evidence_total=len(evidence), incident_ids=[incident.id], **kw)

    findings = [f("observed", o, "Reported by the linked sensors or operators; not independently verified.", core + context, "high")
                for o in incident.observations]
    findings += [f("inference", i, f"Drawn by the correlation engine. Linkage: {incident.linkage_explanation}", core) for i in incident.inferences]
    findings += [f("conflict", c, "Contradictory records; the system does not decide which is correct.", conflicting or core) for c in incident.conflicts]
    findings += [f("missing", m, "Evidence the correlation engine looked for and did not find.", [], "high") for m in incident.missing_information]
    findings += [f("inference", u, "Uncertainty recorded by the correlation engine.", context or core, "low") for u in incident.uncertainty]
    findings += [f("recommendation", a, "Advisory; nothing is triggered automatically.", core, "medium") for a in incident.recommended_actions]
    priority = PrioritizedIncident(
        incident_id=incident.id, title=incident.title, category=incident.category, severity=incident.severity, status=incident.status,
        confidence=incident.confidence, confidence_level=incident.confidence_level, first_occurred_at=incident.first_occurred_at,
        last_occurred_at=incident.last_occurred_at, detected_at=incident.detected_at, event_count=incident.event_count,
        zones=facets["zone"], devices=facets["device"], sources=facets["source"], conflict_count=len(incident.conflicts or []),
        priority_score=scored.score, priority_rank=1, immediate_attention=scored.immediate,
        factors=[PriorityFactor(factor=a, points=b, explanation=c) for a, b, c in scored.factors], reason=scored.reason,
        new_in_period=False, detected_after_period=False, evidence=core[:5], recommended_actions=list(incident.recommended_actions or []),
    )
    return IncidentReasoning(incident_id=incident.id, reference_time=reference, priority=priority, findings=findings)
