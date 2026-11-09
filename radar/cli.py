"""Fetch one public API response and write reviewable scan artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .checkpoint import last_published_day
from .core import DataError, analyze


URL = "https://api.llama.fi/overview/fees?dataType=dailyRevenue"


def fetch() -> dict:
    request = urllib.request.Request(URL, headers={"User-Agent": "defillama-revenue-radar/1.0"})
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status != 200:
                    raise DataError(f"DeFiLlama returned HTTP {response.status}")
                return json.load(response)
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt < 2:
                import time
                time.sleep(2 ** attempt)
    raise DataError(f"Could not fetch DeFiLlama data: {last_error}")


def markdown(report: dict) -> str:
    stats = report["stats"]
    days = report.get("processed_days", [report["as_of"]])
    lines = [f"# Revenue radar — {report['as_of']} UTC", "",
             f"Processed UTC days: {', '.join(days) or 'already current'}.", "",
             f"Scanned {stats['protocols']} entries; {stats['with_history']} had mapped history; "
             f"{stats['momentum_complete']} had complete 14-day protocol data.", "",
             "**Source:** [DeFiLlama dailyRevenue overview](https://api.llama.fi/overview/fees?dataType=dailyRevenue). "
             "Amounts are reported USD revenue. Token existence and on-chain activity were not independently verified.", ""]
    for kind, title in (("first_observed_revenue", "First observed revenue"),
                        ("momentum", "7-day momentum observations")):
        matches = [signal for signal in report["signals"] if signal["type"] == kind]
        lines += [f"## {title} ({len(matches)})", ""]
        if not matches:
            lines += ["No qualifying signals.", ""]
            continue
        amount_label = "Current 7d USD" if kind == "momentum" else "Observed since first USD"
        lines += [f"| Protocol | {amount_label} | Detail |", "| --- | ---: | --- |"]
        for signal in matches:
            name = str(signal["name"]).replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")
            if kind == "momentum":
                detail = f"previous ${signal['previous_7d_usd']:,.2f}; {signal['ratio']:.2f}×"
            else:
                detail = (f"first positive {signal['first_positive_day']}; "
                          f"{signal['prior_observed_days']} earlier observed days")
            amount = signal.get("current_7d_usd", signal.get("observed_since_first_usd"))
            lines.append(f"| {name} | ${amount:,.2f} | {detail} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("out"))
    parser.add_argument("--resume-github", action="store_true")
    args = parser.parse_args()
    try:
        today = datetime.now(timezone.utc).date()
        after = None
        if args.resume_github:
            repo = os.environ.get("GITHUB_REPOSITORY")
            token = os.environ.get("GITHUB_TOKEN")
            if not repo or not token:
                raise DataError("Missing GitHub workflow identity or token")
            after = last_published_day(repo, token, today=today)
        report = analyze(fetch(), today=today, after=after, require_latest=args.resume_github)
    except (ValueError, KeyError, TypeError, urllib.error.URLError, TimeoutError) as error:
        print(f"Scan failed closed: {error}", file=sys.stderr)
        return 1
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output / "report.md").write_text(markdown(report), encoding="utf-8")
    print(f"As of {report['as_of']}: {report['stats']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
