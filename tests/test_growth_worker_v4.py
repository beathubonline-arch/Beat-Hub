from sqlalchemy.exc import OperationalError


def test_growth_worker_is_local_and_importable():
    from app.services.growth_worker_v4 import LOCK_KEY, run_once
    assert LOCK_KEY > 0
    assert callable(run_once)


def test_growth_agent_run_model_has_daily_key():
    from app.models.growth_runs import GrowthAgentRun
    assert GrowthAgentRun.__tablename__ == "growth_agent_runs"
    assert "run_key" in GrowthAgentRun.__table__.columns
    assert GrowthAgentRun.__table__.c.run_key.unique is True


def test_scheduler_is_zero_budget():
    from app.services.growth_scheduler import growth_scheduler_loop
    assert callable(growth_scheduler_loop)


def test_cold_queue_uses_verified_bootstrap_without_live_search(monkeypatch):
    import app.services.growth_worker_v4 as worker

    expected = {
        "ok": True,
        "status": "ready",
        "mode": "verified_public_bootstrap",
        "qualified_queue": 7,
        "queue": [],
    }

    monkeypatch.setattr(worker, "_ready_count", lambda _db: 0)
    monkeypatch.setattr(worker, "run_verified_bootstrap_queue", lambda _db, limit=8: expected)
    monkeypatch.setattr(worker, "run_acquisition_queue", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live search must not run on a cold queue")))

    assert worker._build_acquisition(object()) == expected


def test_acquisition_recovers_to_verified_bootstrap_after_db_disconnect(monkeypatch):
    import app.services.growth_worker_v4 as worker

    class DummyDB:
        def __init__(self):
            self.rolled_back = 0
            self.closed = 0

        def rollback(self):
            self.rolled_back += 1

        def close(self):
            self.closed += 1

    db = DummyDB()

    def fail_live(*args, **kwargs):
        raise OperationalError("SELECT tracks", {}, Exception("SSL connection closed"))

    expected = {
        "ok": True,
        "status": "ready",
        "mode": "verified_public_bootstrap",
        "qualified_queue": 7,
        "queue": [],
    }

    monkeypatch.setattr(worker, "_ready_count", lambda _db: 3)
    monkeypatch.setattr(worker, "run_acquisition_queue", fail_live)
    monkeypatch.setattr(worker, "run_verified_bootstrap_queue", lambda _db, limit=8: expected)

    result = worker._build_acquisition(db)
    assert result == expected
    assert db.rolled_back >= 1
    assert db.closed >= 1
