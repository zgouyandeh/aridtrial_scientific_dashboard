from aridtrial.qc.reconciliation import FILE_TARGETS, KOBO_TARGETS, _result


def test_reconciliation_result_has_explicit_difference_and_details():
    row = _result("sensor", "core_to_mart.row_count", "fail", 100, 97,
                  {"qc_excluded_records": 3})
    assert row["difference"] == -3
    assert row["details"]["qc_excluded_records"] == 3
    assert row["severity"] == "fail"


def test_reconciliation_maps_registered_file_and_kobo_datasets():
    assert FILE_TARGETS["sensors"] == "core.sensor_reading"
    assert FILE_TARGETS["plot_layout"] == "core.plot"
    assert KOBO_TARGETS["soil_sampling_v1"] == ("soil_samples", "core.soil_sample")
    assert KOBO_TARGETS["tree_survey_v1"] == ("tree_observations", "core.tree_obs")
