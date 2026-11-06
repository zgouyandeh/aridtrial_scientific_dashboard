"""KoboToolbox side of the simulator: XLSForm definitions and mock API v2 submissions.

Real Kobo exports return every value as a *string*, nest repeat groups as lists of dicts with
group-prefixed keys, and give edited submissions a new instanceID but the same rootUuid. The mock
reproduces those quirks (and a controlled set of data-entry faults) so the ingestion code is
exercised against realistic payloads.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from openpyxl import Workbook

from ..config import CAMPAIGNS, SOIL_LAYERS, TrialConfig

SOIL_FORM_ID = "soil_sampling_v1"
TREE_FORM_ID = "tree_survey_v1"
COLLECTORS = ["a.alhosani", "m.khan", "s.nasser", "l.farouk"]


# ----------------------------------------------------------------------------- XLSForm


def _write_form(path, form_id, title, survey, choices):
    wb = Workbook()
    ws = wb.active
    ws.title = "survey"
    cols = ["type", "name", "label", "required", "constraint", "constraint_message", "relevant", "repeat_count", "calculation"]
    ws.append(cols)
    for r in survey:
        ws.append([r.get(c, "") for c in cols])
    wc = wb.create_sheet("choices")
    wc.append(["list_name", "name", "label"])
    for r in choices:
        wc.append(r)
    wst = wb.create_sheet("settings")
    wst.append(["form_title", "form_id", "version", "default_language"])
    wst.append([title, form_id, "2026102701", "English (en)"])
    wb.save(path)


def build_xlsforms(layout: pd.DataFrame, out_dir) -> list:
    """Write the two XLSForms. Constraints encode the QC ranges so bad values are caught at entry."""
    plot_choices = [["plots", pid, pid] for pid in layout["plot_id"]]
    camp_choices = [["campaigns", c, c] for c in CAMPAIGNS]
    soil_survey = [
        dict(type="start", name="start"), dict(type="end", name="end"), dict(type="deviceid", name="deviceid"),
        dict(type="select_one plots", name="plot_id", label="Plot", required="yes"),
        dict(type="select_one campaigns", name="campaign", label="Sampling campaign", required="yes"),
        dict(type="date", name="sample_date", label="Sampling date", required="yes",
             constraint=". <= today()", constraint_message="Sampling date cannot be in the future"),
        dict(type="select_one collectors", name="collector", label="Collector", required="yes"),
        dict(type="geopoint", name="location", label="Plot centre GPS", required="yes"),
        dict(type="begin_repeat", name="layers", label="Soil layers", repeat_count="2"),
        dict(type="select_one depth_top", name="depth_top_cm", label="Layer top (cm)", required="yes"),
        dict(type="calculate", name="depth_bottom_cm", calculation="${depth_top_cm} + 15"),
        dict(type="decimal", name="soc_pct", label="Soil organic carbon (%)", required="yes",
             constraint=". >= 0.02 and . <= 5", constraint_message="SOC must be 0.02-5 %"),
        dict(type="decimal", name="ec_dsm", label="Salinity ECe (dS/m)", required="yes",
             constraint=". >= 0.1 and . <= 60", constraint_message="ECe must be 0.1-60 dS/m"),
        dict(type="decimal", name="ph", label="pH", required="yes",
             constraint=". >= 4 and . <= 11", constraint_message="pH must be 4-11"),
        dict(type="decimal", name="bulk_density", label="Bulk density (g/cm3)", required="yes",
             constraint=". >= 0.8 and . <= 2.0", constraint_message="Bulk density must be 0.8-2.0"),
        dict(type="end_repeat", name="layers"),
    ]
    soil_choices = plot_choices + camp_choices + [["collectors", c, c] for c in COLLECTORS] + [["depth_top", str(t), f"{t} cm"] for t, _ in SOIL_LAYERS]
    tree_survey = [
        dict(type="start", name="start"), dict(type="end", name="end"), dict(type="deviceid", name="deviceid"),
        dict(type="select_one plots", name="plot_id", label="Plot", required="yes"),
        dict(type="select_one campaigns", name="campaign", label="Campaign", required="yes"),
        dict(type="date", name="obs_date", label="Observation date", required="yes"),
        dict(type="geopoint", name="location", label="GPS", required="yes"),
        dict(type="begin_repeat", name="trees", label="Trees", repeat_count=str(layout["n_trees"].iloc[0])),
        dict(type="integer", name="tree_no", label="Tree no.", required="yes", constraint=". >= 1 and . <= 10"),
        dict(type="select_one yesno", name="alive", label="Alive?", required="yes"),
        dict(type="decimal", name="height_cm", label="Height (cm)", relevant="${alive} = 'yes'", required="yes",
             constraint=". >= 5 and . <= 600"),
        dict(type="decimal", name="stem_diam_mm", label="Basal stem diameter (mm)", relevant="${alive} = 'yes'", required="yes",
             constraint=". >= 1 and . <= 150"),
        dict(type="end_repeat", name="trees"),
    ]
    tree_choices = plot_choices + camp_choices + [["yesno", "yes", "Yes"], ["yesno", "no", "No"]]
    p1, p2 = out_dir / "soil_sampling.xlsx", out_dir / "tree_survey.xlsx"
    _write_form(p1, SOIL_FORM_ID, "Soil sampling", soil_survey, soil_choices)
    _write_form(p2, TREE_FORM_ID, "Tree survey", tree_survey, tree_choices)
    return [p1, p2]


# ----------------------------------------------------------------------------- submissions


def _meta(form_id, sub_id, root_uuid, instance_uuid, when, lat, lon, rng, collector):
    return {
        "_id": sub_id,
        "_uuid": str(instance_uuid),
        "meta/rootUuid": f"uuid:{root_uuid}",
        "meta/instanceID": f"uuid:{instance_uuid}",
        "formhub/uuid": uuid.uuid5(uuid.NAMESPACE_URL, form_id).hex,
        "_submission_time": when.strftime("%Y-%m-%dT%H:%M:%S"),
        "_submitted_by": collector,
        "_status": "submitted_via_web",
        "_validation_status": {},
        "_notes": [],
        "_tags": [],
        "_attachments": [],
        "__version__": "2026102701",
        "deviceid": f"collect:{uuid.uuid5(uuid.NAMESPACE_DNS, collector).hex[:12]}",
        "_geolocation": [round(lat + rng.normal(0, 2e-5), 6), round(lon + rng.normal(0, 2e-5), 6)],
    }


def make_submissions(cfg: TrialConfig, sim: dict, rng: np.random.Generator):
    """Return (soil_json, tree_json, faults) in KoboToolbox API v2 shape."""
    plots = sim["plots"].set_index("plot_id")
    soil, tree_obs = sim["soil"], sim["tree_obs"]
    faults: list[dict] = []
    soil_subs, tree_subs = [], []
    next_id = 100000

    for (pid, camp), g in soil.groupby(["plot_id", "campaign"]):
        next_id += 1
        root = uuid.uuid5(uuid.NAMESPACE_URL, f"{SOIL_FORM_ID}/{pid}/{camp}")
        inst = uuid.uuid5(uuid.NAMESPACE_URL, f"{SOIL_FORM_ID}/{pid}/{camp}/rev1")
        d = g["sample_date"].iloc[0]
        when = datetime(d.year, d.month, d.day, 11, int(rng.integers(0, 59)), tzinfo=timezone.utc) + timedelta(hours=int(rng.integers(0, 6)))
        coll = COLLECTORS[int(rng.integers(0, len(COLLECTORS)))]
        p = plots.loc[pid]
        sub = _meta(SOIL_FORM_ID, next_id, root, inst, when, p.lat, p.lon, rng, coll)
        sub.update({
            "plot_id": pid, "campaign": camp, "sample_date": d.isoformat(), "collector": coll,
            "location": f"{p.lat:.6f} {p.lon:.6f} {cfg.site_elevation_m:.1f} 4.0",
            "layers": [
                {
                    "layers/depth_top_cm": str(int(r.depth_top_cm)),
                    "layers/depth_bottom_cm": str(int(r.depth_bottom_cm)),
                    "layers/soc_pct": f"{r.soc_pct:.3f}", "layers/ec_dsm": f"{r.ec_dsm:.2f}",
                    "layers/ph": f"{r.ph:.2f}", "layers/bulk_density": f"{r.bulk_density:.3f}",
                } for r in g.sort_values("depth_top_cm").itertuples()
            ],
        })
        soil_subs.append(sub)

    for (pid, camp), g in tree_obs.groupby(["plot_id", "campaign"]):
        next_id += 1
        root = uuid.uuid5(uuid.NAMESPACE_URL, f"{TREE_FORM_ID}/{pid}/{camp}")
        d = g["obs_date"].iloc[0]
        when = datetime(d.year, d.month, d.day, 13, int(rng.integers(0, 59)), tzinfo=timezone.utc)
        coll = COLLECTORS[int(rng.integers(0, len(COLLECTORS)))]
        p = plots.loc[pid]
        sub = _meta(TREE_FORM_ID, next_id, root, root, when, p.lat, p.lon, rng, coll)
        sub.update({
            "plot_id": pid, "campaign": camp, "obs_date": d.isoformat(),
            "location": f"{p.lat:.6f} {p.lon:.6f} {cfg.site_elevation_m:.1f} 4.0",
            "trees": [
                {
                    "trees/tree_no": str(int(r.tree_no)), "trees/alive": "yes" if r.alive else "no",
                    **({"trees/height_cm": f"{r.height_cm:.1f}", "trees/stem_diam_mm": f"{r.stem_diam_mm:.1f}"} if r.alive else {}),
                } for r in g.sort_values("tree_no").itertuples()
            ],
        })
        tree_subs.append(sub)

    # ------------------------------------------------------------------ inject data-entry faults (soil)
    def pick(n, exclude=()):
        idx = [i for i in rng.permutation(len(soil_subs)) if i not in exclude]
        return idx[:n]

    used: set[int] = set()
    # 1. pH decimal-point slip
    for i in pick(2, used):
        used.add(i)
        soil_subs[i]["layers"][0]["layers/ph"] = f"{float(soil_subs[i]['layers'][0]['layers/ph']) * 10:.1f}"
        faults.append(dict(dataset="soil", key=f"{soil_subs[i]['plot_id']}|{soil_subs[i]['campaign']}|0", fault="ph_decimal_slip"))
    # 2. EC entered in uS/cm instead of dS/m
    for i in pick(2, used):
        used.add(i)
        v = float(soil_subs[i]["layers"][0]["layers/ec_dsm"])
        soil_subs[i]["layers"][0]["layers/ec_dsm"] = f"{v * 1000:.0f}"
        faults.append(dict(dataset="soil", key=f"{soil_subs[i]['plot_id']}|{soil_subs[i]['campaign']}|0", fault="ec_unit_error"))
    # 3. missing SOC
    for i in pick(3, used):
        used.add(i)
        soil_subs[i]["layers"][1]["layers/soc_pct"] = ""
        faults.append(dict(dataset="soil", key=f"{soil_subs[i]['plot_id']}|{soil_subs[i]['campaign']}|15", fault="missing_soc"))
    # 4. unknown plot id (typo)
    for i in pick(2, used):
        used.add(i)
        orig = soil_subs[i]["plot_id"]
        soil_subs[i]["plot_id"] = orig.replace("P", "P9") if "P" in orig else orig + "X"
        faults.append(dict(dataset="soil", key=f"{soil_subs[i]['plot_id']}|{soil_subs[i]['campaign']}|*", fault="unknown_plot"))
    # 5. future-dated sample
    for i in pick(1, used):
        used.add(i)
        soil_subs[i]["sample_date"] = "2027-03-15"
        faults.append(dict(dataset="soil", key=f"{soil_subs[i]['plot_id']}|{soil_subs[i]['campaign']}|*", fault="future_date"))
    # 6. bulk density implausible
    for i in pick(1, used):
        used.add(i)
        soil_subs[i]["layers"][0]["layers/bulk_density"] = "15.200"
        faults.append(dict(dataset="soil", key=f"{soil_subs[i]['plot_id']}|{soil_subs[i]['campaign']}|0", fault="bd_outlier"))
    # 7. exact re-submissions (different uuid, identical content): must be de-duplicated, not double counted
    dup_src = [i for i in pick(3, used)]
    for i in dup_src:
        used.add(i)
        s = dict(soil_subs[i])
        s["_id"] = max(x["_id"] for x in soil_subs) + 1
        s["_uuid"] = uuid.uuid4().hex
        s["meta/instanceID"] = f"uuid:{s['_uuid']}"
        s["meta/rootUuid"] = f"uuid:{s['_uuid']}"
        s["_submission_time"] = (datetime.fromisoformat(soil_subs[i]["_submission_time"]) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        soil_subs.append(s)
        faults.append(dict(dataset="soil", key=f"{s['plot_id']}|{s['campaign']}|*", fault="duplicate_submission"))
    # 8. edited submissions: rev1 has an EC typo, rev2 (same rootUuid, new instanceID) is the correction.
    for i in pick(2, used):
        used.add(i)
        rev2 = dict(soil_subs[i])
        rev2["layers"] = [dict(x) for x in soil_subs[i]["layers"]]
        wrong = dict(soil_subs[i])
        wrong["layers"] = [dict(x) for x in soil_subs[i]["layers"]]
        wrong["layers"][0]["layers/ec_dsm"] = f"{float(wrong['layers'][0]['layers/ec_dsm']) * 2.5:.2f}"
        new_inst = uuid.uuid5(uuid.NAMESPACE_URL, f"edit/{soil_subs[i]['_uuid']}").hex
        rev2["_uuid"], rev2["meta/instanceID"] = new_inst, f"uuid:{new_inst}"
        rev2["_submission_time"] = (datetime.fromisoformat(soil_subs[i]["_submission_time"]) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        soil_subs[i] = wrong
        soil_subs.append(rev2)
        faults.append(dict(dataset="soil", key=f"{wrong['plot_id']}|{wrong['campaign']}|0", fault="edited_submission"))

    # ------------------------------------------------------------------ tree faults
    ti = [int(i) for i in rng.permutation(len(tree_subs))[:3]]
    tree_subs[ti[0]]["plot_id"] = "B0-P1"
    faults.append(dict(dataset="tree", key=f"B0-P1|{tree_subs[ti[0]]['campaign']}|*", fault="unknown_plot"))
    tree_subs[ti[1]]["trees"][2]["trees/height_cm"] = "1050.0"
    tree_subs[ti[1]]["trees"][2]["trees/alive"] = "yes"
    faults.append(dict(dataset="tree", key=f"{tree_subs[ti[1]]['plot_id']}|{tree_subs[ti[1]]['campaign']}|3", fault="height_outlier"))
    return (
        {"count": len(soil_subs), "next": None, "previous": None, "results": soil_subs},
        {"count": len(tree_subs), "next": None, "previous": None, "results": tree_subs},
        pd.DataFrame(faults),
    )
