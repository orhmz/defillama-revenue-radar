"""Pure validation and signal detection for the public DeFiLlama revenue feed."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Any


FIRST_REVENUE_MIN_USD = 100
MOMENTUM_MIN_BASE_USD = 10
MOMENTUM_MIN_INCREASE_USD = 1_000
MOMENTUM_MIN_RATIO = 1.25
MOMENTUM_REARM_DAYS = 7


class DataError(ValueError):
    """The source cannot support a trustworthy scan."""


def _amount(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _day(timestamp: Any) -> date:
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise DataError("Chart timestamps must be integer Unix seconds")
    instant = datetime.fromtimestamp(timestamp, timezone.utc)
    if instant.hour or instant.minute or instant.second:
        raise DataError("Chart timestamps must be UTC midnight")
    return instant.date()


def parse_overview(
    payload: dict[str, Any], *, today: date, after: date | None = None,
    require_latest: bool = False,
) -> tuple[dict, dict, date]:
    """Return protocol metadata, explicit daily observations and latest full UTC day.

    Missing protocol entries stay missing. They are never converted to zero.
    """
    if (not isinstance(payload, dict) or not isinstance(payload.get("protocols"), list)
            or not payload["protocols"]):
        raise DataError("Missing protocols array")
    chart = payload.get("totalDataChartBreakdown")
    if not isinstance(chart, list) or not chart:
        raise DataError("Missing daily protocol breakdown")

    protocols: dict[str, dict] = {}
    names: set[str] = set()
    for item in payload["protocols"]:
        if not isinstance(item, dict):
            raise DataError("Malformed protocol metadata")
        identifier, name = item.get("id"), item.get("name")
        if not isinstance(identifier, str) or not identifier or not isinstance(name, str) or not name:
            raise DataError("Protocol missing stable ID or name")
        if identifier in protocols or name in names:
            raise DataError("Duplicate protocol ID or name; cannot map history safely")
        protocols[identifier] = item
        names.add(name)

    history: dict[str, dict[date, float]] = defaultdict(dict)
    chart_days: set[date] = set()
    observed_days: set[date] = set()
    for entry in chart:
        if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[1], dict):
            raise DataError("Malformed daily breakdown row")
        day = _day(entry[0])
        if day in chart_days or day > today:
            raise DataError("Duplicate or future chart day")
        chart_days.add(day)
        for name, raw in entry[1].items():
            if name not in names:
                continue  # A renamed or removed protocol cannot be mapped to an ID safely.
            number = _amount(raw)
            if number is not None:
                history[name][day] = number
                observed_days.add(day)

    eligible = sorted(day for day in chart_days if day < today)
    if not eligible:
        raise DataError("No completed UTC day")
    as_of = eligible[-1]
    yesterday = today - timedelta(days=1)
    if as_of < today - timedelta(days=2) or (require_latest and as_of != yesterday):
        raise DataError(f"Latest completed day {as_of} is stale")
    if after is not None and after > as_of:
        raise DataError(f"Feed ends on {as_of}, before the published checkpoint {after}")
    first_scan_day = min(as_of, after + timedelta(days=1)) if after else as_of
    required_start = first_scan_day - timedelta(days=21)
    for offset in range((as_of - required_start).days + 1):
        day = required_start + timedelta(days=offset)
        if day not in observed_days:
            raise DataError(f"Global chart is missing usable data for {day}")
    return protocols, dict(history), as_of


def _window(series: dict[date, float], end: date, days: int = 7) -> list[float] | None:
    values = [series.get(end - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    return None if any(value is None for value in values) else values


def _above_momentum_threshold(current: float, previous: float) -> bool:
    return (previous >= MOMENTUM_MIN_BASE_USD
            and current - previous >= MOMENTUM_MIN_INCREASE_USD
            and current >= previous * MOMENTUM_MIN_RATIO)


def _episode_start(series: dict[date, float], end: date) -> date | None:
    """Find the first crossing since a full week below the alert threshold."""
    start = end
    below = 0
    day = end
    while True:
        current = _window(series, day)
        previous = _window(series, day - timedelta(days=7))
        if current is None or previous is None:
            return None
        if _above_momentum_threshold(sum(current), sum(previous)):
            start = day
            below = 0
        else:
            below += 1
            if below == MOMENTUM_REARM_DAYS:
                return start
        day -= timedelta(days=1)


def _momentum(series: dict[date, float], end: date) -> dict[str, Any] | None:
    current = _window(series, end)
    previous = _window(series, end - timedelta(days=7))
    if current is None or previous is None:
        return None
    current_total, previous_total = sum(current), sum(previous)
    if (previous_total <= 0 or current_total <= previous_total or
            current_total < previous_total * MOMENTUM_MIN_RATIO or
            sum(value > 0 for value in current) < 4 or
            max(current) > current_total * 0.7):
        return None
    # Independent confirmation windows. Missing observations remain unknown.
    recent3 = _window(series, end, 3)
    prior3 = _window(series, end - timedelta(days=3), 3)
    recent14 = _window(series, end, 14)
    prior14 = _window(series, end - timedelta(days=14), 14)
    three_ratio = (sum(recent3) / sum(prior3)
                   if recent3 is not None and prior3 is not None and sum(prior3) > 0 else None)
    fourteen_pct = (round((sum(recent14) / sum(prior14) - 1) * 100, 2)
                    if recent14 is not None and prior14 is not None and sum(prior14) > 0 else None)
    # A rise that fails the short-term check is an observation, not a push alert.
    confirmation = "confirmed" if three_ratio is not None and three_ratio >= 1 else "unconfirmed"
    above_threshold = _above_momentum_threshold(current_total, previous_total)
    episode_start = _episode_start(series, end) if above_threshold else None
    return {
        "current_7d_usd": round(current_total, 2),
        "previous_7d_usd": round(previous_total, 2),
        "ratio": round(current_total / previous_total, 2),
        "change_usd": round(current_total - previous_total, 2),
        "change_pct": round(100 * (current_total / previous_total - 1), 2),
        "three_day_change_pct": round((three_ratio - 1) * 100, 2) if three_ratio is not None else None,
        "fourteen_day_change_pct": fourteen_pct,
        "confirmation": confirmation,
        "episode_start": episode_start.isoformat() if episode_start else None,
        "confidence": "medium" if episode_start and confirmation == "confirmed" else "low",
    }


def _first_revenue(series: dict[date, float], first: date | None, end: date) -> dict | None:
    if first is None or not end - timedelta(days=13) <= first <= end:
        return None
    observed = _window(series, end, (end - first).days + 1)
    if observed is None or sum(observed) <= 0:
        return None
    positive_days = sum(value > 0 for value in observed)
    prior_days = sum(day < first for day in series)
    qualifies = positive_days >= 2 and sum(observed) >= FIRST_REVENUE_MIN_USD and prior_days > 0
    return {
        "first_positive_day": first.isoformat(),
        "first_day_usd": round(series[first], 2),
        "observed_since_first_usd": round(sum(observed), 2),
        "positive_days": positive_days,
        "prior_observed_days": prior_days,
        "history_start": min(series).isoformat(),
        "observation_kind": "possible_first_revenue" if prior_days else "newly_observed_listing",
        "confidence": "medium" if qualifies else "low",
        "evidence": "first observed in DeFiLlama; historical backfill or earlier on-chain activity not excluded",
    }


def _record(by_event: dict[str, dict], signal: dict) -> None:
    """Use the latest observation while retaining eligible signals on catch-up."""
    key = signal["event_id"]
    previous = by_event.get(key)
    if previous is None or signal["confidence"] == "medium" or previous["confidence"] == "low":
        by_event[key] = signal


def analyze(
    payload: dict[str, Any], *, today: date, after: date | None = None,
    require_latest: bool = False,
) -> dict[str, Any]:
    protocols, history, as_of = parse_overview(
        payload, today=today, after=after, require_latest=require_latest,
    )
    start = after + timedelta(days=1) if after else as_of
    days = [start + timedelta(days=i) for i in range((as_of - start).days + 1)]
    by_event: dict[str, dict] = {}
    stats = {"protocols": len(protocols), "with_history": 0, "momentum_complete": 0}

    for identifier, protocol in protocols.items():
        if protocol.get("protocolType") != "protocol":
            continue  # Chain aggregates would double count underlying protocols.
        series = history.get(protocol["name"], {})
        if not series:
            continue
        stats["with_history"] += 1
        base = {
            "protocol_id": identifier,
            "name": protocol["name"],
            "slug": protocol.get("slug"),
            "category": protocol.get("category"),
            "chains": protocol.get("chains", []),
            "source_url": "https://defillama.com/fees/" + str(protocol.get("slug") or ""),
        }

        if _window(series, as_of, 14) is not None:
            stats["momentum_complete"] += 1
        first = min((day for day, amount in series.items() if amount > 0), default=None)
        for day in days:
            period_end = day.isoformat()
            first_revenue = _first_revenue(series, first, day)
            if first_revenue is not None:
                event_id = f"first:{identifier}:{first_revenue['first_positive_day']}"
                _record(by_event, {
                    **base, "type": "first_observed_revenue", "event_id": event_id,
                    "as_of": period_end, **first_revenue,
                })

            momentum = _momentum(series, day)
            if momentum is not None:
                event_id = f"momentum:{identifier}:{momentum['episode_start'] or period_end}"
                _record(by_event, {
                    **base, "type": "momentum", "event_id": event_id,
                    "as_of": period_end, **momentum,
                })

    signals = list(by_event.values())
    signals.sort(key=lambda x: (x["type"],
                                -x.get("current_7d_usd", x.get("observed_since_first_usd", 0)),
                                x["protocol_id"]))
    stats["first_revenue_candidates"] = sum(s["type"] == "first_observed_revenue" for s in signals)
    stats["momentum_candidates"] = sum(s["type"] == "momentum" for s in signals)
    return {"as_of": as_of.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(),
            "resumed_after": after.isoformat() if after else None,
            "processed_days": [day.isoformat() for day in days],
            "source": "https://api.llama.fi/overview/fees?dataType=dailyRevenue",
            "signal_definition": "DeFiLlama reported USD revenue; token and on-chain activity unverified",
            "stats": stats, "signals": signals}
