"""Optional GitHub Issue digest with event-level deduplication.

This module runs only in a separate, opt-in job with issues:write permission.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


MARKER = "radar-event:"
MAX_NEW_SIGNALS = 20


def _request(url: str, token: str, *, payload: dict | None = None) -> dict | list:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "defillama-revenue-radar/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def known_events(repo: str, token: str) -> set[str]:
    """Read open and closed issues, including older pages, before creating any digest."""
    found: set[str] = set()
    base = f"https://api.github.com/repos/{repo}/issues"
    for page in range(1, 101):
        query = urllib.parse.urlencode({"state": "all", "per_page": 100, "page": page})
        issues = _request(f"{base}?{query}", token)
        if not isinstance(issues, list):
            raise ValueError("Unexpected GitHub issue response")
        for issue in issues:
            if "pull_request" in issue:
                continue
            for line in (issue.get("body") or "").splitlines():
                if line.startswith(MARKER):
                    found.add(line[len(MARKER):].strip())
        if len(issues) < 100:
            return found
    raise ValueError("Issue history exceeds 10,000 entries; refusing to risk duplicate alerts")


def digest(report: dict, known: set[str]) -> tuple[str, str] | None:
    fresh = [signal for signal in report["signals"]
             if signal["event_id"] not in known and signal.get("confidence") != "low"]
    if not fresh:
        return None
    if len(fresh) > MAX_NEW_SIGNALS:
        raise ValueError(f"{len(fresh)} new signals exceed the {MAX_NEW_SIGNALS} issue safety cap")
    title = f"Revenue radar: {len(fresh)} new signal(s) through {report['as_of']} UTC"
    lines = ["DeFiLlama revenue candidates (not independently verified on-chain). Low-confidence observations are retained in the report but not pushed.", ""]
    for signal in fresh:
        if signal["type"] == "momentum":
            detail = (f"7d ${signal['current_7d_usd']:,.2f} vs prior "
                      f"${signal['previous_7d_usd']:,.2f} ({signal['change_pct']:+.2f}%)")
        else:
            detail = (f"first positive day {signal['first_positive_day']}; "
                      f"observed revenue ${signal['observed_since_first_usd']:,.2f} through {signal['as_of']}")
        lines += [f"- [{signal['name']}]({signal['source_url']}) — {detail}",
                  f"{MARKER}{signal['event_id']}"]
    lines += ["", f"Source: {report['source']}"]
    return title, "\n".join(lines)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo or "/" not in repo:
        print("Missing GitHub workflow identity or token", file=sys.stderr)
        return 1
    report = json.loads(Path("out/report.json").read_text(encoding="utf-8"))
    result = digest(report, known_events(repo, token))
    if result is None:
        print("No new events; no issue created")
        return 0
    title, body = result
    created = _request(f"https://api.github.com/repos/{repo}/issues", token,
                       payload={"title": title, "body": body,
                                "assignees": [repo.split("/", 1)[0]]})
    print(f"Created digest issue: {created['html_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
