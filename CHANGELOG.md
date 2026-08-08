# Changelog

All notable changes to AdaptTour are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-05-02

First tagged release. End-to-end Phase 1 plus docs and demo materials.

### Added

- Nine-module architecture mapped to the demo paper's two novelty axes.
  - `agent/`, `retrieval/`, `itinerary/`, `replanner/`, `signals/sources/`,
    `signals/triggers/`, `fusion/`, `ui/`, `logging_/`.
- Critical contracts: `Itinerary`, `POIVisit`, `PlanDiff`, `ContextSnapshot`,
  `WeatherReading`, `CrowdReading`, `TransitReading`, `UserState`,
  `TriggerEvent`, `ReplanRequest`, `ReplanResponse`.
- LLM provider Protocol + OpenAI implementation with cumulative usage + cost summary.
- Singapore POI catalogue bundled at `data/cities/singapore.json`.
- POI retrieval with alpha-weighted category alignment + structured filters
  (kid-friendly, indoor, open-at-hour).
- Greedy orienteering optimiser with fatigue penalty and locked-prefix
  partial-replan support.
- Single-day and multi-day itinerary planning; multi-day spans a start/end
  datetime with overnight breaks.
- Recorded JSONL signal source + Open-Meteo weather + synthetic crowd /
  transit sources.
- Trigger registry with per-trigger debounce + cooldown; tested against a
  noisy trace to bound thrashing.
- Multi-signal fusion with `disable=` ablation flags for the paper's
  signal-isolation study.
- Mid-trip replanner: locks executed prefix, swaps to indoor candidates
  under rain, downweights crowded POIs.
- Live loop driver consumed by both the CLI demo and the FastAPI UI.
- FastAPI server: `/healthz`, `/plan`, `/replan/{sid}`, `/events/{sid}` (SSE),
  `/sessions/{sid}` (DELETE), static one-page UI with Leaflet map.
- CLI: `adaptivetouragent.cli plan` and `adaptivetouragent.cli demo`.
- Three bundled scenarios: `family-rainy-day`, `solo-crowded-museum`,
  `couple-transit-disruption`.
- Documentation: `README.md` install + usage, `docs/architecture.md` runtime
  data flow, `demo/README.md` reviewer walkthrough, `demo/deployment.md`
  headless setup.

### Quality

- 49 tests pass; coverage 88% overall, 85%+ on critical modules.
- ruff and mypy clean.
- Per-session cost cap defaults to $0.50.

### Reuse

- Core LLM client, scoring loop, greedy orienteering, and routing/haversine adapted from the authors' prior tour-recommendation code.
- Agentic LLM Rectifier pattern from prior published work (PRICAI 2025).

### Not in this release

- True multi-user group preference fusion (a single shared profile is used;
  `family_size` scales the fatigue model and party-size spend).
- Real crowd APIs (synthesised, clearly labelled).
- Multi-tenant server (single-tenant by design for the demo).
