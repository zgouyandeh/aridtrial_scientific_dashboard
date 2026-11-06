"""Offline tests for real KoboToolbox API pagination and raw landing."""
from __future__ import annotations

import json

from aridtrial.sources.kobo import KoboClient


class FakeResponse:
    def __init__(self, body, url):
        self._body = body
        self.url = url
        self.content = json.dumps(body).encode("utf-8")
        self.status_code = 200

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if len(self.calls) == 1:
            return FakeResponse({"count": 2, "next": "https://kf.kobotoolbox.org/api/v2/assets/asset-1/data/?page=2",
                                 "results": [{"_uuid": "u1", "_submission_time": "2026-01-01T00:00:00Z"}]}, url)
        return FakeResponse({"count": 2, "next": None,
                             "results": [{"_uuid": "u2", "_submission_time": "2026-01-02T00:00:00Z"}]}, url)


def test_kobo_client_paginates_and_preserves_raw_pages(tmp_path):
    session = FakeSession()
    client = KoboClient(token="secret-token", session=session)
    result = client.download_asset("asset-1", "soil_sampling_v1", tmp_path, page_size=1)

    assert session.headers["Authorization"] == "Token secret-token"
    assert len(session.calls) == 2
    assert session.calls[0][1] == {"format": "json", "limit": 1}
    assert session.calls[1][1] is None
    assert result["submission_count"] == 2
    assert result["page_count"] == 2

    root = __import__("pathlib").Path(result["directory"])
    payload = json.loads((root / "submissions.json").read_text())
    provenance = json.loads((root / "provenance.json").read_text())
    assert [x["_uuid"] for x in payload["results"]] == ["u1", "u2"]
    assert provenance["source_type"] == "real"
    assert provenance["source_name"] == "kobotoolbox_api_v2"
    assert provenance["form_id"] == "soil_sampling_v1"
    assert len(provenance["pages"]) == 2
    assert all((root / p["file"]).exists() for p in provenance["pages"])
    assert all(len(p["sha256"]) == 64 for p in provenance["pages"])


def test_fetch_submissions_compatibility_iterator():
    client = KoboClient(session=FakeSession())
    rows = list(client.fetch_submissions("asset-1", page_size=1))
    assert [x["_uuid"] for x in rows] == ["u1", "u2"]


def test_kobo_download_creates_immutable_snapshots(tmp_path):
    client = KoboClient(session=FakeSession())
    first = client.download_asset("asset-1", "soil_sampling_v1", tmp_path, page_size=1)
    second = client.download_asset("asset-1", "soil_sampling_v1", tmp_path, page_size=1)
    assert first["directory"] != second["directory"]
    assert __import__("pathlib").Path(first["directory"], "submissions.json").exists()
    assert __import__("pathlib").Path(second["directory"], "submissions.json").exists()
