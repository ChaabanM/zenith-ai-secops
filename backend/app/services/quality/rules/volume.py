"""H. repeated device events and I. sudden event spikes (event-time based).

Both use one streamed pass over events sorted by (key, occurred_at) with a sliding
window (deque), so cost is O(n) per dimension after the index-backed sort.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from app.models.ingestion import Event
from app.services.quality.rules.base import (
    EVENT_COLUMNS,
    QualityContext,
    Rule,
    add_facets,
    aggregate_time,
    event_evidence,
    fmt_duration,
    fmt_time,
)
from app.services.quality.schemas import IssueDraft


@dataclass
class Episode:
    key: str
    window: float
    events: dict[int, Any] = field(default_factory=dict)  # event pk -> row (insertion ordered)
    peak: int = 0
    last_qualifying: float = 0.0

    def add(self, rows: Any) -> None:
        for r in rows:
            self.events.setdefault(r.id, r)


class _WindowCounter:
    """Sliding event-time window for one key and one window size."""

    def __init__(self, key: Any, window: float, threshold: float):
        self.key, self.window, self.threshold = key, window, threshold
        self.buf: deque[Any] = deque()
        self.episode: Episode | None = None

    def feed(self, row: Any) -> Episode | None:
        t = row.occurred_at.timestamp()
        closed = None
        if self.episode and t > self.episode.last_qualifying + self.window:
            closed, self.episode = self.episode, None
        self.buf.append(row)
        while self.buf[0].occurred_at.timestamp() < t - self.window:
            self.buf.popleft()
        if len(self.buf) >= self.threshold:
            if self.episode is None:
                self.episode = Episode(key=self.key, window=self.window)
            self.episode.add(self.buf)
            self.episode.peak = max(self.episode.peak, len(self.buf))
            self.episode.last_qualifying = t
        return closed


def sliding_episodes(rows: Iterator[Any], key_of: Any, windows: list[float], threshold_of: Any) -> Iterator[Episode]:
    """One streamed pass over rows sorted by (key, occurred_at): yield maximal episodes, per window size,
    where a key has >= threshold_of(key, window) events within the window."""
    current_key: Any = object()
    counters: list[_WindowCounter] = []
    for row in rows:
        key = key_of(row)
        if key != current_key:
            yield from (c.episode for c in counters if c.episode)
            current_key = key
            counters = [_WindowCounter(key, w, threshold_of(key, w)) for w in windows]
        for counter in counters:
            closed = counter.feed(row)
            if closed:
                yield closed
    yield from (c.episode for c in counters if c.episode)


def poisson_threshold(expected: float, tail: float) -> int:
    """Smallest k with P(X >= k) < tail for X ~ Poisson(expected)."""
    if expected <= 0:
        return 1
    term = math.exp(-expected)  # P(X = 0)
    cdf, k = term, 0
    while 1.0 - cdf >= tail and k < 10_000:
        k += 1
        term *= expected / k
        cdf += term
    return k + 1


class RepeatedDeviceEventsRule(Rule):
    """H. The same non-routine alert repeated by one device within a short window."""

    rule_id = "REP-001"
    issue_type = "repeated_device_events"
    summary = "One device raised the same alert many times in a short period."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        s = ctx.settings
        stmt = (
            select(*EVENT_COLUMNS)
            .where(Event.event_type.not_in(s.telemetry_event_types), Event.severity.is_not(None), Event.severity != "informational")
            .order_by(Event.device_id, Event.event_type, Event.occurred_at, Event.id)
        )
        for ep in sliding_episodes(ctx.stream(stmt), lambda r: (r.device_id, r.event_type), [s.repeat_window_seconds], lambda _k, _w: s.repeat_min_count):
            rows = list(ep.events.values())
            device_id, event_type = ep.key
            start, end = rows[0].occurred_at, rows[-1].occurred_at
            draft = self.draft(
                f"{device_id}:{event_type}:{rows[0].id}",
                severity="medium" if ep.peak >= 2 * s.repeat_min_count else "low",
                title=f"{device_id} raised '{event_type}' {len(rows)} times in {fmt_duration((end - start).total_seconds())}",
                description=(
                    "A single device is repeating the same alert far more often than normal. This often indicates a nuisance "
                    "trigger or a faulty sensor; it can also be a genuine persistent condition. Operators should not treat "
                    "each repetition as a separate incident."
                ),
                detection_reason=(
                    f"{len(rows)} '{event_type}' events from {device_id} between {fmt_time(start)} and {fmt_time(end)}; at peak "
                    f"{ep.peak} within {fmt_duration(s.repeat_window_seconds)} (threshold {s.repeat_min_count}). Routine telemetry "
                    f"and informational events are excluded."
                ),
                occurred_from=start,
                occurred_to=end,
                confidence=0.9,
                details={"device_id": device_id, "event_type": event_type, "count": len(rows), "peak_in_window": ep.peak,
                         "window_seconds": s.repeat_window_seconds, "threshold": s.repeat_min_count},
                evidence=[event_evidence(r, "repeated_event") for r in rows],
            )
            yield add_facets(draft, rows)


KEY_OF = {
    "device": lambda r: (r.device_id,),
    "zone_event_type": lambda r: (r.zone, r.event_type),
    "zone": lambda r: (r.zone,),
    "event_type": lambda r: (r.event_type,),
}

DIMENSIONS = {
    "device": (lambda: (Event.device_id,), "device"),
    "zone_event_type": (lambda: (Event.zone, Event.event_type), "zone and event type"),
    "zone": (lambda: (Event.zone,), "zone"),
    "event_type": (lambda: (Event.event_type,), "event type"),
}


class EventSpikeRule(Rule):
    """I. Event volume for a device, zone, event type (or zone + type) far above its own baseline.

    Baseline: the key's average rate over the whole analysed period (event time),
    multiplied by a diurnal factor, gives the expected count per window. A window is
    a spike when its count reaches the Poisson upper-tail threshold for that
    expectation at ``spike_alpha / windows tested`` (Bonferroni-style correction, so
    across all keys and windows about ``spike_alpha`` false alarms are expected
    from normal randomness), and is at least ``spike_min_count`` and
    ``spike_ratio`` x expected.
    Dimensions are checked from most to least specific; an episode whose events are
    mostly already covered by a more specific spike is not reported again.
    """

    rule_id = "SPK-001"
    issue_type = "event_spike"
    summary = "Unusual increase in event volume for a device, zone or event type."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        s = ctx.settings
        first, last = (aggregate_time(v) for v in ctx.db.execute(select(func.min(Event.occurred_at), func.max(Event.occurred_at))).one())
        if first is None or last is None:
            return
        period = max((last - first).total_seconds(), max(s.spike_windows_seconds))
        covered: list[set[int]] = []
        for dim, (columns_of, label) in DIMENSIONS.items():
            cols = columns_of()
            totals = {tuple(r[:-1]): r[-1] for r in ctx.db.execute(select(*cols, func.count()).where(*(c.is_not(None) for c in cols)).group_by(*cols))}
            stmt = select(*EVENT_COLUMNS).where(*(c.is_not(None) for c in cols)).order_by(*cols, Event.occurred_at, Event.id)
            cache: dict[tuple, int] = {}

            def expected_of(key: tuple, window: float) -> float:
                return totals.get(key, 0) / period * window * s.spike_diurnal_factor

            def threshold(key: tuple, window: float) -> int:
                if (key, window) not in cache:
                    tests = max(1.0, len(totals) * period / window)  # windows examined for this dimension
                    expected = expected_of(key, window)
                    cache[(key, window)] = max(s.spike_min_count, math.ceil(s.spike_ratio * expected),
                                               poisson_threshold(expected, s.spike_alpha / tests))
                return cache[(key, window)]

            key_of = KEY_OF[dim]
            episodes = [(ep, expected_of(ep.key, ep.window), threshold(ep.key, ep.window))
                        for ep in sliding_episodes(ctx.stream(stmt), key_of, s.spike_windows_seconds, threshold)]
            for ep, expected, limit in self._merge(episodes):
                ids = set(ep.events)
                if any(len(ids & c) >= 0.6 * len(ids) for c in covered):
                    continue
                covered.append(ids)
                yield self._issue(dim, label, ep, expected, limit, s.spike_max_evidence)

    @staticmethod
    def _merge(items: list[tuple[Episode, float, int]]) -> list[tuple[Episode, float, int]]:
        """Merge overlapping episodes of the same key found with different window sizes."""
        items.sort(key=lambda x: (x[0].key, min(r.occurred_at for r in x[0].events.values())))
        merged: list[tuple[Episode, float, int]] = []
        for ep, expected, limit in items:
            start = min(r.occurred_at for r in ep.events.values())
            if merged and merged[-1][0].key == ep.key and start <= max(r.occurred_at for r in merged[-1][0].events.values()):
                prev = merged[-1][0]
                prev.add(ep.events.values())
                if ep.peak / max(expected, 1e-9) > prev.peak / max(merged[-1][1], 1e-9):
                    prev.window, prev.peak = ep.window, ep.peak
                    merged[-1] = (prev, expected, limit)
            else:
                merged.append((ep, expected, limit))
        for ep, _, _ in merged:
            ep.events = dict(sorted(ep.events.items(), key=lambda kv: (kv[1].occurred_at, kv[0])))
        return merged

    def _issue(self, dim: str, label: str, ep: Episode, expected: float, limit: int, cap: int) -> IssueDraft:
        rows = list(ep.events.values())
        start, end = rows[0].occurred_at, rows[-1].occurred_at
        key_text = " / ".join(str(k) for k in ep.key)
        normal = f"{expected:.1f}" if expected >= 0.1 else "<0.1"
        ratio = ep.peak / expected if expected > 0 else math.inf
        types: dict[str, int] = {}
        for r in rows:
            types[r.event_type] = types.get(r.event_type, 0) + 1
        top = ", ".join(f"{t} x{n}" for t, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))[:4])
        draft = self.draft(
            f"{dim}:{key_text}:{rows[0].id}",
            subtype=dim,
            severity="high" if ep.peak >= max(20, 2 * limit) else "medium",
            title=f"Spike at {label} {key_text}: {ep.peak} events in {fmt_duration(ep.window)} (normally {normal})",
            description=(
                "Event volume rose sharply above this key's normal rate. A spike is a signal to investigate (attack, fault, "
                "or a real cluster of activity); it is not by itself evidence of an incident."
            ),
            detection_reason=(
                f"Between {fmt_time(start)} and {fmt_time(end)}, {label} '{key_text}' produced {len(rows)} events (event time), "
                f"peaking at {ep.peak} within {fmt_duration(ep.window)}. Its baseline over the analysed period predicts about "
                f"{expected:.2f} per window (including a daytime factor); a count of {limit} or more is statistically implausible as normal variation. Event types: {top}."
            ),
            occurred_from=start,
            occurred_to=end,
            confidence=0.8 if ratio < 10 else 0.9,
            uncertainty="The baseline is the average rate over the loaded data; with little history it may be inaccurate.",
            details={"dimension": dim, "key": list(ep.key), "event_count": len(rows), "peak_in_window": ep.peak,
                     "window_seconds": ep.window, "expected_in_window": round(expected, 3), "threshold": limit,
                     "event_types": types, "evidence_truncated": len(rows) > cap},
            evidence=[event_evidence(r, "spike_event") for r in rows[:cap]],
        )
        return add_facets(draft, rows)
