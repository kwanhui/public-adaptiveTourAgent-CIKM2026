---
title: AdaptTour
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Real-time adaptive tour recommendation with agentic AI
---

# AdaptTour: Real-Time Adaptive Tour Recommendation with Agentic AI

> **Status:** v0.1.0 (released 2026-05-02). Accepted at the CIKM 2026 Demonstration Track.
> **Demonstration video:** <https://youtu.be/qWlkl9_vnp8>
> **Hosted demo:** <https://kwanhui-adaptive-tour-agent.hf.space> (HF Spaces). This
> instance is provided on a best-effort basis and may be taken down; the
> repository is the durable artefact and runs locally, see Quick start below.

AdaptTour is a system for Real-Time
Adaptive Tour Recommendation with Agentic AI. It plans
personalised tours and re-plans them on the fly as user context changes:
weather shifts, queues grow, the user gets tired, the group changes its mind,
a venue closes early. An LLM-driven agent combines retrieval over POI metadata
with constraint-aware itinerary optimisation; users interact through a chat
panel and a synchronised map view.

This repository is the standalone, runnable release of the demo system. The
accompanying paper has been accepted; the DOI and proceedings link will be added
here once the proceedings appear.

## Companion Paper

- Kwan Hui Lim, Aldy Gunawan, and Carmen Kar Hang Lee. AdaptTour: Real-Time
  Adaptive Tour Recommendation with Agentic AI. **Accepted at the CIKM 2026
  Demonstration Track**, Rome, Italy, 7 to 11 November 2026. DOI and proceedings
  link to follow.
- Demonstration video: <https://youtu.be/qWlkl9_vnp8>

## Architecture

Nine modules, organised around the two novelty axes of the demo paper:

| Axis | Modules |
| --- | --- |
| Real-time replanning loop | `replanner/`, `signals/triggers/` |
| Multi-signal context fusion | `fusion/`, `signals/sources/` |
| Plan production | `agent/`, `retrieval/`, `itinerary/` |
| Surface | `ui/`, `logging_/` |

See the `docs/` directory for design notes the companion paper cites directly:

- `docs/architecture.md`: module map, trigger lifecycle, ablation knobs
- `docs/cities.md`: the five city catalogues + per-city fare tables
- `docs/route_geometry.md`: OSRM-routed polylines with on-disk cache
- `docs/reasoning.md`: split "Why this stop" surface (text + numeric)
- `docs/preferences.md`: every UI toggle and which layer it acts on

## Supported cities

Singapore, Melbourne, London, New York, Paris. Each ships its own POI set
(15 stops), accommodation set, fare table, and map centre; see
`docs/cities.md`.

## Install

```bash
git clone https://github.com/kwanhui/public-adaptiveTourAgent-CIKM2026.git
cd public-adaptiveTourAgent-CIKM2026

python3 -m venv venv
source venv/bin/activate
make install
```

Set `OPENAI_API_KEY` in a local `.env` file (never commit this):

```bash
echo "OPENAI_API_KEY=sk-..." > .env
export $(cat .env | xargs)
```

## Run the Demo (UI)

```bash
make demo
```

Open <http://localhost:8080>. Pick a profile preset (family / solo / couple),
generate a plan, then click one of the trigger buttons to inject a replan.
The map redraws in real time as the SSE stream pushes the updated itinerary.

## Run the Demo (CLI)

For a non-UI walkthrough, useful for reviewers who want to inspect the
JSONL event log:

```bash
make demo-cli SCENARIO=family-rainy-day LOG=/tmp/run.jsonl
grep '"event": "replan"' /tmp/run.jsonl | head
```

Three scenarios are bundled under `demo/sample-inputs/scenarios/`:

- `family-rainy-day`: weather shifts to rain at 11:30; agent replans to indoor.
- `solo-crowded-museum`: popular museum hits high-queue at midday; agent reorders.
- `couple-transit-disruption`: late-day MRT delay; agent swaps the tail.

## Library Usage

```python
from adaptivetouragent import plan_tour

itinerary = plan_tour(
    user_profile="demo/sample-inputs/profile-family.yaml",
    city="Singapore",
    days=1,
)
print(itinerary)
```

## Development

```bash
make test       # pytest
make lint       # ruff check
make typecheck  # mypy
make format     # ruff format
```

## Citation

```bibtex
@inproceedings{Lim2026AdaptiveTourAgent,
  title     = {AdaptTour: Real-Time Adaptive Tour Recommendation with Agentic AI},
  author    = {Lim, Kwan Hui and Gunawan, Aldy and Lee, Carmen Kar Hang},
  booktitle = {Proceedings of the 35th ACM International Conference on Information and Knowledge Management (CIKM '26), Demonstration Track},
  address   = {Rome, Italy},
  year      = {2026},
}
```

## License

See [LICENSE](LICENSE).
