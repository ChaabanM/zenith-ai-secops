#!/usr/bin/env bash
# Core test command (requirements Section 9). Runs everything that exists so far:
#   1. backend test suite (Phase 1 regression, Phase 2 ingestion / retrieval /
#      generator / ground-truth integrity, Phase 3 quality rules / API / idempotency
#      and precision-recall floors against ground truth, Phase 4A correlation /
#      incidents API / reprocessing and related-vs-unrelated ground-truth checks,
#      Phase 4B priority sort / unresolved counts / timeline reliability flags,
#      Phase 5 briefing periods / grounding / prioritization / narrative fallback,
#      CORS settings parsing)
#   2. demo dataset ground-truth integrity check via the generator CLI
#   3. data-quality evaluation of the demo dataset against its ground truth
#   4. frontend component tests (vitest: incident overview, investigation view,
#      status controls, event -> incident links, briefing page) + type-check + build
#   5. optional: both 50,000-submission benchmarks incl. quality analysis,
#      incident correlation and briefings for every day and shift (--benchmark)
#
# Usage: ./evaluate.sh [--benchmark] [--skip-frontend]
# Later phases extend this script.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_BENCHMARK=0
SKIP_FRONTEND=0
for arg in "$@"; do
  case "$arg" in
    --benchmark) RUN_BENCHMARK=1 ;;
    --skip-frontend) SKIP_FRONTEND=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# Backend interpreter: the project venv when present, else python3 (with deps installed).
if [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
  PY="$ROOT/backend/.venv/bin/python"
else
  PY="$(command -v python3)"
fi
"$PY" -c "import fastapi, sqlalchemy, pytest, httpx" 2>/dev/null || {
  echo "Backend dependencies missing. Run: cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
}

step "Backend tests"
(cd "$ROOT/backend" && "$PY" -m pytest -q)

step "Demo dataset integrity (regenerate into a temp dir and verify)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
"$PY" "$ROOT/scripts/generate_events.py" --count 500 --seed 42 --format json \
  --output "$TMP/demo_events.json" --ground-truth "$TMP/demo_ground_truth.json" >/dev/null
cmp -s "$TMP/demo_events.json" "$ROOT/data/samples/demo_events.json" \
  && echo "demo_events.json is reproducible and its ground truth passes integrity checks" \
  || { echo "data/samples/demo_events.json differs from regenerated output" >&2; exit 1; }

step "Data-quality findings vs ground truth (demo dataset)"
"$PY" "$ROOT/scripts/evaluate_quality.py"

if [[ $SKIP_FRONTEND -eq 0 ]]; then
  step "Frontend tests, type-check and production build"
  if command -v npm >/dev/null 2>&1; then
    (cd "$ROOT/frontend" && { [[ -d node_modules ]] || npm ci; } && npm test && npm run build)
  else
    echo "npm not found: install Node.js 20.19+ / 22.12+ or rerun with --skip-frontend" >&2
    exit 1
  fi
fi

if [[ $RUN_BENCHMARK -eq 1 ]]; then
  step "50K benchmarks (clean + mixed): ingestion + quality analysis + correlation + briefings + dashboard through the real HTTP API"
  "$PY" "$ROOT/scripts/benchmark_ingest.py"
fi

step "All checks passed"
