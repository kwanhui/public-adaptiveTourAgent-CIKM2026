"""Trigger registry: the single most important defence against trigger thrashing.

Rules:
  - A trigger only fires if its threshold is crossed.
  - Per-trigger debounce: the threshold must hold for `debounce_s` continuous seconds.
  - Per-trigger cooldown: after firing, the same trigger cannot fire again for
    `cooldown_s` seconds.

The registry is stateful; the loop driver keeps one instance per session.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from adaptivetouragent.fusion.snapshot import (
    ContextSnapshot,
    CrowdReading,
    WeatherReading,
)
from adaptivetouragent.signals.triggers.types import Severity, TriggerEvent, TriggerKind


@dataclass
class TriggerRule:
    kind: TriggerKind
    severity: Severity
    debounce_s: float
    cooldown_s: float
    # Internal state:
    crossed_since: datetime | None = field(default=None, init=False)
    last_fired_at: datetime | None = field(default=None, init=False)


# Defaults tuned for the demo. The numbers are intentionally generous to
# avoid thrashing during a demo run.
DEFAULT_RULES: dict[TriggerKind, TriggerRule] = {
    "weather_rain_onset": TriggerRule("weather_rain_onset", "warn", 60.0, 600.0),
    "weather_storm": TriggerRule("weather_storm", "critical", 30.0, 300.0),
    "crowd_spike": TriggerRule("crowd_spike", "warn", 120.0, 600.0),
    "poi_closed": TriggerRule("poi_closed", "critical", 0.0, 600.0),
    "transit_disruption": TriggerRule("transit_disruption", "warn", 60.0, 600.0),
    "fatigue_high": TriggerRule("fatigue_high", "info", 0.0, 1800.0),
    "user_request": TriggerRule("user_request", "info", 0.0, 0.0),
}


class TriggerRegistry:
    """Apply rules + debounce + cooldown to incoming snapshots."""

    def __init__(self, rules: dict[TriggerKind, TriggerRule] | None = None):
        if rules is None:
            self.rules = {k: TriggerRule(**v.__dict__) for k, v in DEFAULT_RULES.items()}
        else:
            self.rules = rules

    def evaluate(
        self,
        snapshot: ContextSnapshot,
        upcoming_poi_ids: Iterable[str] = (),
    ) -> list[TriggerEvent]:
        """Return the triggers that fire for this snapshot. Mutates internal debounce/cooldown state."""
        candidates = self._candidates(snapshot, list(upcoming_poi_ids))
        fired: list[TriggerEvent] = []
        active_kinds = {k for k, _, _ in candidates}

        for kind, affects, details in candidates:
            rule = self.rules[kind]
            if rule.crossed_since is None:
                rule.crossed_since = snapshot.at

            held_for = (snapshot.at - rule.crossed_since).total_seconds()
            if held_for < rule.debounce_s:
                continue

            if rule.last_fired_at is not None:
                cool = (snapshot.at - rule.last_fired_at).total_seconds()
                if cool < rule.cooldown_s:
                    continue

            rule.last_fired_at = snapshot.at
            fired.append(
                TriggerEvent(
                    kind=kind,
                    severity=rule.severity,
                    at=snapshot.at,
                    affects=affects,
                    details=details,
                    snapshot_id=snapshot.snapshot_id,
                )
            )

        # Reset debounce timers for kinds whose condition has lapsed.
        for kind, rule in self.rules.items():
            if kind not in active_kinds:
                rule.crossed_since = None

        return fired

    def _candidates(
        self,
        snapshot: ContextSnapshot,
        upcoming_poi_ids: list[str],
    ) -> list[tuple[TriggerKind, list[str], dict[str, str]]]:
        out: list[tuple[TriggerKind, list[str], dict[str, str]]] = []

        weather = snapshot.weather
        if weather is not None:
            if weather.condition == "storm":
                out.append(("weather_storm", [], {"condition": "storm"}))
            elif _is_rain_onset(weather):
                out.append(
                    (
                        "weather_rain_onset",
                        [],
                        {"precip_mm_per_h": f"{weather.precip_mm_per_h:.2f}"},
                    )
                )

        for pid in upcoming_poi_ids:
            cr = snapshot.crowd.get(pid)
            if cr is None:
                continue
            if cr.crowd_level == "closed":
                out.append(("poi_closed", [pid], {}))
            elif _is_crowd_spike(cr):
                out.append(
                    (
                        "crowd_spike",
                        [pid],
                        {
                            "crowd_level": cr.crowd_level,
                            "queue_minutes": str(cr.queue_minutes or 0.0),
                        },
                    )
                )

        for (a, b), tr in snapshot.transit.items():
            if not tr.disruption:
                continue
            out.append(
                (
                    "transit_disruption",
                    [f"edge:{a}->{b}"],
                    {"duration_min": f"{tr.duration_min:.1f}"},
                )
            )

        if snapshot.user.fatigue_0_1 >= 0.85:
            out.append(("fatigue_high", [], {"fatigue": f"{snapshot.user.fatigue_0_1:.2f}"}))

        if snapshot.user.explicit_pref_changes:
            out.append(
                (
                    "user_request",
                    [],
                    {"changes": ",".join(snapshot.user.explicit_pref_changes)},
                )
            )

        return out

    def force_cooldown_reset(self, kind: TriggerKind) -> None:
        """Used by tests to reset state between cases."""
        self.rules[kind].crossed_since = None
        self.rules[kind].last_fired_at = None


def _is_rain_onset(w: WeatherReading) -> bool:
    return w.condition == "rain" and w.precip_mm_per_h > 1.0


def _is_crowd_spike(c: CrowdReading) -> bool:
    if c.crowd_level == "high":
        return True
    return c.queue_minutes is not None and c.queue_minutes >= 30.0


# Helper to inflate cooldown windows in tests that need to fire repeatedly.
def short_window_rules() -> dict[TriggerKind, TriggerRule]:
    """Variant with negligible debounce/cooldown, convenient for unit tests."""
    return {
        "weather_rain_onset": TriggerRule("weather_rain_onset", "warn", 0.0, 0.0),
        "weather_storm": TriggerRule("weather_storm", "critical", 0.0, 0.0),
        "crowd_spike": TriggerRule("crowd_spike", "warn", 0.0, 0.0),
        "poi_closed": TriggerRule("poi_closed", "critical", 0.0, 0.0),
        "transit_disruption": TriggerRule("transit_disruption", "warn", 0.0, 0.0),
        "fatigue_high": TriggerRule("fatigue_high", "info", 0.0, 0.0),
        "user_request": TriggerRule("user_request", "info", 0.0, 0.0),
    }


def overridden_rules(**overrides: tuple[float, float]) -> dict[TriggerKind, TriggerRule]:
    """Helper to build a rule dict with custom (debounce_s, cooldown_s) overrides."""
    base = {k: TriggerRule(**v.__dict__) for k, v in DEFAULT_RULES.items()}
    for kind_str, (debounce, cooldown) in overrides.items():
        kind = cast(TriggerKind, kind_str)
        if kind in base:
            base[kind].debounce_s = debounce
            base[kind].cooldown_s = cooldown
    return base
