"""Export the dashboard's read-only API responses as static JSON for a GitHub Pages snapshot.

Reads the *latest* analysis run from the database (ARIDTRIAL_DSN) by calling the real FastAPI route functions and
encoding their results exactly as FastAPI does, so the snapshot is what the live dashboard would show
(no HTTP client dependency). Output: <out>/api/**.json   (default: frontend/dist-static/api)

    python scripts/export_static_site.py [--out frontend/dist-static]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.encoders import jsonable_encoder

from aridtrial.analysis.scientific import SECTIONS
from aridtrial.dashboard import app as api

ROUTES = {
    "health": api.health,
    "overview": api.overview,
    "reconciliation": api.reconciliation,
    "qc-issues": api.qc_issues,
    "analysis/runs": api.analysis_runs,
    "analysis/run": api.analysis_run,
    **{f"analysis/{s}": (lambda s=s: api.analysis_section(s)) for s in SECTIONS},
}
ENDPOINTS = list(ROUTES)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "frontend" / "dist-static"))
    out = Path(ap.parse_args(argv).out)
    for ep in ENDPOINTS:
        try:
            payload = jsonable_encoder(ROUTES[ep]())
        except Exception as exc:  # HTTPException (404/503) or database error
            raise SystemExit(f"/api/{ep} failed: {getattr(exc, 'detail', exc)}") from exc
        target = out / "api" / f"{ep}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    # in a snapshot there is exactly one run; keep the selector consistent
    runs = out / "api" / "analysis" / "runs.json"
    runs.write_text(json.dumps(json.loads(runs.read_text(encoding="utf-8"))[:1]), encoding="utf-8")
    print(f"exported {len(ENDPOINTS)} endpoints to {out / 'api'}")


if __name__ == "__main__":
    main()
