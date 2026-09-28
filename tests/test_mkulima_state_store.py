import json
import state_store

def test_actor_ref_is_stable_and_not_phone(monkeypatch):
    monkeypatch.setenv("MKULIMA_ACTOR_SALT","test-only-salt")
    phone="254700123456"
    a=state_store.actor_ref(phone)
    assert a==state_store.actor_ref(phone)
    assert phone not in a
    assert len(a)==64

def test_save_prefers_supabase_without_raw_phone(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL","https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY","secret-test-key")
    seen={}
    def fake_api(method,path,body=None,prefer=None):
        seen.update(method=method,path=path,body=body,prefer=prefer)
    monkeypatch.setattr(state_store,"_api",fake_api)
    backend=state_store.save_state("254700123456",{"crop":"maize"})
    assert backend=="supabase"
    assert seen["body"]["state"]["crop"]=="maize"
    assert seen["body"]["actor_ref"]!="254700123456"
    assert "254700123456" not in json.dumps(seen)

def test_save_falls_back_to_sqlite(monkeypatch,tmp_path):
    monkeypatch.delenv("SUPABASE_URL",raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY",raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY",raising=False)
    monkeypatch.setattr(state_store,"DB",str(tmp_path/"state.db"))
    assert state_store.save_state("254700000001",{"location":"Turbo"})=="sqlite"
    assert state_store.load_state("254700000001")["location"]=="Turbo"

def test_load_prefers_supabase(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL","https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY","secret-test-key")
    monkeypatch.setattr(state_store,"_api",lambda *a,**k:[{"state":{"crop":"tomato","location":"Turbo"}}])
    state=state_store.load_state("254700123456")
    assert state=={"crop":"tomato","location":"Turbo"}
