# DeFiLlama revenue radar

This radar scans the public [DeFiLlama dailyRevenue overview](https://api.llama.fi/overview/fees?dataType=dailyRevenue) once a day. It reads the full protocol breakdown history from one API response, then writes a JSON report and a readable run summary. It uses Python's standard library only.

## Signals

- **First observed revenue.** A protocol qualifies for an alert after two positive completed UTC days totaling at least $100, with earlier observations in the feed and complete coverage since the first positive day. The first day must be within the latest 14 completed days of the evaluation. Newly tracked listings stay in the report for review. This is the first observation in DeFiLlama.
- **7-day momentum.** Compare two complete, consecutive seven-day windows. An alert needs at least 25% growth and a $1,000 increase, a prior total of at least $10, at least four positive current days, and at most 70% of the current total from one day. The latest three days must also hold or improve relative to the preceding three. An episode starts after two complete comparisons below threshold and keeps one event ID across later observations.

Chain aggregates are excluded to avoid double counting. Missing protocol entries remain unknown. Scheduled scans require the latest completed UTC day and fail if usable global data is missing across the period needed for the scan. Amounts reflect DeFiLlama's reported USD revenue. Independent confirmation of token contracts or on-chain payments requires separate evidence.

## Schedule and notifications

The [radar workflow](.github/workflows/radar.yml) is scheduled daily at 06:17 UTC on the default branch. It restores the last published data day from the report of a successful notification job and evaluates each missed completed day in order. A new setup without an available checkpoint starts with the latest complete day. Each scan adds a summary and stores `report.json` and `report.md` for 30 days.

Scheduled scans can publish one digest of up to 20 eligible events, assigned to the repository owner. A manual scan with `notify=false` previews the report. A manual run on `main` can publish by setting `notify=true`. The notification job checks event markers in open and closed bot digests, including markers created by earlier versions. A run with zero new events completes quietly, and additional candidates stay in the report. GitHub Mobile push delivery depends on the owner's notification settings.

The published thresholds prioritize material changes. Smaller signals and newly observed listings remain available for review in Actions.

## Run locally

```sh
python -m unittest discover -s tests -v
python -m radar.cli --output out
```

`out/` is generated locally and is excluded from Git. The diagnostic script and manual diagnostic workflow remain available for API troubleshooting.
