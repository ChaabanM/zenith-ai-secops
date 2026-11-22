"""Reasoning layer: grounding checks, optional narrative provider and its deterministic fallback, periods, prioritization."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.core.database import SessionLocal
from app.schemas.briefing import EvidenceRef
from app.services.briefing import periods
from app.services.briefing.configuration import BriefingSettings
from app.services.reasoning import narrative
from app.services.reasoning.configuration import ReasoningSettings
from app.services.reasoning.grounding import check_narrative, verify_references
from app.services.reasoning.prioritization import rank, score_incident
from tests.test_briefing import DAY_SHIFT, brief, scenario  # noqa: F401 (fixture)

CORPUS = "Day shift: 120 events from 7 devices. Needs attention first: #12 Door forced at Gate-A (critical, open). evt-abc123 radar-int05"


class FakeProvider:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.name, self.prompts = reply, error, "fake", []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.reply


# --------------------------------------------------------------------------- narrative grounding check


@pytest.mark.parametrize(
    "text,problem",
    [
        ("Incident #12 at Gate-A needs attention first; 120 events were recorded.", None),
        ("Incident #13 needs attention.", "unknown reference #13"),
        ("Event evt-zzz999 shows a forced door.", "unknown event id evt-zzz999"),
        ("radar-int09 is offline.", "unknown identifier radar-int09"),
        ("There were 450 events.", "number(s) not present"),
        ("", "empty output"),
        ("x" * 5000, "longer than"),
    ],
)
def test_check_narrative_rejects_anything_not_in_the_grounded_content(text, problem):
    result = check_narrative(text, CORPUS, 2000)
    if problem is None:
        assert result is None
    else:
        assert result is not None and problem in result


def test_deterministic_provider_by_default():
    out = narrative.compose("Template text with 3 events.", "Template text with 3 events.", {}, ReasoningSettings())
    assert out.generated_by == "deterministic_template" and out.provider == "deterministic" and out.fallback_reason is None


def test_grounded_model_output_is_used_and_labelled():
    fake = FakeProvider(reply="Attention first: #12 Door forced at Gate-A; 120 events in the shift.")
    with narrative.use_provider(fake):
        out = narrative.compose("Template", CORPUS, {"x": 1}, ReasoningSettings())
    assert out.generated_by == "local_language_model" and out.provider == "fake" and out.validated
    assert out.text.startswith("Attention first") and "do not add events" in fake.prompts[0]


@pytest.mark.parametrize(
    "fake,reason",
    [
        (FakeProvider(reply="Also evt-invented1 shows an intrusion."), "unknown event id"),
        (FakeProvider(reply="Incident #99 is critical."), "unknown reference #99"),
        (FakeProvider(reply="About 4000 events happened."), "number(s) not present"),
        (FakeProvider(error=TimeoutError("model timed out")), "TimeoutError"),
        (FakeProvider(error=ConnectionRefusedError("no server")), "ConnectionRefusedError"),
    ],
)
def test_unsupported_or_failed_model_output_falls_back_to_the_template(fake, reason):
    with narrative.use_provider(fake):
        out = narrative.compose("Deterministic text.", CORPUS, {}, ReasoningSettings())
    assert out.text == "Deterministic text." and out.generated_by == "deterministic_template"
    assert reason in out.fallback_reason


def test_ollama_provider_unreachable_falls_back(monkeypatch):
    s = ReasoningSettings(provider="ollama", ollama_url="http://127.0.0.1:9", timeout_seconds=0.5)
    out = narrative.compose("Deterministic text.", CORPUS, {}, s)
    assert out.text == "Deterministic text." and "provider ollama" in out.fallback_reason


def test_briefing_uses_provider_and_rejects_fabrication_end_to_end(scenario):  # noqa: F811
    with narrative.use_provider(FakeProvider(reply="Incident #987654 shows evt-made-up at Gate-Z.")):
        b = brief(scenario, **DAY_SHIFT)
    assert b["executive_summary"]["generated_by"] == "deterministic_template"
    assert "#987654" not in b["executive_summary"]["text"] and "rejected by the grounding check" in b["executive_summary"]["fallback_reason"]
    top = b["priority_incidents"][0]
    good = f"First priority: #{top['incident_id']} {top['title']}."
    with narrative.use_provider(FakeProvider(reply=good)):
        b2 = brief(scenario, **DAY_SHIFT)
    assert b2["executive_summary"]["generated_by"] == "local_language_model" and b2["executive_summary"]["text"] == good
    assert b2["evidence_index"] == b["evidence_index"]  # the model never changes the structured findings


# --------------------------------------------------------------------------- reference verification


def test_verify_references_flags_fabricated_and_mismatched_ids(scenario):  # noqa: F811
    event = scenario.get("/events", params={"page_size": 1}).json()["items"][0]
    with SessionLocal() as db:
        assert verify_references(db, [EvidenceRef(event_pk=event["id"], event_id=event["event_id"])], [], []) == []
        bad = verify_references(db, [EvidenceRef(event_pk=event["id"], event_id="evt-wrong"), EvidenceRef(event_pk=10**9, event_id="evt-x")],
                                [10**9], [10**9])
    assert len(bad) == 4 and any("evt-wrong" in b for b in bad) and "incident 1000000000" in bad and "quality issue 1000000000" in bad


# --------------------------------------------------------------------------- periods


def test_shift_containing_and_previous_shift_default():
    s, utc = BriefingSettings(), ZoneInfo("UTC")
    shift, day = periods.shift_containing(datetime(2026, 1, 15, 3, 0, tzinfo=UTC), s, utc)
    assert shift.name == "night" and day == date(2026, 1, 14)
    # latest event early in the night shift -> the previous (evening) shift is chosen
    p = periods.resolve("shift", utc, s, latest_event=datetime(2026, 1, 14, 23, 0, tzinfo=UTC))
    assert (p.start, p.end) == (datetime(2026, 1, 14, 14, tzinfo=UTC), datetime(2026, 1, 14, 22, tzinfo=UTC)) and p.default_reason
    p = periods.resolve("shift", utc, s, latest_event=datetime(2026, 1, 15, 3, 0, tzinfo=UTC))
    assert p.start == datetime(2026, 1, 14, 22, tzinfo=UTC)


def test_dst_day_has_23_hours():
    p = periods.resolve("day", ZoneInfo("Europe/London"), BriefingSettings(), day=date(2026, 3, 29))
    assert p.end - p.start == timedelta(hours=23)


def test_baselines_use_same_hours_on_previous_days():
    p = periods.resolve("shift", ZoneInfo("UTC"), BriefingSettings(), shift_name="day", day=date(2026, 1, 15))
    assert p.baselines(2)[0] == (datetime(2026, 1, 14, 6, tzinfo=UTC), datetime(2026, 1, 14, 14, tzinfo=UTC))
    week = periods.resolve("custom", ZoneInfo("UTC"), BriefingSettings(), start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 8, tzinfo=UTC))
    assert week.baselines(1)[0][1] == datetime(2026, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- prioritization


def _incident(i, severity="high", status="open", confidence=0.8, conflicts=(), category="perimeter_intrusion", hours_ago=1.0):
    ref = datetime(2026, 1, 15, 12, tzinfo=UTC)
    return SimpleNamespace(id=i, severity=severity, status=status, confidence=confidence, confidence_level="high", conflicts=list(conflicts),
                           category=category, last_occurred_at=ref - timedelta(hours=hours_ago)), ref


def test_priority_factors_and_tie_breaking():
    s = ReasoningSettings()
    crit, ref = _incident(1, "critical")
    high, _ = _incident(2, "high")
    resolved, _ = _incident(3, "critical", status="resolved")
    dup_a, _ = _incident(5, "medium")
    dup_b, _ = _incident(4, "medium")
    scored = [score_incident(x, ["radar", "anpr"], ref, 0, s) for x in (high, resolved, dup_a, crit, dup_b)]
    order = [x.incident.id for x in rank(scored)]
    assert order[:2] == [1, 2] and order[2:4] == [4, 5]  # equal scores: lower id first
    r = next(x for x in scored if x.incident.id == 3)
    assert r.score == 0 and not r.immediate and "resolved" in r.reason
    many = score_incident(_incident(6, "medium")[0], ["radar"] * 10, ref, 0, s)  # ten events of one source type add nothing
    assert not any(f[0] == "corroboration" for f in many.factors)
    conflicted = score_incident(_incident(7, conflicts=["a", "b", "c"])[0], ["radar"], ref, 0, s)
    assert dict((f[0], f[1]) for f in conflicted.factors)["unresolved_conflicts"] == 2 * s.conflict_points
