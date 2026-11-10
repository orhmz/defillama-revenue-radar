"""Audit-only snapshots of complete DeFiLlama revenue source coverage.

This module never publishes candidates or notifications. Rolling totals and
completed UTC day observations are kept separate in its output.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from math import isfinite
from pathlib import Path
import sys
import urllib.request

from .core import DataError, parse_overview


FEEDS = {
    "holders_global": "https://api.llama.fi/overview/fees?dataType=dailyHoldersRevenue",
    "robinhood_fees": "https://api.llama.fi/overview/fees/robinhood-chain?dataType=dailyFees",
    "robinhood_protocol_revenue": "https://api.llama.fi/overview/fees/robinhood-chain?dataType=dailyProtocolRevenue",
    "robinhood_holders_revenue": "https://api.llama.fi/overview/fees/robinhood-chain?dataType=dailyHoldersRevenue",
}
LOOKBACK_DAYS = 30


def fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "defillama-revenue-radar/1.0"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def snapshot(payload: dict, *, source: str, today: date, fetched_at: str) -> dict:
    protocols, history, last_day = parse_overview(payload, today=today, require_latest=True)
    days = [last_day - timedelta(days=offset) for offset in range(LOOKBACK_DAYS - 1, -1, -1)]
    recent = {day.isoformat() for day in days[-14:]}
    names = {item["name"] for item in protocols.values()}
    usable_days = {datetime.fromtimestamp(timestamp, timezone.utc).date()
                   for timestamp, values in payload["totalDataChartBreakdown"]
                   if any(name in names and _number(value) is not None
                          for name, value in values.items())}
    missing_days = [day.isoformat() for day in days if day not in usable_days]
    if missing_days:
        raise DataError(f"Global chart is missing usable data for {missing_days[0]}")
    unmapped = sorted({name for timestamp, values in payload["totalDataChartBreakdown"]
                       if datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat() in recent
                       for name in values if name not in names})
    if unmapped:
        raise DataError(f"{len(unmapped)} recent chart names have no protocol ID")

    rows = []
    for identifier, item in protocols.items():
        if item.get("protocolType") != "protocol":
            continue  # A chain aggregate would duplicate its underlying protocols.
        series = history.get(item["name"], {})
        values = [series.get(day) for day in days]
        current7, previous7 = values[-7:], values[-14:-7]
        rows.append({
            "id": identifier, "name": item["name"], "slug": item.get("slug"),
            "chains": item.get("chains", []),
            "daily_completed_utc": {day.isoformat(): value for day, value in zip(days, values)},
            "complete_14d": all(value is not None for value in values[-14:]),
            "current_7d_usd": sum(current7) if all(value is not None for value in current7) else None,
            "previous_7d_usd": sum(previous7) if all(value is not None for value in previous7) else None,
            "rolling_at_fetch": {key: _number(item.get(key))
                                 for key in ("total24h", "total7d", "total30d")},
        })

    return {
        "source": source, "fetched_at_utc": fetched_at,
        "last_completed_utc_day": last_day.isoformat(),
        "rolling_values_are_not_closed_days": True,
        "coverage": {
            "scope": "Source metadata and observed daily history; missing protocol days are unknown",
            "metadata_protocols": len(protocols), "product_protocols": len(rows),
            "with_recent_daily_observation": sum(
                any(value is not None for value in row["daily_completed_utc"].values())
                for row in rows),
            "complete_14d_protocols": sum(row["complete_14d"] for row in rows),
            "incomplete_14d_protocols": sum(not row["complete_14d"] for row in rows),
            "daily_window_start": days[0].isoformat(),
            "daily_window_end": days[-1].isoformat(),
        },
        "protocols": rows,
    }


def collect(*, today: date, fetched_at: str, fetcher=fetch) -> dict:
    result = {"status": "source_validated", "fetched_at_utc": fetched_at,
              "candidate_scan_performed": False, "alerts_enabled": False,
              "feeds": {}, "errors": {}}
    for name, url in FEEDS.items():
        try:
            result["feeds"][name] = snapshot(
                fetcher(url), source=url, today=today, fetched_at=fetched_at)
        except (DataError, OSError, ValueError, KeyError, TypeError) as error:
            result["errors"][name] = str(error)
    if result["errors"]:
        result["status"] = "incomplete"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out/source_snapshot.json"))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    result = collect(today=now.date(), fetched_at=now.isoformat())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "feeds": list(result["feeds"]),
                      "errors": result["errors"]}, ensure_ascii=False))
    return 0 if result["status"] == "source_validated" else 1


if __name__ == "__main__":
    sys.exit(main())
