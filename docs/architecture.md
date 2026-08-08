# Architecture

This document describes how data flows through the nine modules at runtime
and which contracts are stable.

## Two reading orders

- **By novelty axis** (matches the paper):
  1. `replanner/` + `signals/triggers/`: the real-time replanning loop.
  2. `fusion/` + `signals/sources/`: multi-signal context fusion.
- **By call order** (the usual way in for a first-time reader):
  1. `cli` → `replanner.initial.plan_initial`
  2. `retrieval` → `agent.score_pois` → `itinerary.greedy_plan`
  3. UI / loop driver → `replanner.replan` → `agent.rectifier.narrate_plan`

## Module map

```
cli ──┬──> replanner.initial ─┬──> retrieval.retrieve_candidates
      │                       ├──> agent.score_pois ──> llm
      │                       └──> itinerary.greedy_plan
      │
      └──> demo.run ──> replanner.loop.step ──> ...

replanner.loop.step ─┬──> signals.sources[*].fetch
                     ├──> fusion.user_state.estimate_user_state
                     ├──> fusion.fuser.fuse  ─────────> ContextSnapshot
                     ├──> signals.triggers.registry ──> [TriggerEvent]
                     └──> replanner.replan.replan ───> ReplanResponse

ui.server ── HTTP ──> POST /plan      ──> replanner.initial.plan_initial
            ── HTTP ──> POST /replan   ──> replanner.loop.step
            ── SSE  ──> GET  /events   ──> Session.queue
```

## Critical contracts

These four files are the wire; change them with care, regenerate the JSONL
fixtures if you do:

| File | Owner | Consumer |
| --- | --- | --- |
| `itinerary/types.py` (`Itinerary`, `POIVisit`, `PlanDiff`) | optimizer, replanner | UI, logger, tests |
| `fusion/snapshot.py` (`ContextSnapshot`, `WeatherReading`, ...) | fuser | replanner, triggers |
| `signals/triggers/types.py` (`TriggerEvent`, `TriggerKind`) | trigger registry | replanner, logger |
| `replanner/types.py` (`ReplanRequest`, `ReplanResponse`) | replanner | UI, loop driver |

## Trigger lifecycle

```
SignalSource.fetch ──> SignalBatch
                          │
                          ▼
                       fuser.fuse
                          │
                  ContextSnapshot
                          │
                          ▼
            triggers.registry.evaluate
                          │
            TriggerEvent (debounce + cooldown applied)
                          │
                          ▼
                replanner.replan
                          │
            ReplanResponse (PlanDiff + rationale)
                          │
                          ▼
        EventLog.write("replan", ...)   +   UI SSE push
```

A trigger only fires when its threshold has been crossed for `debounce_s`
continuous seconds AND the per-trigger `cooldown_s` since the last fire has
elapsed. Defaults are in `signals.triggers.registry.DEFAULT_RULES`. The
`tests/test_triggers.py::test_noisy_trace_does_not_thrash` test pins the
upper bound on replan count under noisy input.

## Ablation knobs

The paper makes two ablation claims; both are wired into the existing API:

- **Single-signal isolation**: pass `disable=("crowd",)` (or `"weather"`,
  `"transit"`) to `fusion.fuser.fuse`. The corresponding field on the
  produced `ContextSnapshot` is set to None / empty.
- **Loop disabled**: call `replanner.initial.plan_initial` once and never
  invoke `replanner.loop.step`. This is the "one-shot LLM tour generator"
  baseline the paper compares against.

## What is intentionally simple

Deliberate scope choices:

- Greedy orienteering, not ILP. The novelty is in the adaptive loop, not
  in optimisation; ILP is well-covered by prior work.
- Synthesised crowd readings. Real free crowd APIs are not reliable; the
  source field on each `CrowdReading` makes provenance explicit.
- Single-tenant FastAPI server. Multi-tenant is out of scope for the demo.
- Rule-based fatigue. A learned model would be a separate paper.

## Cross-cutting design surfaces

These four design choices span multiple modules. Each has its own dedicated
design note for the paper to cite:

- **Multi-city support** (`docs/cities.md`): five city catalogues, each
  with its own POI set, accommodation set, and per-city routing fare table.
  Loaded by `retrieval.poi_index.load_city` and
  `accommodations.index.load_accommodations`.
- **Realistic route geometry** (`docs/route_geometry.md`):
  `itinerary/geometry.py` upgrades straight-line Leaflet polylines to
  OSRM-routed paths, with an on-disk cache and graceful fallback. Runs as
  a post-process step after `greedy_plan` returns.
- **Reasoning surface split** (`docs/reasoning.md`): `POIVisit` now
  carries two reasoning fields: `reasoning_text` (human-readable, inline)
  and `reasoning_scores` (numeric trace, behind an info icon). Both are
  built deterministically inside `greedy_plan` from signals already known
  at pick time, with no extra LLM call per visit.
- **Preferences surface** (`docs/preferences.md`): six toggles in the UI
  form (kid-friendly, prefer low-carbon, require accessible route,
  sensory-friendly mode, pace, party size). Each maps onto a specific
  module: accessibility fields flow into `AccessibilityRequirements` and
  drive both `POIIndex.filter` and `pick_mode`; pace flows into the
  fatigue coefficient and per-stop buffer in `greedy_plan`.
