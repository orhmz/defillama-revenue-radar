"""Publish a deduplicated GitHub Issue digest in the notification job."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
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
            author = (issue.get("user") or {}).get("login")
            if ("pull_request" in issue or author != "github-actions[bot]"
                    or not (issue.get("title") or "").startswith("Revenue radar:")):
                continue
            for line in (issue.get("body") or "").splitlines():
                if line.startswith(MARKER):
                    found.add(line[len(MARKER):].strip())
        if len(issues) < 100:
            return found
    raise ValueError("Issue history exceeds 10,000 entries; refusing to risk duplicate alerts")


def digest(report: dict, known: set[str]) -> tuple[str, str] | None:
    known_days = defaultdict(set)
    for event_id in known:
        identity, _, day = event_id.rpartition(":")
        known_days[identity].add(day)

    fresh = {}
    for signal in report["signals"]:
        event_id = signal["event_id"]
        if (event_id in known or signal.get("confidence") == "low"
                or signal.get("observation_kind") == "newly_observed_listing"):
            continue
        identity, _, _ = event_id.rpartition(":")
        prior_days = known_days.get(identity, set())
        if signal["type"] == "first_observed_revenue" and prior_days:
            continue  # A source backfill may revise the date of the same first observation.
        episode_start = signal.get("episode_start")
        if episode_start and any(episode_start <= day <= signal["as_of"] for day in prior_days):
            continue  # A digest produced by an older version may use a date within this episode.
        previous = fresh.get(event_id)
        if previous is None or signal["as_of"] > previous["as_of"]:
            fresh[event_id] = signal
    if not fresh:
        return None
    # Retain the remaining candidates in the Actions report.
    ranked = sorted(fresh.values(), key=lambda signal: (
        signal.get("type") != "first_observed_revenue",
        -signal.get("change_usd", signal.get("observed_since_first_usd", 0)),
        signal["event_id"],
    ))[:MAX_NEW_SIGNALS]
    title = f"Revenue radar: {len(ranked)} new signal(s) through {report['as_of']} UTC"
    lines = ["DeFiLlama revenue candidates (not independently verified on-chain). Low-confidence and newly listed observations are retained in the report but not pushed.", ""]
    for signal in ranked:
        if signal["type"] == "momentum":
            detail = (f"7d ${signal['current_7d_usd']:,.2f} vs prior "
                      f"${signal['previous_7d_usd']:,.2f} ({signal['change_pct']:+.2f}%; "
                      f"3d {signal.get('three_day_change_pct')}%; 14d {signal.get('fourteen_day_change_pct')}%)")
            if signal.get("episode_start"):
                detail += f"; episode started {signal['episode_start']}; data through {signal['as_of']}"
        else:
            detail = (f"first positive day {signal['first_positive_day']}; "
                      f"observed revenue ${signal['observed_since_first_usd']:,.2f} through {signal['as_of']}")
            if "positive_days" in signal:
                detail += f" ({signal['positive_days']} positive days)"
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
                       payload={"title": title, "body": body})
    print(f"Created digest issue: {created['html_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
