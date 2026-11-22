"""End-to-end: generated datasets through POST /ingest match the ground truth record by record."""

import json
from pathlib import Path

from datagen.generator import Config, generate

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


def assert_matches_truth(client, summary, truth):
    outcomes = truth["expected_outcomes"]
    assert summary["total_received"] == outcomes["total_submissions"]
    assert summary["accepted"] == outcomes["accepted"]
    assert summary["invalid"] == outcomes["invalid"]
    assert summary["duplicate_event_ids"] == outcomes["duplicate"]
    assert summary["exact_duplicates"] == outcomes["exact_duplicate"]
    assert summary["conflicting_duplicates"] == outcomes["conflicting_duplicate"]

    stored = []
    page = 1
    while True:
        body = client.get("/submissions", params={"batch_id": summary["batch_id"], "page": page, "page_size": 500}).json()
        stored += body["items"]
        if not body["has_next"]:
            break
        page += 1
    assert len(stored) == len(truth["submissions"])
    accepted_pk_by_line = {}
    for sub, expected in zip(stored, truth["submissions"], strict=True):
        assert sub["position"] == expected["line"]
        assert sub["status"] == expected["expected_status"], (expected, sub["errors"])
        if sub["status"] == "accepted":
            accepted_pk_by_line[sub["position"]] = sub["normalized_event_id"]
            assert sub["external_event_id"] == expected["event_id"]
        if sub["status"] == "duplicate":
            assert sub["duplicate_kind"] == expected["duplicate_kind"]
            assert sub["normalized_event_id"] == accepted_pk_by_line[expected["duplicate_of_index"] + 1]

    facets = client.get("/events/facets").json()
    assert {f["value"]: f["count"] for f in facets["sources"]} == truth["source_counts"]


def test_mixed_jsonl_dataset_matches_ground_truth(client):
    dataset = generate(Config(count=2500, seed=11))
    truth = dataset.ground_truth("f")
    text = "\n".join(dataset.lines()) + "\n"
    response = client.post("/ingest", content=text.encode(), headers={"Content-Type": "application/x-ndjson"})
    assert response.status_code == 201
    assert_matches_truth(client, response.json(), truth)


def test_demo_json_dataset_matches_ground_truth(client):
    events = (SAMPLES / "demo_events.json").read_bytes()
    truth = json.loads((SAMPLES / "demo_ground_truth.json").read_text())
    response = client.post("/ingest", content=events, headers={"Content-Type": "application/json"})
    assert response.status_code == 201
    assert_matches_truth(client, response.json(), truth)


def test_reingesting_the_same_file_only_produces_duplicates(client):
    dataset = generate(Config(count=300, seed=4, profile="clean"))
    text = ("\n".join(dataset.lines()) + "\n").encode()
    headers = {"Content-Type": "application/x-ndjson"}
    assert client.post("/ingest", content=text, headers=headers).json()["accepted"] == 300
    again = client.post("/ingest", content=text, headers=headers)
    assert again.status_code == 200
    body = again.json()
    assert (body["accepted"], body["exact_duplicates"]) == (0, 300)
    assert client.get("/events").json()["total"] == 300
