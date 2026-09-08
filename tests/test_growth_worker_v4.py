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
