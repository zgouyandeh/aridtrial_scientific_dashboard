"""KoboToolbox API v2 download client and immutable raw-payload landing.

Credentials are supplied through KOBOTOOLBOX_TOKEN (or an explicit token argument).
This module only downloads and records source payloads; parsing and business-level
normalisation remain in pipeline.kobo_client.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import requests


class KoboDownloadError(RuntimeError):
    """Raised when a Kobo API response cannot be safely downloaded."""


class KoboClient:
    def __init__(self, server: str = "https://kf.kobotoolbox.org", token: str | None = None,
                 timeout: int = 60, session: requests.Session | None = None):
        self.server = server.rstrip("/")
        self.session = session or requests.Session()
        credential = token or os.environ.get("KOBOTOOLBOX_TOKEN")
        if credential:
            self.session.headers["Authorization"] = f"Token {credential}"
        self.timeout = timeout

    def iter_pages(self, asset_uid: str, page_size: int = 1000) -> Iterator[dict]:
        if not asset_uid.strip():
            raise ValueError("asset_uid must not be empty")
        if page_size < 1:
            raise ValueError("page_size must be positive")
        url = f"{self.server}/api/v2/assets/{asset_uid}/data/"
        params = {"format": "json", "limit": page_size}
        while url:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise KoboDownloadError(f"Unexpected Kobo API response for asset {asset_uid}")
            yield {"url": getattr(response, "url", url), "content": response.content,
                   "body": body, "status_code": response.status_code}
            next_url = body.get("next")
            if next_url:
                # Kobo's pagination URL should remain on the configured server.
                if urlparse(next_url).netloc and urlparse(next_url).netloc != urlparse(self.server).netloc:
                    raise KoboDownloadError("Kobo pagination returned a URL outside the configured server")
            url, params = next_url, None

    def fetch_submissions(self, asset_uid: str, page_size: int = 1000) -> Iterator[dict]:
        """Compatibility iterator yielding submissions from all API pages."""
        for page in self.iter_pages(asset_uid, page_size):
            yield from page["body"]["results"]

    def download_asset(self, asset_uid: str, form_id: str, output_dir: str | Path,
                       page_size: int = 1000) -> dict:
        """Save each raw API page, a combined API-shaped payload, and provenance metadata."""
        safe_uid = re.sub(r"[^A-Za-z0-9_.-]", "_", asset_uid)
        retrieved = datetime.now(timezone.utc)
        retrieved_at = retrieved.isoformat()
        snapshot_id = retrieved.strftime("%Y%m%dT%H%M%S%fZ")
        target = Path(output_dir) / safe_uid / snapshot_id
        pages_dir = target / "pages"
        pages_dir.mkdir(parents=True, exist_ok=False)
        submissions: list[dict] = []
        page_records = []
        for number, page in enumerate(self.iter_pages(asset_uid, page_size), start=1):
            raw = page["content"] or json.dumps(page["body"], ensure_ascii=False).encode("utf-8")
            page_name = f"page-{number:04d}.json"
            page_path = pages_dir / page_name
            page_path.write_bytes(raw)
            rows = page["body"]["results"]
            submissions.extend(rows)
            page_records.append({"page": number, "file": f"pages/{page_name}",
                                 "url": page["url"], "status_code": page["status_code"],
                                 "submission_count": len(rows), "sha256": hashlib.sha256(raw).hexdigest()})

        combined = {"count": len(submissions), "next": None, "previous": None, "results": submissions}
        combined_path = target / "submissions.json"
        combined_bytes = json.dumps(combined, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        combined_path.write_bytes(combined_bytes)
        submitted = [str(x.get("_submission_time")) for x in submissions if x.get("_submission_time")]
        metadata = {
            "source_type": "real", "source_name": "kobotoolbox_api_v2",
            "server": self.server, "asset_uid": asset_uid, "form_id": form_id,
            "retrieved_at": retrieved_at, "page_size": page_size,
            "page_count": len(page_records), "submission_count": len(submissions),
            "submission_time_min": min(submitted) if submitted else None,
            "submission_time_max": max(submitted) if submitted else None,
            "pages": page_records,
            "combined_payload": "submissions.json",
            "combined_payload_sha256": hashlib.sha256(combined_bytes).hexdigest(),
        }
        (target / "provenance.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"directory": str(target), "payload": str(combined_path), **metadata}
