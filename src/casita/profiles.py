"""Swappable ranking priorities.

A `LifestyleProfile` bundles what a household cares about: which anchor
categories matter and how much (`anchor_groups`), whether to hard-gate on
dog policy, whether the DB's cached `llm_rank` / `llm_reason` /
`llm_severity` were computed under this profile's priorities (and are
therefore safe to trust for display/sorting), and the Gemini ranking
prompt to use for live `casita enrich` runs.

`sf_dogs` is the original household's profile — migrated verbatim from what
used to be hardcoded across `rank.py`, `llm.py`, and `walk.py`. It's the
default; nothing about its behavior changes by existing here instead.

`sf_gym_halal` is a second, unrelated household re-ranking the exact same
listing set: no dogs, no yard preference, no SF-neighborhood bonus —
instead a gym, a halal market, and a downtown commute. It proves the
ranking layer generalizes without touching scraping, the Listing schema,
or the demo fixture. `trust_llm_fields=False` because the fixture's
llm_rank/llm_reason/llm_severity were written for sf_dogs's priorities —
see rank.py's sort_key and html.py/listing_page.py's rendering, which both
fall back to deterministic-only ranking when a profile doesn't trust them.
"""
from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass

from .walk import (
    Anchor, BAKERIES, BEACHES, GYMS, HALAL_MARKETS, PRESIDIO_GATES, WORK_COMMUTE,
)


@dataclass(frozen=True)
class AnchorGroup:
    """A named category of points of interest a profile cares about.

    `weight` multiplies the group's walk-time bonus in rank.py's heuristic
    score — 0 means "shown in the prompt/UI but not scored" (bakery, today).
    """
    key: str
    label: str
    anchors: list[Anchor]
    weight: int = 1
    sweet_spot: int = 10  # minutes; see rank._walk_bonus


@dataclass(frozen=True)
class LifestyleProfile:
    key: str
    label: str
    anchor_groups: list[AnchorGroup]  # priority order, first = primary
    hood_bonus: dict[str, int]  # lowercase hood substring -> bonus points
    dog_gate: bool  # apply the dog-policy hard gate + score weight?
    trust_llm_fields: bool  # does the DB's llm_rank/reason/severity match this profile?
    rank_prompt: str  # Gemini system prompt for live `casita enrich` runs

    def all_anchors(self) -> list[Anchor]:
        out: list[Anchor] = []
        for group in self.anchor_groups:
            out.extend(group.anchors)
        return out


_SF_DOGS_RANK_PROMPT = textwrap.dedent("""
    You're ranking rental listings for a household looking in San Francisco
    (Richmond / Sunset / Presidio-adjacent) or Marin (Mill Valley / Sausalito).
    They have two large dogs, prefer 2-3 bedrooms, and value access to trails,
    beaches, bakeries, and practical daily transportation.

    ── PRECEDENCE — how to resolve conflicting signals ──
      • This prompt is settled policy, but the household's actual votes
        (provided as PREFERENCE EXAMPLES before the listings, when present) are
        the ground truth. Where an example conflicts with a SOFT policy line
        here, follow the examples — they reflect the most current preference.
      • HARD REQUIREMENTS below always win, even over examples (dog policy, etc.).
      • When reviewer signals conflict, prefer the reviewer_a examples over
        reviewer_b examples. reviewer_a is the primary preference signal.

    ── HARD REQUIREMENTS — drop the listing from results if any fail ──
      • Dog policy — the household has two large dogs. Hard rules:
          - **no_dogs** → severity="filtered", drop to the bottom, reason
            starts with "No dogs allowed".
          - **small_only** → severity="concerns" ALWAYS, never "ok". Reason
            MUST start with "Small dogs only — would need to negotiate"
            because the badge says SMALL DOGS ONLY and the rank reason has
            to match the badge. Never describe these as "dog-friendly".
            They should not outrank a comparable dogs_ok / large_ok listing.
          - **dogs_ok / large_ok** → eligible for severity="ok".
          - **null/unknown** → severity="concerns", flag the verification need.
      • For Marin listings: a private yard is strongly preferred
        (fenced backyard, side yard, or private patio with grass). NEVER
        hard-filter a Marin listing for yard alone — treat missing/
        unknown yard data as severity="concerns" and flag for verification.
        Only mark a Marin yard-less listing severity="filtered" if
        ALL of these are true: (a) yard is explicitly false in the data,
        AND (b) has_yard is the listing's primary problem (other factors
        like pet policy aren't already disqualifying).
      • Listings missing critical data like price or beds (showing as ? or 0)
        should still be included if the title suggests it's a real listing —
        flag the missing data in the reason.

    ── STRONG REQUIREMENTS — heavy penalty if missing, not a hard gate ──
      • Location must be in-scope: SF Inner/Outer Richmond, Inner/Outer Sunset,
        Lake Street, Presidio Heights, Central Richmond/Sunset; OR Marin —
        Mill Valley (incl. Tam Valley, Homestead Valley, Almonte) or Sausalito.
      • Size: **≥120 m² (≈1,292 sqft) is the comfortable floor** for two adults
        and two large dogs. Treat smaller sizes as significant penalty:
          - 100–119 m² (1,076–1,292 sqft): tight, flag in reason
          - <100 m² (<1,076 sqft): too small, downgrade hard or filter
        If sqft is missing entirely, don't gate — flag as needs verification.
      • In-unit laundry strongly preferred. "Shared in building" is acceptable;
        hookups-only or none is a significant penalty.
      • Parking on-site (garage, attached, off-street). Street-only is
        workable in SF, but is a soft penalty.
      • Trail OR beach access — REVEALED AS NEAR-MANDATORY. ~80% of the passes
        so far cite "not walkable to a trail or beach," so treat this as a
        strong requirement, not a tie-breaker: a listing that isn't within an
        easy walk (SF) / short drive (Marin) of EITHER a trail or a beach gets
        a heavy penalty → severity="concerns", ranked low. NOT a hard gate —
        never "filtered"-drop on trail/beach distance alone. (Which specific
        anchor — Baker, the Presidio gates, the Dipsea — still breaks ties; see
        PREFERENCES.)
      • Aesthetics is a SOFT tie-breaker, NOT a heavy penalty. The household
        cares about design, but the votes are clear: location beats finishes. "It's
        ugly but we can take a look" was an UP vote. Treat dated / low-end
        finishes as a "concerns" flag at most — never rank a well-located place
        low for looks alone, and never "filtered" on aesthetics.

    ── DISTANCE MODES ──
    The brief's "walks" field is prefixed with WALKING (SF) or DRIVING (Marin).
    For SF listings, all times are WALKING — apply the bakery preference, etc.
    For Marin/Mill Valley listings, all times are DRIVING — these are different
    units. Don't penalize a Mill Valley listing for being "far" from SF anchors
    when a 20-minute drive is normal there.

    ── PREFERENCES — in priority order, used to break ties and shape ranking ──
      (Trail/beach ACCESS is now a strong requirement above; #2/#3 here govern
       how close and which anchor — they break ties among listings that qualify.)
      1. Close to SF. For SF listings this means walking distance to Muni /
         downtown. For Marin listings this means proximity to ferry service or
         the Golden Gate Bridge.
      2. Close to trail access. SF = Presidio gates (Arguello, Lyon, West
         Pacific). Marin = Dipsea / Tennessee Valley / Headlands access.
      3. Close to a beach. **Baker Beach is the preferred beach** — proximity
         to Baker carries more weight than proximity to China or Ocean (and in
         Marin, Muir / Stinson). When evaluating, look at the named anchor in
         the brief; if it's Baker, that's a stronger positive.
      4. Close to a bakery or cafe-with-pastries.
         For SF listings: the bar is 4.7★ + 1,500+ reviews. Qualifying set:
         Arsicault, Cinderella, b. patisserie, Arizmendi — all in SF.
         **Arsicault is the preferred favorite** — proximity to Arsicault
         carries more weight than the others.
         For Marin listings: the bar is lower (4.5★+ / 100+ reviews) because
         the market is smaller. Qualifying set: Bob's Donuts, Madrona,
         Equator Coffees Mill Valley, Emporio Rulli Larkspur. Don't
         over-penalize Marin listings on this dimension — driving is expected,
         and the local options are real (just lower-volume).

    ── ENGAGEMENT BOOST — listings where we're already in conversation ──
      If the listing's status is one of: contacted, viewing_scheduled,
      viewing_done, shortlist, applied — rank it higher than fresh listings
      with similar facts. Conversations have momentum; protect that.
      Exception: if status is declined_by_us or declined_by_landlord, the
      listing is dead — leave it out.

    ── SOFT BONUSES ──
      • 3 bed > 2 bed
      • Private yard (huge bonus in SF, very strong preference in Marin)
      • In-unit laundry
      • Garage parking
      • Inner Richmond / Lake Street / Presidio Heights / Inner Sunset.
      • Downtown Mill Valley walkability.

    ── OUTPUT FORMAT ──
    Return EVERY listing in the input — none dropped silently. Order best
    first. Each entry has:
      • key: the listing key
      • reason: one short sentence with the load-bearing facts
      • severity:
          - "ok"       → A strong fit given SF rental realities. Calibrate
                         to the market, not to an ideal:
                           * Street parking is NORMAL in SF — not a concern.
                           * Shared laundry-in-building is fine — not a concern.
                           * No private yard in SF is the default — not a concern.
                           * 1 bath in a 2-bed, or 1.5 bath in a 3-bed, is
                             normal — not a concern.
                         A 3BR/1.5BA Inner Richmond remodel with W/D and
                         street parking IS as good as SF gets — that's "ok".
          - "concerns" → Actual red flags worth pausing on:
                           * Missing critical data (no price, no bed count)
                           * Small-dogs-only or weight-cap (needs negotiation)
                           * Visibly dated / cheap finishes / "needs work"
                           * Out-of-scope neighborhood
                           * Marin without yard data verified
                           * Hookups-only or NO laundry at all
          - "filtered" → Hard gate fail (no-dogs explicit, multi-unit
                         building landing pages with no usable listing
                         data) — sort to the bottom.
""").strip()


_SF_GYM_HALAL_RANK_PROMPT = textwrap.dedent("""
    You're ranking rental listings for a single professional working in San
    Francisco's Financial District. They have no pets and no yard
    requirement. What matters: an easy commute downtown, a gym they can
    walk to, and a halal market nearby for groceries.

    ── HARD REQUIREMENTS — drop the listing from results if any fail ──
      • None. Only drop a listing (severity="filtered") if it's an unusable
        stub with no price, no bed count, and no address — a multi-unit
        building landing page with nothing real to evaluate.

    ── STRONG REQUIREMENTS — heavy penalty if missing, not a hard gate ──
      • Commute: an easy walk or short transit ride to the Financial
        District. A long commute is a significant penalty, not a filter.
      • In-unit or shared-in-building laundry preferred over hookups-only
        or none.

    ── PREFERENCES — in priority order, used to break ties and shape ranking ──
      1. Close to a gym — walkable beats needing to drive.
      2. Close to a halal market — grocery runs without a special trip.
      3. Close to downtown / the commute anchor.
      4. Lower price, all else equal.

    ── OUTPUT FORMAT ──
    Return EVERY listing in the input — none dropped silently. Order best
    first. Each entry has:
      • key: the listing key
      • reason: one short sentence with the load-bearing facts
      • severity:
          - "ok"       → Reasonable commute, plausible gym or halal market
                         access.
          - "concerns" → Long commute, no nearby gym or halal market, or
                         missing data worth flagging.
          - "filtered" → Unusable stub listing only (see HARD REQUIREMENTS).
""").strip()


PROFILES: dict[str, LifestyleProfile] = {
    "sf_dogs": LifestyleProfile(
        key="sf_dogs",
        label="SF + Marin — two large dogs, trails, beaches, bakeries",
        anchor_groups=[
            AnchorGroup("trail", "trail", PRESIDIO_GATES, weight=2, sweet_spot=10),
            AnchorGroup("beach", "beach", BEACHES, weight=1, sweet_spot=10),
            AnchorGroup("bakery", "bakery", BAKERIES, weight=0, sweet_spot=10),
        ],
        # Precedence matters: more specific / higher-bonus matches must come
        # before broader ones (e.g. "presidio heights" before bare "presidio")
        # since the lookup returns on first substring match. Mirrors the old
        # rank._hood_fallback_bonus if/elif chain exactly.
        hood_bonus={
            "inner richmond": 6,
            "lake street": 6,
            "presidio heights": 6,
            "inner sunset": 5,
            "presidio": 4,
            "central richmond": 2,
            "central sunset": 2,
            "outer richmond": 2,
            "outer sunset": 1,
            "parkside": 1,
        },
        dog_gate=True,
        trust_llm_fields=True,
        rank_prompt=_SF_DOGS_RANK_PROMPT,
    ),
    "sf_gym_halal": LifestyleProfile(
        key="sf_gym_halal",
        label="SF + Marin — gym, halal market, downtown commute",
        anchor_groups=[
            AnchorGroup("gym", "gym", GYMS, weight=2, sweet_spot=15),
            AnchorGroup("halal_market", "halal market", HALAL_MARKETS, weight=1, sweet_spot=15),
            AnchorGroup("commute", "commute", WORK_COMMUTE, weight=1, sweet_spot=20),
        ],
        hood_bonus={},
        dog_gate=False,
        trust_llm_fields=False,
        rank_prompt=_SF_GYM_HALAL_RANK_PROMPT,
    ),
}

DEFAULT_PROFILE_KEY = "sf_dogs"


def get_profile(key: str | LifestyleProfile | None) -> LifestyleProfile:
    """Resolve a profile by key, `LifestyleProfile` passthrough, `CASITA_PROFILE`
    env var, or the default — in that order.
    """
    if isinstance(key, LifestyleProfile):
        return key
    resolved = key or os.environ.get("CASITA_PROFILE") or DEFAULT_PROFILE_KEY
    try:
        return PROFILES[resolved]
    except KeyError:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {resolved!r}. Valid profiles: {valid}") from None
