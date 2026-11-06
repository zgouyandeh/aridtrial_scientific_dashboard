"""Prepare downloaded real Kobo submissions without modifying raw API payloads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from ..pipeline.kobo_client import envelope, load_mock, normalise_soil, normalise_trees, resolve_current
from ..simulate.kobo import SOIL_FORM_ID, TREE_FORM_ID


def _normalised_rows(payload_path: Path, form_id: str) -> list[dict]:
    submissions = load_mock(payload_path)
    rows = []
    for submission in submissions:
        rows.append(dict(envelope(submission, form_id), payload=submission))
    return rows


def _source_metadata(payload_path: Path, form_id: str) -> dict:
    provenance_path = payload_path.parent / "provenance.json"
    metadata = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}
    return {
        "source_type": "real", "source_name": "kobotoolbox_api_v2", "form_id": form_id,
        "asset_uid": metadata.get("asset_uid"), "raw_payload": str(payload_path),
        "raw_payload_sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
        "source_provenance": metadata,
    }


def prepare_kobo_downloads(soil_payload: str | Path, tree_payload: str | Path,
                           output_dir: str | Path) -> dict:
    """Flatten and revision-label real soil/tree submissions into separate prepared CSVs.

    Raw payloads are read-only inputs. This function does not merge these observations
    with the synthetic Kobo submissions and does not load anything into PostgreSQL.
    """
    soil_path, tree_path = Path(soil_payload), Path(tree_payload)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    soil = normalise_soil(_normalised_rows(soil_path, SOIL_FORM_ID))
    if not soil.empty:
        if soil[["plot_id", "campaign", "depth_top_cm"]].isna().any().any():
            raise ValueError("real Kobo soil submissions contain missing plot_id, campaign, or depth_top_cm")
        soil["sample_key"] = soil["plot_id"].astype(str) + "|" + soil["campaign"].astype(str) + "|" + soil["depth_top_cm"].astype(int).astype(str)
        soil = resolve_current(soil, "sample_key")
    soil_meta = _source_metadata(soil_path, SOIL_FORM_ID)
    soil["source_type"] = soil_meta["source_type"]
    soil["source_name"] = soil_meta["source_name"]
    soil["asset_uid"] = soil_meta["asset_uid"]
    soil.to_csv(out / "soil_samples_real.csv", index=False)

    trees = normalise_trees(_normalised_rows(tree_path, TREE_FORM_ID))
    if not trees.empty:
        if trees[["plot_id", "campaign", "tree_no"]].isna().any().any():
            raise ValueError("real Kobo tree submissions contain missing plot_id, campaign, or tree_no")
        trees["obs_key"] = trees["plot_id"].astype(str) + "|" + trees["campaign"].astype(str) + "|" + trees["tree_no"].astype(int).astype(str)
        trees["tree_id"] = trees["plot_id"].astype(str) + "-T" + trees["tree_no"].astype(int).map("{:02d}".format)
        trees = resolve_current(trees, "obs_key")
    tree_meta = _source_metadata(tree_path, TREE_FORM_ID)
    trees["source_type"] = tree_meta["source_type"]
    trees["source_name"] = tree_meta["source_name"]
    trees["asset_uid"] = tree_meta["asset_uid"]
    trees.to_csv(out / "tree_observations_real.csv", index=False)

    manifest = {
        "source_type": "real", "source_name": "kobotoolbox_api_v2",
        "soil": soil_meta, "trees": tree_meta,
        "outputs": {"soil": "soil_samples_real.csv", "trees": "tree_observations_real.csv"},
        "row_counts": {"soil_layers": len(soil), "tree_observations": len(trees)},
        "revision_policy": "Latest submission per business key is current; prior versions are retained as superseded.",
        "database_ingest": False,
        "separation_policy": "Real Kobo outputs remain separate from synthetic Kobo submissions until an explicit source-selection policy is applied.",
    }
    (out / "provenance.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"output_dir": str(out), "soil_rows": len(soil), "tree_rows": len(trees), "manifest": str(out / "provenance.json")}
