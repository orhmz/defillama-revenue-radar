"""Pure validation and signal detection for the public DeFiLlama revenue feed."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Any


SECONDS_PER_DAY = 86_400


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


def parse_overview(payload: dict[str, Any], *, today: date) -> tuple[dict, dict, date]:
    """Return protocol metadata, explicit daily observations and latest full UTC day.

    Missing protocol entries stay missing. They are never converted to zero.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("protocols"), list):
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

    eligible = sorted(day for day in chart_days if day < today)
    if not eligible:
        raise DataError("No completed UTC day")
    as_of = eligible[-1]
    if as_of < today - timedelta(days=2):
        raise DataError(f"Latest completed day {as_of} is stale")
    required_days = {as_of - timedelta(days=i) for i in range(22)}
    if not required_days.issubset(chart_days):
        raise DataError("Global chart has gaps in the recent 22 days")
    return protocols, dict(history), as_of


def _window(series: dict[date, float], end: date, days: int = 7) -> list[float] | None:
    values = [series.get(end - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    return None if any(value is None for value in values) else values


def _momentum(series: dict[date, float], end: date) -> dict[str, float] | None:
    current = _window(series, end)
    previous = _window(series, end - timedelta(days=7))
    if current is None or previous is None:
        return None
    current_total, previous_total = sum(current), sum(previous)
    if (previous_total < 100 or current_total < 1_000 or
            current_total - previous_total < 1_000 or
            current_total / previous_total < 2 or
            sum(value > 0 for value in current) < 5 or
            max(current) > current_total * 0.6):
        return None
    return {
        "current_7d_usd": round(current_total, 2),
        "previous_7d_usd": round(previous_total, 2),
        "ratio": round(current_total / previous_total, 2),
        "change_usd": round(current_total - previous_total, 2),
    }


def analyze(payload: dict[str, Any], *, today: date) -> dict[str, Any]:
    protocols, history, as_of = parse_overview(payload, today=today)
    signals: list[dict[str, Any]] = []
    stats = {"protocols": len(protocols), "with_history": 0, "momentum_complete": 0,
             "first_revenue_candidates": 0, "momentum_candidates": 0}

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

        positive_days = sorted(day for day, amount in series.items() if amount > 0)
        if positive_days:
            first = positive_days[0]
            observed_days = [series.get(first + timedelta(days=i))
                             for i in range((as_of - first).days + 1)]
            # A first observation can be a newly tracked protocol. The signal
            # deliberately makes no claim about on-chain launch or prior activity.
            if (as_of - timedelta(days=6) <= first <= as_of and
                    all(value is not None for value in observed_days) and
                    sum(value > 0 for value in observed_days) >= 2 and
                    sum(observed_days) >= 100):
                stats["first_revenue_candidates"] += 1
                signals.append({**base, "type": "first_observed_revenue",
                                "event_id": f"first:{identifier}:{first.isoformat()}",
                                "first_positive_day": first.isoformat(),
                                "as_of": as_of.isoformat(),
                                "observed_since_first_usd": round(sum(observed_days), 2),
                                "prior_observed_days": sum(day < first for day in series),
                                "history_start": min(series).isoformat()})

        if _window(series, as_of) is None or _window(series, as_of - timedelta(days=7)) is None:
            continue
        stats["momentum_complete"] += 1
        current_signal = _momentum(series, as_of)
        if current_signal is None:
            continue
        # Only report the beginning of a surge. Require the previous rolling
        # comparison to be complete so missing history cannot look like a crossing.
        if (_window(series, as_of - timedelta(days=1)) is None or
                _window(series, as_of - timedelta(days=8)) is None or
                _momentum(series, as_of - timedelta(days=1)) is not None):
            continue
        stats["momentum_candidates"] += 1
        signals.append({**base, "type": "momentum",
                        "event_id": f"momentum:{identifier}:{as_of.isoformat()}",
                        "as_of": as_of.isoformat(), **current_signal})

    signals.sort(key=lambda x: (x["type"],
                                -x.get("current_7d_usd", x.get("observed_since_first_usd", 0)),
                                x["protocol_id"]))
    return {"as_of": as_of.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "https://api.llama.fi/overview/fees?dataType=dailyRevenue",
            "signal_definition": "DeFiLlama reported USD revenue; token and on-chain activity unverified",
            "stats": stats, "signals": signals}
