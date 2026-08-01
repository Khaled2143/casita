import sqlite3

from casita import walk
from casita.models import Listing


def test_routes_api_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("CASITA_ROUTES_OFFLINE", raising=False)

    assert walk._routes_api_enabled() is False


def test_routes_api_disabled_when_offline(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "fake-key")
    monkeypatch.setenv("CASITA_ROUTES_OFFLINE", "1")

    assert walk._routes_api_enabled() is False


def test_routes_api_enabled_with_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "fake-key")
    monkeypatch.delenv("CASITA_ROUTES_OFFLINE", raising=False)

    assert walk._routes_api_enabled() is True


def test_ensure_cache_migrates_mode_into_primary_key(tmp_path):
    db_path = tmp_path / "routes.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE walk_cache (
                from_lat REAL, from_lng REAL,
                to_lat REAL, to_lng REAL,
                mode TEXT NOT NULL DEFAULT 'walk',
                minutes INTEGER NOT NULL,
                source TEXT NOT NULL,
                ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (from_lat, from_lng, to_lat, to_lng)
            )"""
        )
        conn.execute(
            "INSERT INTO walk_cache "
            "(from_lat, from_lng, to_lat, to_lng, mode, minutes, source) "
            "VALUES (1, 2, 3, 4, 'walk', 10, 'api')"
        )
        walk._ensure_cache(conn)

        pk_cols = {row[1] for row in conn.execute("PRAGMA table_info(walk_cache)") if row[5]}
        assert "mode" in pk_cols

        conn.execute(
            "INSERT INTO walk_cache "
            "(from_lat, from_lng, to_lat, to_lng, mode, minutes, source) "
            "VALUES (1, 2, 3, 4, 'drive', 5, 'api')"
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM walk_cache WHERE from_lat=1 AND from_lng=2"
        ).fetchone()[0]
        assert count == 2


def test_populate_for_accepts_custom_anchors(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.setenv("CASITA_ROUTES_OFFLINE", "1")
    monkeypatch.setenv("CASITA_ROUTE_CACHE_DB", str(tmp_path / "routes.sqlite"))

    L = Listing(source="manual", source_id="1", url="", lat=37.78, lng=-122.42)
    custom = [walk.Anchor("Test Gym", "Gym", 37.79, -122.41)]

    result = walk.populate_for([L], custom)

    assert (L.key, "Test Gym") in result
    assert isinstance(result[(L.key, "Test Gym")], int)


def test_populate_drive_for_marin_accepts_custom_anchors(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.setenv("CASITA_ROUTES_OFFLINE", "1")
    monkeypatch.setenv("CASITA_ROUTE_CACHE_DB", str(tmp_path / "routes.sqlite"))

    # lat > 37.84 puts this listing on the Marin side (walk.is_marin).
    L = Listing(source="manual", source_id="1", url="", lat=37.90, lng=-122.54)
    custom = [walk.Anchor("Test Halal Market", "Halal Market", 37.79, -122.41)]

    result = walk.populate_drive_for_marin([L], custom)

    assert (L.key, "Test Halal Market") in result
    assert isinstance(result[(L.key, "Test Halal Market")], int)
