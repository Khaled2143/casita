"""Rank listings by fit.

Heuristic baseline. Higher score = better.

Weights and gates come from the active LifestyleProfile (see profiles.py) —
which anchor categories matter and how much, whether dog policy gates, and
which neighborhoods get a fallback bonus. The default profile ("sf_dogs")
reproduces the original hardcoded priorities exactly:
  - Dogs OK (large or any-size) — gate; no-dogs heavily penalized
  - Walk-to-Presidio (trail access) — primary
  - Walk-to-beach — secondary
  - 3 bedrooms preferred, ≥ 1.5 baths preferred
  - In-unit laundry > shared > hookups
  - Garage parking > street > none

Walking times come from the `walk_map` populated by walk.populate_for().
When None, score is computed without those terms.
"""
from .models import Listing
from .profiles import LifestyleProfile, get_profile


def _hood_fallback_bonus(listing: Listing, profile: LifestyleProfile) -> int:
    """Small extra credit for the profile's preferred neighborhoods.

    `profile.hood_bonus` is ordered highest-bonus-and-most-specific first
    (e.g. "presidio heights" before bare "presidio") — first substring match
    wins, same precedence as the original if/elif chain.
    """
    hood = (listing.hood or "").lower()
    for substr, bonus in profile.hood_bonus.items():
        if substr in hood:
            return bonus
    return 0


def _walk_bonus(minutes: int | None, *, sweet_spot: int) -> int:
    """Sigmoid-ish: a 5-min walk should clearly beat 20-min, but 20 vs 25 is noise."""
    if minutes is None:
        return 0
    if minutes <= sweet_spot:
        return 15
    if minutes <= sweet_spot + 5:
        return 10
    if minutes <= sweet_spot + 10:
        return 5
    if minutes <= sweet_spot + 20:
        return 1
    return -3


def score(listing: Listing, walk_map: dict | None = None, *, profile: LifestyleProfile | str | None = None) -> int:
    profile = get_profile(profile)
    s = 0

    # Dog policy — gate. Only applies for profiles that care about it.
    if profile.dog_gate:
        if listing.dog_policy == "no_dogs" or listing.pets_allowed is False:
            return -1000
        if listing.dog_policy == "small_only":
            s -= 30  # not a hard gate, but large dogs need negotiation.
        if listing.dog_policy == "large_ok":
            s += 12
        elif listing.dog_policy == "dogs_ok":
            s += 6

    # Walk times — weighted per the profile's anchor priority order.
    if walk_map is not None:
        from .walk import nearest
        for group in profile.anchor_groups:
            if group.weight == 0:
                continue
            best = nearest(walk_map, listing.key, group.anchors)
            if best:
                s += _walk_bonus(best[1], sweet_spot=group.sweet_spot) * group.weight

    s += _hood_fallback_bonus(listing, profile)

    # Size / config.
    if listing.beds and listing.beds >= 3:
        s += 4
    if listing.baths and listing.baths >= 1.5:
        s += 5

    # Laundry.
    if listing.laundry == "in-unit":
        s += 3
    elif listing.laundry == "shared (in building)":
        s += 1
    elif listing.laundry in ("hookups only", "none"):
        s -= 2

    # Parking.
    if listing.parking and "no parking" not in (listing.parking or "").lower() and listing.parking != "none":
        s += 2
    if listing.parking and ("garage" in listing.parking.lower()):
        s += 2

    return s


ELIMINATED_STATUSES = frozenset({"declined_by_landlord", "declined_by_us", "passed_on"})

# Active CRM pipeline — the listings we're actually pursuing. Higher strength =
# further along; orders within the pipeline bucket after vote weight.
PIPELINE_STRENGTH = {
    "applied": 5,
    "viewing_done": 4,
    "viewing_scheduled": 3,
    "shortlist": 2,
    "contacted": 1,
}


def rank(
    listings: list[Listing],
    walk_map: dict | None = None,
    status_map: dict[str, str] | None = None,
    vote_scores: dict[str, int] | None = None,
    *,
    profile: LifestyleProfile | str | None = None,
) -> list[Listing]:
    """Sort order — six buckets:
     -2. Active pipeline — a live CRM status (contacted → viewing → applied):
         the real to-do list, above everything. Within: more up-voters first,
         then further-along status, then llm_rank.
     -1. Favorites — net-upvoted (and not in pipeline/eliminated). An explicit
         human "yes" beats the ranker. Within: more up-voters first, then rank.
      0. Ranked + not filtered (severity ok / concerns), by llm_rank ascending
      1. New listings without an llm_rank yet (don't punish for being unranked)
      2. Filtered listings (severity=filtered)
      3. Eliminated — landlord-declined / we-passed / out-of-area, at the bottom

    Eliminated is soft-delete: we keep them visible at the end so we don't lose
    track of past leads. An eliminated listing stays down even if it was once
    up-voted or in the pipeline — the explicit pass is the newer, stronger
    signal. Within each bucket, ties break on heuristic score.
    """
    profile = get_profile(profile)
    status_map = status_map or {}
    vote_scores = vote_scores or {}
    def sort_key(L: Listing) -> tuple:
        net = vote_scores.get(L.key, 0)
        status = status_map.get(L.key)
        strength = PIPELINE_STRENGTH.get(status, 0)
        # llm_severity/llm_rank were computed for whichever profile last ran
        # `casita enrich` — only trust them for bucketing when the active
        # profile matches (profile.trust_llm_fields). Otherwise treat every
        # listing as "not yet ranked" (bucket 1), same as a fresh listing.
        if status in ELIMINATED_STATUSES:
            bucket = 3
        elif strength:
            bucket = -2
        elif net > 0:
            bucket = -1
        elif profile.trust_llm_fields and (L.llm_severity == "filtered" or (L.llm_rank or 0) >= 9000):
            bucket = 2
        elif not profile.trust_llm_fields or L.llm_rank is None:
            bucket = 1
        else:
            bucket = 0
        llm_rank = L.llm_rank if profile.trust_llm_fields else None
        return (bucket, -net, -strength, llm_rank or 0, -score(L, walk_map, profile=profile))
    return sorted(listings, key=sort_key)
