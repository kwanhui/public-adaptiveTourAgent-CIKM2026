# Running the demo

This directory holds the sample inputs and notes for running and hosting the
system.

## Prerequisites

- Python 3.10+
- `OPENAI_API_KEY` exported in the shell

## One-command start

From the repo root:

```bash
make install   # once
make demo      # serves http://localhost:8080
```

Then in a browser:

1. **Pick a profile preset** (family / solo / couple).
2. **Click "Generate plan".** The agent calls the LLM to score POIs and the
   greedy orienteering optimiser routes them under the time budget. The map
   draws the route; the chat panel logs the initial plan.
3. **Click a trigger button** ("Rain started", "Kids tired", "Transit delay").
   The server fuses the synthetic signal sources into a `ContextSnapshot`,
   the trigger registry decides whether to fire, and the replanner updates
   the tail of the itinerary. The new plan pushes through the SSE stream and
   the map redraws.

## Bundled scenarios

Three recorded signal traces live under `sample-inputs/scenarios/`. The CLI
driver runs them headlessly:

```bash
make demo-cli SCENARIO=family-rainy-day LOG=/tmp/run.jsonl
grep '"event": "replan"' /tmp/run.jsonl
```

| Scenario | Trigger surface | Expected behaviour |
| --- | --- | --- |
| `family-rainy-day` | weather, crowd | Rain onset at 11:30 forces indoor-only candidates; agent swaps outdoor stops for museums and the ArtScience exhibition. |
| `solo-crowded-museum` | crowd | Two museums hit "high" crowd at 11:00; agent reorders to visit them after the queue subsides. |
| `couple-transit-disruption` | transit | MRT corridor between the zoo and Botanic Gardens is disrupted at 13:00; agent reroutes the day. |

## Hosted demo

- **URL**: <https://kwanhui-adaptive-tour-agent.hf.space> (HF Spaces; gallery: <https://huggingface.co/spaces/kwanhui/adaptive-tour-agent>)
- **Run it yourself**: clone the repo, set `OPENAI_API_KEY`, run `make demo`, and open `http://localhost:8080`. The bundled scenarios above exercise the replanning loop end to end.

## Cost guardrails

- Default model is `gpt-4o-mini` (cheapest in the bundled `OpenAIClient.PRICING` table).
- Per-session cost cap is `MAX_USD_PER_SESSION` (default `$0.50`); `/replan` returns 429 when crossed.
- LLM `temperature=0.0` for reproducibility.

A full walkthrough (initial plan + 1 replan) typically costs under $0.05 with
`gpt-4o-mini`.

## Deployment

For a hosted demo, see `deployment.md`.

## Event logs

The JSONL event logs capture the full run trail for analysis. Each log line is
one of:

- `snapshot`: fused `ContextSnapshot` at a given wall-clock
- `trigger`: a `TriggerEvent` that fired
- `replan`: a successful replan, with diff summary, rationale, and cost
- `plan_start` / `plan_done` / `demo_start` / `demo_done`: bookkeeping

Aggregated cost and latency CSVs land under `results/` once produced.
