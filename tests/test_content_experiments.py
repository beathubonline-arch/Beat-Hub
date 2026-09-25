from types import SimpleNamespace

from app.routers.content_experiments import create_content_experiment
from app.services.content_experiments import build_variants, strongest_variant
from main import app


def test_three_hooks_by_two_visuals_creates_six_unique_variants():
    variants = build_variants(SimpleNamespace(title="Eldoret Nights", genre="Afrobeats"))
    assert len(variants) == 6
    assert len({item["hook_number"] for item in variants}) == 3
    assert len({item["visual_treatment"] for item in variants}) == 2
    assert len({(item["hook_number"], item["visual_treatment"]) for item in variants}) == 6


def test_winner_uses_commercial_outcomes_and_never_invents_missing_results():
    blank = SimpleNamespace(impressions=0, avg_watch_time_seconds=0, saves=0, sends=0, profile_visits=0, registrations=0, purchases=0)
    assert strongest_variant([blank]) is None
    viral = SimpleNamespace(impressions=10000, avg_watch_time_seconds=12, saves=500, sends=200, profile_visits=80, registrations=4, purchases=0)
    seller = SimpleNamespace(impressions=900, avg_watch_time_seconds=5, saves=10, sends=4, profile_visits=20, registrations=2, purchases=1)
    assert strongest_variant([viral, seller]) is seller


def test_creator_experiment_routes_are_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/dashboard/content-experiments" in paths
    assert "/dashboard/content-experiments/create" in paths
    assert "/dashboard/content-experiments/{variant_id}/metrics" in paths


def test_create_route_persists_exactly_six_creator_owned_variants():
    class Query:
        def filter(self, *args): return self
        def first(self): return track
    class DB:
        def __init__(self): self.added = []; self.commits = 0
        def query(self, model): return Query()
        def add(self, item): self.added.append(item)
        def commit(self): self.commits += 1
    track = SimpleNamespace(id="track-1", title="Eldoret Nights", genre="Afrobeats")
    user = SimpleNamespace(profile=SimpleNamespace(id="creator-1"))
    db = DB()
    response = create_content_experiment(track_id="track-1", channel="instagram", db=db, user=user)
    assert response.status_code == 303
    assert db.commits == 1
    assert len(db.added) == 6
    assert {item.creator_profile_id for item in db.added} == {"creator-1"}
    assert {item.track_id for item in db.added} == {"track-1"}
    assert {item.channel for item in db.added} == {"instagram"}


def test_migration_enables_rls_and_indexes_owner_fields():
    source = open("alembic/versions/acb258ba7531_content_experiments.py", encoding="utf-8").read()
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "creator_profile_id" in source
    assert "track_id" in source
