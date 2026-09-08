from app.services.growth_worker_v3 import run_once

def test_worker_callable():
    assert callable(run_once)
