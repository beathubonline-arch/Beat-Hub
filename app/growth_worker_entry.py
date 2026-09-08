from app.services.growth_worker_v2 import run_once

if __name__ == "__main__":
    result = run_once(False)
    print(f"[BeatHub Growth Agent] status={result.get('status')}")
    if not result.get("ok"):
        raise SystemExit(1)
