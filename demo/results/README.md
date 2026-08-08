# Results

Drop CSVs of cost / latency per scenario here once produced from a real LLM
run. Each filename should encode the run's config:

```
{scenario}-{model}-{YYYYMMDD}-{commit-sha}.csv
```

Example: `family-rainy-day-gpt4o-mini-20260502-d69542e.csv`.

Required columns:

| Column | Type | Notes |
| --- | --- | --- |
| `scenario` | str | one of family-rainy-day, solo-crowded-museum, couple-transit-disruption |
| `model` | str | OpenAI model id |
| `event_no` | int | step index from 0 |
| `event_kind` | str | snapshot / trigger / replan |
| `wallclock_iso` | str | from the JSONL log |
| `latency_ms` | float | end-to-end LLM call latency, when applicable |
| `cost_usd_step` | float | cost of this step alone |
| `cost_usd_cum` | float | cumulative session cost |
| `n_replans_cum` | int | cumulative replan count |

Tag the git commit that produced each CSV (e.g. `results-2026-05-02-family`).
