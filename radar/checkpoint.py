"""Restore the last published data day from a successful Actions report."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, timedelta
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from .core import DataError
from .notify import _request


REPORT_NAME = "revenue-radar-report"
REPORT_RETENTION_DAYS = 30


def _artifact_day(base: str, artifact_id: int, token: str) -> date:
    request = urllib.request.Request(f"{base}/actions/artifacts/{artifact_id}/zip")
    # The signed archive URL is on another host and does not need GitHub credentials.
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        archive_bytes = response.read()
    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            report = json.loads(archive.read("report.json"))
        return date.fromisoformat(report["as_of"])
    except (BadZipFile, KeyError, TypeError, ValueError) as error:
        raise DataError(f"Unreadable checkpoint in Actions artifact {artifact_id}") from error


def last_published_day(repo: str, token: str, *, today: date) -> date | None:
    base = f"https://api.github.com/repos/{repo}"
    since = today - timedelta(days=REPORT_RETENTION_DAYS)
    for page in range(1, 11):
        query = urllib.parse.urlencode({
            "branch": "main", "status": "success", "created": f">={since}",
            "per_page": 100, "page": page,
        })
        runs = _request(f"{base}/actions/workflows/radar.yml/runs?{query}", token)["workflow_runs"]
        for run in runs:
            if run["event"] not in {"schedule", "workflow_dispatch"}:
                continue
            run_url = f"{base}/actions/runs/{run['id']}"
            # Reading the current attempt is sufficient for both a scheduled run and a manual preview.
            jobs = _request(f"{run_url}/jobs", token)["jobs"]
            if not any(job["name"] == "notify" and job["conclusion"] == "success" for job in jobs):
                continue
            artifacts = _request(f"{run_url}/artifacts?name={REPORT_NAME}", token)["artifacts"]
            for artifact in artifacts:
                if artifact["name"] == REPORT_NAME and not artifact["expired"]:
                    return _artifact_day(base, artifact["id"], token)
        if len(runs) < 100:
            return None
    raise DataError("Could not verify the latest published Actions report")
