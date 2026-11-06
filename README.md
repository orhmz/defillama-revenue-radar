# DeFiLlama revenue radar

This radar scans the public [DeFiLlama dailyRevenue overview](https://api.llama.fi/overview/fees?dataType=dailyRevenue) once a day. It reads the full protocol breakdown history from one API response, then writes a JSON report and a readable run summary. It uses Python's standard library only.

## Signals

- **First observed revenue:** a protocol's first positive daily entry in the available DeFiLlama history is within the latest seven completed UTC days; all days from that entry to the scan date are present; at least two are positive; and their total is at least $100. A newly tracked protocol can qualify. This does **not** establish its launch date or first on-chain revenue.
- **7-day momentum crossing:** the latest seven daily entries and the preceding seven are all present; prior revenue is at least $100; current revenue is at least $1,000 and at least $1,000 higher; growth is at least 2×; at least five current days are positive; and no single day contributes more than 60%. The previous rolling comparison must be complete and below this threshold, so an ongoing surge is not reported every day.

Chain aggregates are excluded to avoid double counting. Missing protocol entries are unknown, never zero. The scan fails if the global chart has recent date gaps or is more than two days old. Amounts are DeFiLlama's reported USD revenue; the radar does not verify token existence, token contracts, methodology, or on-chain activity.

## Schedule and notifications

The [radar workflow](.github/workflows/radar.yml) runs daily at 06:17 UTC on the default branch. Each successful run adds a report to the Actions summary and stores `report.json` and `report.md` as a 30-day artifact. It can also be started manually.

Notifications are **off by default**. To opt in for a single manual run, select the `notify` input. To opt in for scheduled runs, set repository variable `RADAR_CREATE_ISSUES` to `true`. The separate notification job has `issues: write` permission and creates at most one GitHub Issue digest per run. It reads all open and closed issue event markers first, so reruns and repeated first-revenue observations do not resend the same event. More than 20 new signals fail closed without creating an issue. The scan job has read-only permission. No external messaging credentials are required or used.

## Run locally

```sh
python -m unittest discover -s tests -v
python -m radar.cli --output out
```

`out/` is generated locally and is excluded from Git. The diagnostic script and manual diagnostic workflow remain available for API troubleshooting.
