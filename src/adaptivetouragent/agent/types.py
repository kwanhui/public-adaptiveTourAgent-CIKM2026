"""Agent-internal data types (not part of the user-facing contract)."""

from dataclasses import dataclass, field


@dataclass
class AccessibilityRequirements:
    """Hard accessibility constraints layered on top of POI selection.

    Closes the gap noted in Mayr et al. 2025 (RecSoGood): mainstream POI
    recommenders systematically under-serve mobility-impaired users; LLM
    travel-planning demos surface accessibility only as free-text in the
    prompt with no verifier.
    """

    require_wheelchair: bool = False
    dietary: tuple[str, ...] = ()  # "vegetarian", "halal", "vegan", "gluten-free"
    require_low_stimulation: bool = False  # quiet, low-crowd-noise spaces


@dataclass
class GroupMember:
    """One member of a multi-user group, with their own preferences."""

    member_id: str
    name: str
    category_weights: dict[str, float]
    veto_categories: tuple[str, ...] = ()  # categories this member refuses
    boost_categories: tuple[str, ...] = ()  # categories this member loves


@dataclass
class UserProfile:
    """Tourist profile passed to the planner.

    The category_weights are normalised to a probability distribution over
    POI categories. `family_size` scales the fatigue model and per-stop
    spend (entry fees and transit fares are multiplied by `family_size`).
    """

    user_id: str
    name: str
    category_weights: dict[str, float]
    family_size: int = 1
    require_kid_friendly: bool = False
    notes: str = ""
    accessibility: AccessibilityRequirements = field(default_factory=AccessibilityRequirements)
    group_members: tuple[GroupMember, ...] = ()  # populated for true multi-user groups
    # Vetoes cast mid-trip (not at plan time): the UI's live "veto a category"
    # control appends here, and the replanner re-applies them on the next
    # revision so a veto re-routes the tail in place.
    live_veto_categories: tuple[str, ...] = ()
    # Individual stops the traveller removed by hand; excluded from retrieval
    # on every (re)plan so a manual "remove this stop" sticks across replans.
    excluded_pois: tuple[str, ...] = ()

    def normalised_weights(self) -> dict[str, float]:
        total = sum(self.category_weights.values())
        if total <= 0:
            return {}
        return {k: v / total for k, v in self.category_weights.items()}

    def is_group(self) -> bool:
        return len(self.group_members) > 0

    def vetoed_categories(self) -> set[str]:
        """Categories blocked for the whole group: any member's veto, plus any live veto."""
        out: set[str] = set(self.live_veto_categories)
        for m in self.group_members:
            out.update(m.veto_categories)
        return out

    def boosted_categories(self) -> set[str]:
        """Categories any member loves (amplified in the aggregated weights)."""
        out: set[str] = set()
        for m in self.group_members:
            out.update(m.boost_categories)
        return out

    def aggregated_weights(self) -> dict[str, float]:
        """Group-aware category weights used by retrieval and scoring.

        With no group members this is the single-user normalised weights, minus
        any live veto. With members, each member's weights are averaged with
        equal voice; a single veto (member-level or live) drops the category for
        the whole group, and a boost amplifies it by 25%. Re-evaluated on every
        replan so vetoes/boosts are live constraints, not plan-time-only.
        """
        vetoed = self.vetoed_categories()
        boosted = self.boosted_categories()

        if not self.group_members:
            base = {k: v for k, v in self.normalised_weights().items() if k not in vetoed}
            return {k: (v * 1.25 if k in boosted else v) for k, v in base.items()}

        all_categories: set[str] = set()
        for m in self.group_members:
            all_categories.update(m.category_weights.keys())

        aggregated: dict[str, float] = {}
        for cat in all_categories:
            if cat in vetoed:
                continue
            shares = []
            for m in self.group_members:
                total = sum(m.category_weights.values()) or 1.0
                shares.append(m.category_weights.get(cat, 0.0) / total)
            aggregated[cat] = sum(shares) / len(shares)
            if cat in boosted:
                aggregated[cat] *= 1.25
        return aggregated


@dataclass
class AgentRunStats:
    """Bookkeeping for one planner invocation."""

    llm_calls: int = 0
    rectifier_calls: int = 0
    cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)
