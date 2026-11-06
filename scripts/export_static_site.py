"""Export the dashboard's read-only API responses as static JSON for a GitHub Pages snapshot.

Reads the *latest* analysis run from the database (ARIDTRIAL_DSN) through the real FastAPI app, so the snapshot is
byte-for-byte what the live dashboard would show. Output: <out>/api/**.json   (default: frontend/dist-static/api)

    python scripts/export_static_site.py [--out frontend/dist-static]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from aridtrial.analysis.scientific import SECTIONS
from aridtrial.dashboard.app import app

ENDPOINTS = ["health", "overview", "reconciliation", "qc-issues", "analysis/runs", "analysis/run", *[f"analysis/{s}" for s in SECTIONS]]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "frontend" / "dist-static"))
    out = Path(ap.parse_args(argv).out)
    client = TestClient(app)
    for ep in ENDPOINTS:
        r = client.get(f"/api/{ep}")
        if r.status_code != 200:
            raise SystemExit(f"/api/{ep} -> {r.status_code}: {r.text[:200]}")
        target = out / "api" / f"{ep}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(r.json(), separators=(",", ":")), encoding="utf-8")
    # in a snapshot there is exactly one run; keep the selector consistent
    runs = out / "api" / "analysis" / "runs.json"
    runs.write_text(json.dumps(json.loads(runs.read_text(encoding="utf-8"))[:1]), encoding="utf-8")
    print(f"exported {len(ENDPOINTS)} endpoints to {out / 'api'}")


if __name__ == "__main__":
    main()
