import pytest

from casita import profiles
from casita.models import Listing
from casita.rank import rank as do_rank
from casita.rank import score as do_score


def _listing(**kw) -> Listing:
    base = dict(source="manual", source_id="1", url="")
    base.update(kw)
    return Listing(**base)


def test_get_profile_none_resolves_default():
    assert profiles.get_profile(None) is profiles.PROFILES[profiles.DEFAULT_PROFILE_KEY]


def test_get_profile_by_key():
    assert profiles.get_profile("sf_dogs") is profiles.PROFILES["sf_dogs"]


def test_get_profile_passthrough():
    p = profiles.PROFILES["sf_dogs"]
    assert profiles.get_profile(p) is p


def test_get_profile_env_var(monkeypatch):
    monkeypatch.setenv("CASITA_PROFILE", "sf_dogs")
    assert profiles.get_profile(None) is profiles.PROFILES["sf_dogs"]


def test_get_profile_unknown_key_raises_with_valid_list():
    with pytest.raises(ValueError, match="sf_dogs"):
        profiles.get_profile("does-not-exist")


def test_default_profile_dog_gate_and_llm_trust_are_on():
    p = profiles.get_profile(None)
    assert p.dog_gate is True
    assert p.trust_llm_fields is True


def test_score_default_profile_gates_no_dogs():
    L = _listing(dog_policy="no_dogs")
    assert do_score(L) == -1000


def test_score_default_profile_rewards_large_dogs_over_unknown():
    large_ok = _listing(source_id="a", dog_policy="large_ok")
    unknown = _listing(source_id="b", dog_policy=None)
    assert do_score(large_ok) > do_score(unknown)


def test_score_default_profile_applies_hood_bonus_precedence():
    # "presidio heights" must outrank bare "presidio" — precedence pin for
    # the dict-based lookup that replaced the old if/elif chain.
    heights = _listing(source_id="a", neighborhood_resolved="Presidio Heights")
    bare = _listing(source_id="b", neighborhood_resolved="Presidio")
    outside = _listing(source_id="c", neighborhood_resolved="Bayview")
    assert do_score(heights) > do_score(bare) > do_score(outside)


def test_rank_default_profile_sorts_filtered_severity_last():
    ok = _listing(source_id="ok", llm_rank=1, llm_severity="ok")
    filtered = _listing(source_id="bad", llm_rank=2, llm_severity="filtered")
    ranked = do_rank([filtered, ok])
    assert [L.source_id for L in ranked] == ["ok", "bad"]


def test_all_anchors_matches_trail_beach_bakery_counts():
    from casita.walk import BAKERIES, BEACHES, PRESIDIO_GATES

    p = profiles.get_profile(None)
    assert len(p.all_anchors()) == len(PRESIDIO_GATES) + len(BEACHES) + len(BAKERIES)


# ---------- sf_gym_halal (alternate profile) ----------


def test_sf_gym_halal_does_not_trust_llm_fields_or_gate_dogs():
    p = profiles.get_profile("sf_gym_halal")
    assert p.dog_gate is False
    assert p.trust_llm_fields is False
    assert p.hood_bonus == {}


def test_score_sf_gym_halal_does_not_gate_no_dogs():
    L = _listing(dog_policy="no_dogs")
    assert do_score(L, profile="sf_gym_halal") != -1000


def test_score_sf_gym_halal_rewards_proximity_to_gym(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.setenv("CASITA_ROUTES_OFFLINE", "1")
    monkeypatch.setenv("CASITA_ROUTE_CACHE_DB", str(tmp_path / "routes.sqlite"))
    from casita import walk

    p = profiles.get_profile("sf_gym_halal")
    gym = p.anchor_groups[0].anchors[0]
    near = _listing(source_id="near", lat=gym.lat, lng=gym.lng)
    far = _listing(source_id="far", lat=37.70, lng=-122.10)  # far corner of the Bay Area

    walk_map = walk.populate_for([near, far], p.all_anchors())
    assert do_score(near, walk_map, profile=p) > do_score(far, walk_map, profile=p)


def test_rank_sf_gym_halal_ignores_stale_filtered_severity():
    # This severity was computed under sf_dogs (e.g. a no-dogs hard gate) —
    # sf_gym_halal doesn't trust it and shouldn't sort on it.
    stale_filtered = _listing(source_id="a", llm_rank=1, llm_severity="filtered", dog_policy="no_dogs")
    stale_ok = _listing(source_id="b", llm_rank=2, llm_severity="ok")
    ranked = do_rank([stale_filtered, stale_ok], profile="sf_gym_halal")
    # Neither bucket-2 (filtered) nor llm_rank should decide order — both
    # listings land in the same "unranked" bucket, tie-broken by score().
    keys = {L.source_id for L in ranked}
    assert keys == {"a", "b"}
