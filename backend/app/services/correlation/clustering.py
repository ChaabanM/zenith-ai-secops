"""Evidence-gated incident construction.

Design against over-merging (see docs/correlation.md):

1. **Seeds, not edges.** An incident starts only from notable evidence: an
   enforcement violation corroborated by another sensor, an alarm, a credential
   attempt sequence, a device-health failure, an authentication burst, an
   identity conflict, impossible travel of one identifier, or a critical
   standalone alert. Routine observations (plate reads, positions, heartbeats,
   granted badges, face matches) never start an incident.
2. **Strong links grow the core.** Core members join only through a shared
   reliable identifier or an explicit reference (plate, radar track reference,
   credential, door, alarm segment, affected device, source address + account,
   report references), each inside a bounded time window. The vehicle-pass union
   rejects merges that would combine different plates, exceed the maximum span or
   imply impossible travel.
3. **Context only attaches leaves.** Camera detections, face captures and
   operator reports without a shared identifier attach as non-core *leaves* to
   exactly one incident; a leaf never recruits further events, so weak chains
   cannot connect two unrelated incidents. If two incidents could claim the same
   leaf, it is left unattached (ambiguous).
4. **One incident per event.** Builders run in a fixed order; an event already
   in an incident's core is not reused by later builders.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.services.correlation.candidate_selection import Candidates, Obs, TimeIndex, load_device_window
from app.services.correlation.configuration import CorrelationSettings
from app.services.correlation.constraints import (
    distance_km,
    full_plate,
    implied_speed_kph,
    low_confidence,
    plausible_movement,
    vehicle_details_conflict,
)
from app.services.correlation.scoring import LINK_TYPES, Member

ENFORCEMENT = ("traffic_violation", "mobile_phone_detection", "seatbelt_detection")
PERIMETER_ALARMS = ("fence_cut", "fence_climb", "beam_break")
# Report categories that may attach by context (zone + time) to an incident category. Other categories
# (for example vague "suspicious_activity" reports) attach only through explicit event references.
REPORT_CATEGORY_FIT = {
    "access_violation": {"unauthorized_access"},
    "perimeter": {"perimeter_intrusion", "emergency"},
    "maintenance": {"device_failure"},
}


@dataclass
class Group:
    kind: str  # builder that produced it
    category: str
    anchor: Obs
    members: dict[int, Member] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    order: int = 0

    def add(self, m: Member) -> None:
        if m.obs.pk not in self.members:
            self.members[m.obs.pk] = m
            if m.conflict and m.conflict not in self.conflicts:
                self.conflicts.append(m.conflict)

    @property
    def first(self) -> float:
        return min(m.obs.ts for m in self.members.values())

    @property
    def last(self) -> float:
        return max(m.obs.ts for m in self.members.values())

    def core(self) -> list[Member]:
        return [m for m in self.members.values() if m.core]


class Correlator:
    def __init__(self, db: Session, candidates: Candidates, settings: CorrelationSettings):
        self.db = db
        self.c = candidates.families
        self.s = settings
        self.groups: list[Group] = []
        self.claimed: dict[int, Group] = {}
        self.proposals: dict[int, list[tuple[Group, Member]]] = defaultdict(list)
        self.stats: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ helpers

    def _new(self, kind: str, category: str, anchor: Obs, explanation: str, role: str = "anchor") -> Group:
        g = Group(kind, category, anchor, order=len(self.groups))
        g.add(Member(anchor, role, "anchor", explanation))
        return g

    def _commit(self, g: Group) -> None:
        if any(pk in self.claimed for pk in g.members):  # never share core events between incidents
            g.members = {pk: m for pk, m in g.members.items() if pk not in self.claimed}
            if g.anchor.pk not in g.members:
                self.stats["dropped_overlapping_group"] += 1
                return
        self.groups.append(g)
        g.order = len(self.groups)
        for pk in g.members:
            self.claimed[pk] = g
        self.stats[f"group:{g.kind}"] += 1

    def _propose(self, g: Group, obs: Obs, role: str, link_type: str, explanation: str, conflict: str | None = None) -> None:
        # Low-confidence detections never become context for an incident just because they are nearby
        # (the failing device's own degraded output is the deliberate exception).
        if link_type != "affected_output" and low_confidence(obs, self.s.low_source_confidence):
            self.stats["contextual_rejected_low_confidence"] += 1
            return
        self.proposals[obs.pk].append((g, Member(obs, role, link_type, explanation, conflict, core=False)))

    def run(self) -> list[Group]:
        self.health_episodes()
        self.perimeter_alarms()
        self.access_attempts()
        self.auth_bursts()
        self.vehicle_passes()
        self.identity_conflicts()
        self.impossible_travel()
        self.standalone_alerts()
        self.operator_reports()
        self.resolve_proposals()
        return self.groups

    # ------------------------------------------------------------------ D. device health

    @staticmethod
    def _health_state(o: Obs) -> str | None:
        status = (o.get("status") or "").lower()
        if o.etype == "device_offline" or status == "offline":
            return "offline"
        if o.etype == "sensor_failure" or status in ("fault", "failed", "failure"):
            return "fault"
        if o.etype == "device_degraded" or status == "degraded":
            return "degraded"
        if o.etype == "device_recovered" or status == "online":
            return "recovered"
        return None

    def health_episodes(self) -> None:
        by_device: dict[str, list[Obs]] = defaultdict(list)
        for o in self.c["health"]:
            by_device[o.device].append(o)
        for device in sorted(by_device):
            events = by_device[device]
            episode: list[Obs] = []
            for o in events + [None]:  # sentinel flushes the last episode
                state = self._health_state(o) if o else None
                if episode and (o is None or o.ts - episode[-1].ts > self.s.health_episode_gap_seconds or self._health_state(episode[-1]) == "recovered"):
                    self._health_incident(episode)
                    episode = []
                if o is None:
                    break
                if state in ("degraded", "fault", "offline") or (episode and state == "recovered"):
                    episode.append(o)

    def _health_incident(self, episode: list[Obs]) -> None:
        states = [self._health_state(o) for o in episode]
        if not ({"offline", "fault"} & set(states)):
            self.stats["health_episode_without_failure"] += 1
            return
        anchor = episode[0]
        g = self._new("health", "device_failure", anchor, f"First reported problem for {anchor.device}: {anchor.etype}.", role="health_report")
        for o in episode[1:]:
            g.add(Member(o, "health_report", "same_affected_device",
                         f"Health report about the same affected device {o.device} ({o.etype}) within the same episode."))
        g.facts.update(device=anchor.device, states=states, recovered="recovered" in states,
                       monitor=next((o.get("monitor") for o in episode if o.get("monitor")), None),
                       codes=[o.get("code") for o in episode if o.get("code")])
        offline = next((o for o in episode if self._health_state(o) == "offline"), None)
        recovered = next((o for o in episode if self._health_state(o) == "recovered"), None)
        if offline and recovered:
            g.facts["downtime_seconds"] = recovered.ts - offline.ts
        self._commit(g)
        end = (offline or episode[-1]).t
        for out in load_device_window(self.db, anchor.device, anchor.t - timedelta(seconds=self.s.affected_output_lead_seconds), end,
                                      self.s.affected_output_confidence):
            self._propose(g, out, "possibly_affected_output", "affected_output",
                          f"Output of {out.device} with low confidence ({out.confidence}) while the device was degraded.")

    # ------------------------------------------------------------------ C. perimeter

    def perimeter_alarms(self) -> None:
        alarms = [o for o in self.c["perimeter"] if o.etype in PERIMETER_ALARMS or o.get("alarm_type") in PERIMETER_ALARMS
                  or o.severity in ("high", "critical")]
        panel_index = TimeIndex(self.c["emergency"], lambda o: o.get("input_point"))
        cctv_zone = TimeIndex(self.c["cctv"], lambda o: o.zone)
        open_groups: dict[tuple, Group] = {}
        for a in alarms:
            key = (a.zone, a.get("segment"))
            g = open_groups.get(key)
            if g and a.ts - g.last <= self.s.perimeter_merge_window_seconds:
                g.add(Member(a, "perimeter_alarm", "explicit_reference", f"Alarm on the same fence segment {a.get('segment')} within minutes."))
                continue
            g = self._new("perimeter", "perimeter_intrusion", a, f"{a.etype} alarm on segment {a.get('segment')} of {a.zone}.", role="perimeter_alarm")
            g.facts.update(zone=a.zone, segment=a.get("segment"), alarm_type=a.get("alarm_type") or a.etype)
            for p in panel_index.window(f"PIDS-{a.get('segment')}", a.ts, a.ts + self.s.panel_link_window_seconds):
                if p.zone == a.zone:
                    g.add(Member(p, "panel_alarm", "explicit_reference",
                                 f"Alarm panel input {p.get('input_point')} names the same fence segment {a.get('segment')}."))
            self._commit(g)
            open_groups[key] = g
            for cam in cctv_zone.window(a.zone, a.ts, a.ts + self.s.perimeter_verification_window_seconds):
                if cam.get("verification") == "no_activity":
                    self._propose(g, cam, "camera_verification", "contextual",
                                  f"Camera covering {a.zone} verified the scene {cam.ts - a.ts:.0f} s after the alarm.",
                                  conflict=f"{cam.device} reported no activity {cam.ts - a.ts:.0f} s after the {a.etype} alarm")
                elif cam.etype == "person_detected" and cam.ts - a.ts <= self.s.perimeter_camera_window_seconds:
                    self._propose(g, cam, "camera_detection", "contextual",
                                  f"Person detected by {cam.device} in {a.zone} {cam.ts - a.ts:.0f} s after the alarm (no shared identifier).")

    # ------------------------------------------------------------------ B. access attempts

    def access_attempts(self) -> None:
        s = self.s
        denials = [o for o in self.c["access"] if o.etype == "access_denied" and o.get("credential")]
        by_cred = TimeIndex(denials, lambda o: (o.zone, o.get("credential")))
        chains: list[list[Obs]] = []
        for key in sorted(by_cred.keys(), key=str):
            chain: list[Obs] = []
            for o in by_cred.all(key) + [None]:
                if chain and (o is None or o.ts - chain[-1].ts > s.denial_chain_gap_seconds):
                    if len(chain) >= s.denial_min_attempts:
                        chains.append(chain)
                    chain = []
                if o is not None:
                    chain.append(o)
        # a door alarm belongs to the attempt sequence on that door whose last attempt most recently precedes it
        door_of: dict[int, list[Obs]] = defaultdict(list)
        for d in (o for o in self.c["access"] if o.etype == "door_forced_open"):
            fits = [i for i, c in enumerate(chains) if c[0].get("door") == d.get("door") and c[0].ts <= d.ts <= c[-1].ts + s.door_forced_window_seconds]
            if fits:
                best = max(fits, key=lambda i: (min(chains[i][-1].ts, d.ts), -i))
                door_of[best].append(d)
        cctv_zone = TimeIndex([o for o in self.c["cctv"] if o.etype == "loitering_detected"], lambda o: o.zone)
        faces = TimeIndex([o for o in self.c["face"] if o.etype == "face_no_match"], lambda o: o.zone)
        for i, chain in sorted(enumerate(chains), key=lambda ic: (ic[1][0].t, ic[1][0].pk)):
            self._access_incident(chain, door_of.get(i, []), cctv_zone, faces)

    def _access_incident(self, chain: list[Obs], doors: list[Obs], cctv_zone: TimeIndex, faces: TimeIndex) -> None:
        s = self.s
        first, last = chain[0], chain[-1]
        cred = first.get("credential")
        g = self._new("access", "unauthorized_access", first, f"First denied attempt with credential {cred}.", role="access_attempt")
        for o in chain[1:]:
            g.add(Member(o, "access_attempt", "same_credential", f"Denied again with the same credential {cred} ({o.get('reason')})."))
        door = first.get("door")
        for d in doors:
            g.add(Member(d, "door_alarm", "same_door", f"Door {door} reported forced open {d.ts - last.ts:.0f} s after the last attempt."))
        g.facts.update(credential=cred, holder=first.get("holder"), zone=first.zone, door=door, attempts=len(chain),
                       reasons=sorted({str(o.get("reason")) for o in chain}))
        self._commit(g)
        end = max(m.obs.ts for m in g.members.values())
        for cam in cctv_zone.window(first.zone, first.ts, end + s.access_context_window_seconds):
            self._propose(g, cam, "camera_detection", "contextual",
                          f"{cam.etype.replace('_', ' ')} by {cam.device} at {cam.zone} during or shortly after the attempts (no shared identifier).")
        for f in faces.window(first.zone, last.ts, last.ts + s.face_context_window_seconds):
            self._propose(g, f, "face_capture", "contextual",
                          f"Face at {f.zone} not matched to any enrolled person {f.ts - last.ts:.0f} s after the last attempt.")

    # ------------------------------------------------------------------ J. authentication bursts

    def auth_bursts(self) -> None:
        s = self.s
        failures = [o for o in self.c["network"] if o.etype == "auth_failure" and o.get("src_ip")]
        successes = TimeIndex([o for o in self.c["network"] if o.etype == "auth_success"], lambda o: (o.get("src_ip"), o.get("username")))
        by_key = TimeIndex(failures, lambda o: (o.device, o.get("src_ip"), o.get("username")))
        for key in sorted(by_key.keys(), key=str):
            chain: list[Obs] = []
            for o in by_key.all(key) + [None]:
                if chain and (o is None or o.ts - chain[-1].ts > s.auth_burst_gap_seconds):
                    if len(chain) >= s.auth_burst_min_failures:
                        self._auth_incident(chain, successes)
                    chain = []
                if o is not None:
                    chain.append(o)

    def _auth_incident(self, chain: list[Obs], successes: TimeIndex) -> None:
        first, last = chain[0], chain[-1]
        ip, user = first.get("src_ip"), first.get("username")
        g = self._new("auth", "cyber_security", first, f"First failed login for {user} from {ip}.", role="failed_login")
        for o in chain[1:]:
            g.add(Member(o, "failed_login", "same_source_and_account", f"Failed login from the same address {ip} for the same account {user}."))
        ok = successes.window((ip, user), last.ts, last.ts + self.s.auth_success_window_seconds)
        for o in ok[:1]:
            g.add(Member(o, "successful_login", "same_source_and_account",
                         f"Successful login from the same address {ip} for {user} {o.ts - last.ts:.0f} s after the failures."))
        g.facts.update(src_ip=ip, username=user, failures=len(chain), success=bool(ok), device=first.device,
                       window_seconds=last.ts - first.ts)
        self._commit(g)

    # ------------------------------------------------------------------ A / E. vehicle passes

    def vehicle_passes(self) -> None:
        s = self.s
        plate_obs = [o for o in self.c["plate"] if o.source != "gps_fleet" and full_plate(o)]
        radar_index = TimeIndex(self.c["radar"], lambda o: (o.device, o.get("track")))
        radar_by_pk = {}
        edges: list[tuple[int, float, Obs, Obs, str, str | None, str]] = []  # (strength, time, a, b, type, conflict, explanation)

        for v in plate_obs:
            if v.source == "traffic_violation" and v.get("radar_track") and v.get("radar_device"):
                cands = radar_index.window((v.get("radar_device"), v.get("radar_track")), v.ts - s.radar_track_search_seconds, v.ts + s.radar_track_search_seconds)
                if cands:
                    r = min(cands, key=lambda o: (abs(o.ts - v.ts), o.pk))
                    radar_by_pk[r.pk] = r
                    gap = v.ts - r.ts
                    conflict = None
                    if abs(gap) > s.linked_timestamp_tolerance_seconds:
                        conflict = f"{v.device} and {r.device} disagree on the time of radar track {v.get('radar_track')} by {abs(gap):.0f} s"
                    elif v.get("speed") is not None and r.get("speed") is not None and abs(float(v.get("speed")) - float(r.get("speed"))) > s.linked_speed_tolerance_kph:
                        conflict = f"speed {v.get('speed')} km/h in the violation vs {r.get('speed')} km/h measured by the radar"
                    edges.append((0, min(v.ts, r.ts), r, v, "explicit_reference", conflict,
                                  f"The violation record cites radar {r.device} track {v.get('radar_track')}, measured by {r.event_id}."))

        by_plate = TimeIndex(plate_obs, full_plate)
        for plate in sorted(by_plate.keys()):
            seq = by_plate.all(plate)
            for a in seq:
                for b in by_plate.window(plate, a.ts, a.ts + s.movement_window_seconds):
                    if (b.ts, b.pk) <= (a.ts, a.pk):
                        continue
                    conflicts = vehicle_details_conflict(a, b)
                    conflict = "; ".join(conflicts) or None
                    if a.zone == b.zone and b.ts - a.ts <= s.same_site_plate_window_seconds:
                        edges.append((1, a.ts, a, b, "same_plate_same_site", conflict,
                                      f"Same plate {plate} read by {a.device} and {b.device} at {a.zone} {b.ts - a.ts:.0f} s apart."))
                    elif a.zone != b.zone and (distance_km(a, b) or 0) > 1.0 and plausible_movement(a, b, s.movement_max_speed_kph):
                        speed = implied_speed_kph(a, b)
                        edges.append((2, a.ts, a, b, "plausible_movement", conflict,
                                      f"Same plate {plate} at {b.zone} {(b.ts - a.ts) / 60:.1f} min after {a.zone} "
                                      f"({distance_km(a, b):.1f} km, about {speed:.0f} km/h)."))

        nodes = {o.pk: o for o in plate_obs} | radar_by_pk
        parent = {pk: pk for pk in nodes}
        comp: dict[int, dict[str, Any]] = {pk: {"plates": {full_plate(o)} - {None}, "lo": o.ts, "hi": o.ts, "located": [o] if o.lat is not None and o.source != "radar" else [],
                                               "links": {}} for pk, o in nodes.items()}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        edges.sort(key=lambda e: (e[0], e[1], e[2].pk, e[3].pk))
        for _strength, _t, a, b, ltype, conflict, text in edges:
            ra, rb = find(a.pk), find(b.pk)
            if ra == rb:
                continue
            ca, cb = comp[ra], comp[rb]
            if ca["plates"] and cb["plates"] and ca["plates"] != cb["plates"]:
                self.stats["rejected_merge_different_plates"] += 1
                continue
            if max(ca["hi"], cb["hi"]) - min(ca["lo"], cb["lo"]) > s.max_pass_span_seconds:
                self.stats["rejected_merge_span"] += 1
                continue
            if any((implied_speed_kph(x, y) or 0) > s.impossible_speed_kph and (distance_km(x, y) or 0) > s.impossible_min_distance_km
                   for x in ca["located"] for y in cb["located"]):
                self.stats["rejected_merge_impossible_travel"] += 1
                continue
            parent[rb] = ra
            ca["plates"] |= cb["plates"]
            ca["lo"], ca["hi"] = min(ca["lo"], cb["lo"]), max(ca["hi"], cb["hi"])
            ca["located"] += cb["located"]
            ca["links"].update(cb["links"])
            # remember how each endpoint was linked (first, i.e. strongest, link wins)
            ca["links"].setdefault(b.pk, (ltype, conflict, text))
            ca["links"].setdefault(a.pk, (ltype, conflict, text))

        components: dict[int, list[Obs]] = defaultdict(list)
        for pk, o in nodes.items():
            components[find(pk)].append(o)
        appearance = TimeIndex([o for o in self.c["cctv"] if o.etype == "vehicle_detected" and o.get("colour")], lambda o: o.zone)
        for root in sorted(components, key=lambda r: min((o.t, o.pk) for o in components[r])):
            obs = sorted(components[root], key=lambda o: (o.t, o.pk))
            devices = {o.device for o in obs}
            enforcement = [o for o in obs if o.source in ENFORCEMENT]
            if len(devices) < 2 or not enforcement:
                continue
            if any(o.pk in self.claimed for o in obs):
                continue
            anchor = min(enforcement, key=lambda o: (o.t, o.pk))
            g = self._new("vehicle", "traffic_enforcement", anchor, f"{anchor.etype.replace('_', ' ')} recorded by {anchor.device}.", role="violation")
            links = comp[root]["links"]
            for o in obs:
                if o is anchor:
                    continue
                ltype, conflict, text = links.get(o.pk, ("same_plate_same_site", None, "Linked through the same vehicle pass."))
                role = "violation" if o.source in ENFORCEMENT else "radar_measurement" if o.source == "radar" else "plate_read"
                g.add(Member(o, role, ltype, text, conflict))
            plates = sorted(comp[root]["plates"])
            details = next((o for o in obs if o.get("make")), None)
            g.facts.update(plate=plates[0] if plates else None, zone=anchor.zone, violations=sorted({o.etype for o in enforcement}),
                           vehicle={k: details.get(k) for k in ("make", "model", "colour", "vtype")} if details else {})
            self._commit(g)
            colour = (details.get("colour") if details else None)
            vtype = (details.get("vtype") if details else None)
            if colour:
                zones = {o.zone for o in obs}
                for zone in sorted(z for z in zones if z):
                    for cam in appearance.window(zone, comp[root]["lo"] - s.appearance_window_seconds, comp[root]["hi"] + s.appearance_window_seconds):
                        if str(cam.get("colour")).casefold() == str(colour).casefold() and (not vtype or cam.get("category") == vtype):
                            self._propose(g, cam, "camera_detection", "appearance_match",
                                          f"{cam.device} saw a {cam.get('colour')} {cam.get('category')} at {zone} within seconds of the pass (appearance only, no plate).")

    # ------------------------------------------------------------------ E. identity conflicts

    def identity_conflicts(self) -> None:
        s = self.s
        granted = [o for o in self.c["access"] if o.etype == "access_granted" and o.get("holder")]
        badges = TimeIndex(granted, lambda o: o.zone)
        faces_all = TimeIndex(self.c["face"], lambda o: o.zone)
        for f in self.c["face"]:
            if f.get("match_status") != "matched" or not f.get("subject") or (f.get("score") or f.confidence or 0) < s.identity_min_face_score:
                continue
            near_badges = badges.window(f.zone, f.ts - s.identity_pair_window_seconds, f.ts + s.identity_pair_window_seconds)
            near_faces = faces_all.window(f.zone, f.ts - s.identity_pair_window_seconds, f.ts + s.identity_pair_window_seconds)
            if len(near_badges) != 1 or len(near_faces) != 1:
                continue
            b = near_badges[0]
            if b.get("holder") == f.get("subject") or b.pk in self.claimed or f.pk in self.claimed:
                continue
            conflict = f"badge holder {b.get('holder')} vs face match {f.get('subject')} (score {f.get('score')})"
            g = self._new("identity", "unauthorized_access", b, f"Credential {b.get('credential')} of {b.get('holder')} granted at {b.zone}.",
                          role="badge_use")
            g.add(Member(f, "face_capture", "unambiguous_pairing",
                         f"The only face capture at {f.zone} within {s.identity_pair_window_seconds:.0f} s of the only badge use; "
                         "provisionally paired, not verified to be the same person.", conflict))
            g.facts.update(credential=b.get("credential"), holder=b.get("holder"), face_subject=f.get("subject"), zone=b.zone)
            self._commit(g)

    # ------------------------------------------------------------------ F / E. impossible travel of one identifier

    def impossible_travel(self) -> None:
        s = self.s
        located = [o for o in self.c["plate"] if full_plate(o) and o.lat is not None]
        by_plate = TimeIndex(located, full_plate)
        for plate in sorted(by_plate.keys()):
            for a in by_plate.all(plate):
                for b in by_plate.window(plate, a.ts, a.ts + s.impossible_window_seconds):
                    if b.device == a.device or (b.ts, b.pk) <= (a.ts, a.pk) or (a.pk in self.claimed and b.pk in self.claimed):
                        continue
                    allowance = 0.5 if "gps_fleet" in (a.source, b.source) else 0.0
                    km = (distance_km(a, b) or 0) - allowance
                    speed = implied_speed_kph(a, b, allowance)
                    if km <= s.impossible_min_distance_km or speed is None or speed <= s.impossible_speed_kph:
                        continue
                    category = "fleet_anomaly" if "gps_fleet" in (a.source, b.source) else "suspicious_activity"
                    conflict = f"plate {plate} at {a.zone} and {b.zone}, {distance_km(a, b):.1f} km apart, {b.ts - a.ts:.0f} s apart"
                    owner = self.claimed.get(a.pk) or self.claimed.get(b.pk)
                    if owner is not None:
                        # one side already belongs to an incident: surface the contradiction there instead of a new incident
                        other = b if a.pk in self.claimed else a
                        owner.add(Member(other, "contradicting_observation", "same_identifier_conflict",
                                         f"The same plate {plate} reported at {other.zone}, which is incompatible with the incident's location and time.",
                                         conflict))
                        self.claimed[other.pk] = owner
                        break
                    g = self._new("travel", category, a, f"{a.source} observation of {plate} at {a.zone}.", role="observation")
                    g.add(Member(b, "contradicting_observation", "same_identifier_conflict",
                                 f"The same plate {plate} reported at {b.zone} {b.ts - a.ts:.0f} s later.", conflict))
                    g.facts.update(plate=plate, distance_km=round(distance_km(a, b) or 0, 2), seconds=b.ts - a.ts,
                                   vehicle_id=a.get("vehicle_id") or b.get("vehicle_id"))
                    self._commit(g)
                    break
                if a.pk in self.claimed and self.claimed[a.pk].kind == "travel":
                    continue

    # ------------------------------------------------------------------ standalone critical alerts

    def standalone_alerts(self) -> None:
        for o in self.c["emergency"]:
            kind = str(o.get("alarm_type") or o.etype)
            if o.pk in self.claimed or "test" in kind or "test" in o.etype or o.get("state") != "active":
                continue
            g = self._new("alert", "emergency", o, f"Active {kind} alarm from {o.device}.", role="alarm")
            g.facts.update(zone=o.zone, alarm_type=kind)
            self._commit(g)
        for o in self.c["access"]:
            if o.etype == "door_forced_open" and o.pk not in self.claimed:
                g = self._new("alert", "unauthorized_access", o, f"Door {o.get('door')} reported forced open.", role="door_alarm")
                g.facts.update(zone=o.zone, door=o.get("door"))
                self._commit(g)
        for o in self.c["network"]:
            if o.severity in ("high", "critical") and o.pk not in self.claimed and o.etype != "auth_success":
                g = self._new("alert", "cyber_security", o, f"{o.etype} ({o.severity}) from {o.device}.", role="alert")
                g.facts.update(signature=o.get("signature"), src_ip=o.get("src_ip"))
                self._commit(g)

    # ------------------------------------------------------------------ operator reports

    def operator_reports(self) -> None:
        s = self.s
        by_event_id = {m.obs.event_id: g for g in self.groups for m in g.members.values()}
        for r in self.c["report"]:
            if r.pk in self.claimed:
                continue
            refs = [x for x in (r.get("refs") or []) if isinstance(x, str)]
            target = next((by_event_id[x] for x in refs if x in by_event_id), None)
            if target:
                target.add(Member(r, "operator_report", "explicit_reference",
                                  f"Operator report {r.event_id} explicitly references {', '.join(x for x in refs if x in by_event_id)}."))
                self.claimed[r.pk] = target
                continue
            fit = REPORT_CATEGORY_FIT.get(str(r.get("category")), set())
            candidates = [g for g in self.groups if g.anchor.zone == r.zone and g.category in fit
                          and g.first - s.report_window_before_seconds <= r.ts <= g.last + s.report_window_after_seconds]
            if len(candidates) == 1:
                g = candidates[0]
                self._propose(g, r, "operator_report", "contextual",
                              f"Operator report ({r.get('category')}) about {r.zone} {(r.ts - g.first) / 60:.1f} min after the incident began; no explicit reference.")
            elif len(candidates) > 1:
                self.stats["ambiguous_report"] += 1
            elif r.get("priority") in ("high", "critical"):
                category = {"access_violation": "unauthorized_access", "perimeter": "perimeter_intrusion"}.get(str(r.get("category")), "suspicious_activity")
                g = self._new("report", category, r, f"Operator report ({r.get('category')}, priority {r.get('priority')}).", role="operator_report")
                g.facts.update(zone=r.zone, report_category=r.get("category"))
                self._commit(g)

    # ------------------------------------------------------------------ contextual leaves

    def resolve_proposals(self) -> None:
        for pk in sorted(self.proposals):
            options = self.proposals[pk]
            if pk in self.claimed:
                continue
            groups = {id(g): (g, m) for g, m in options}
            if len(groups) != 1:
                self.stats["ambiguous_contextual_evidence"] += 1
                continue
            g, m = next(iter(groups.values()))
            g.add(m)
            self.claimed[pk] = g
            self.stats["contextual_attached"] += 1


def describe_link(link_type: str) -> str:
    return LINK_TYPES[link_type][2]


__all__ = ["Correlator", "Group", "describe_link", "low_confidence"]
