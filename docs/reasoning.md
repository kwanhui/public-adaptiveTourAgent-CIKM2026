# Per-visit reasoning surface

The original demo carried one rationale string per `POIVisit`:

```
score=0.84 (base=0.94, fatigue=0.90); transit 2.7km; step_cost=$1.62
```

This was readable to a developer but not to an end user: it read as a debug
log, not as agentic reasoning. It is now split into two complementary fields:

| Field | Purpose | Where surfaced |
|-------|---------|----------------|
| `reasoning_text` | Human-readable, 2-4 sentences | Inline under "Why this stop:" on the visit card |
| `reasoning_scores` | Numeric trace, identical format to the original | Behind an `ⓘ` info icon next to "Why this stop:" |

Both are built inside `greedy_plan` at the point a candidate is committed
to the plan, using only signals already computed in the greedy step, with no
extra LLM call per visit.

## Numeric trace (`reasoning_scores`)

Unchanged from the original, so existing tooling that grepped for `score=`
keeps working. Format:

```
score={adjusted:.2f} (base={base:.2f}, fatigue={fatigue:.2f}); {mode} {dist:.1f}km; co2e={co2e:.2f}kg; step_cost=${step:.2f}
```

The `co2e=` segment is only present when `prefer_low_carbon=True` (the carbon
factor is what made it into the score then); the `step_cost=` segment is
only present when the step had a cost. The mode segment is dropped for the
first visit (no inbound leg).

## Human-readable text (`reasoning_text`)

Composed by `_build_reasoning_text` (`itinerary/optimizer.py`) from three
sentence templates:

### 1. Why this POI

Reports the position of this pick relative to the runners-up considered at
the same greedy step:

- **Clear winner** (runner-up gap ≥ 0.10):
  > Top fit among remaining {category} candidates, scored {Δ:.2f} above the
  > runner-up ({runner_up_name}).
- **Near tie** (runner-up gap < 0.10):
  > Strongest near-tie among remaining {category} candidates (versus
  > {runner_up_name}).
- **No alternatives left**:
  > Highest-scoring remaining {category} stop for this profile.

### 2. Why now

Reports where in the day this visit lands and whether fatigue mattered:

- **Late in day with fatigue effect** (progress > 60%, fatigue < 0.85):
  > Late in the day, so fatigue reduced its base score by ~{drop:.2f} pts;
  > still the best remaining slot.
- **Opening slot** (progress < 20%):
  > Opening slot, starts the day on a high-base-score anchor.
- **Otherwise**:
  > Fits the remaining time window with margin.

### 3. Why this mode

Reports the inbound transport leg and the active driver. Multiple drivers
can compose:

- `require_wheelchair=True` and mode ≠ cycle → "step-free routing required"
- `prefer_low_carbon=True` and mode ∈ {walk, cycle, transit} → "low-carbon preference active"
- mode ∈ {rideshare, drive} with money budget set → "budget headroom (~${remaining:.0f} left)"
- Otherwise → falls back to a generic per-mode rationale (`_MODE_DRIVER`):
  - `walk` → "short hop, no fare needed"
  - `cycle` → "medium hop with low-carbon preference active"
  - `transit` → "default for longer hops when rideshare isn't budget-justified"
  - `rideshare`/`drive` → "fastest option for this distance, budget headroom available"

For the first visit of the day (no inbound leg) this sentence is replaced
by "First stop of the day, no inbound leg."

### 4. Cost note (conditional)

When a money budget is set and the step had a cost:
> Marginal cost ${step:.2f} fits within the remaining money budget.

## Why template-built rather than LLM-authored

- **Cost**. The current per-plan cost is ~$0.0001 (LLM scoring only). An
  LLM call per visit would raise this 5-10× depending on plan length.
- **Latency**. The reasoning text needs to be present in the SSE `replan`
  event with no extra round-trip. Template generation is sub-millisecond.
- **Determinism for testing**. The unit test
  `test_iterations.py::test_iter3_reasoning_includes_score_and_alternatives`
  asserts on substring matches; a non-deterministic LLM output would
  require either rich mocking or skipping these tests.
- **Faithfulness**. The template uses exactly the signals the optimiser
  used; there is no possibility of the prose "explaining" a different
  decision from the one made.

## Example

Plan for a London family of 2, 9am-5pm, 2026-06-01:

```
10:00 London Eye
  text:   Strongest near-tie among remaining viewpoint candidates (versus British
          Museum). Opening slot, starts the day on a high-base-score anchor.
          First stop of the day, no inbound leg.
  scores: score=0.94 (base=0.94, fatigue=1.00); step_cost=$84.00

11:13 British Museum
  text:   Strongest near-tie among remaining museum candidates (versus Tower of
          London). Fits the remaining time window with margin. Reached by metro/bus
          (2.7 km), default for longer hops when rideshare isn't budget-justified.
  scores: score=0.84 (base=0.94, fatigue=0.90); transit 2.7km; step_cost=$1.62

14:04 Tower of London
  text:   Strongest near-tie among remaining heritage candidates (versus Natural
          History Museum). Fits the remaining time window with margin. Reached by
          metro/bus (5.4 km), default for longer hops when rideshare isn't
          budget-justified.
  scores: score=0.70 (base=0.92, fatigue=0.76); transit 5.4km; step_cost=$71.26
```

## UI surface

`ui/static/index.html` already carries an `<div id="info-popover">` that
the existing `initInfoIcons()` JS uses for the form-side tooltips
(`Money cap`, `CO2`, etc.). The visit card reuses the same `.info-icon`
class + `data-tip` attribute pattern. The popover JS does event
delegation, so dynamically-rendered visit cards work without re-binding.
