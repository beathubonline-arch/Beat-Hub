import ast
from pathlib import Path


def test_campaign_model_and_migrations_exist():
    model = Path("app/models/growth.py").read_text(encoding="utf-8")
    tree = ast.parse(model)
    names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "GrowthCampaignDay" in names
    migration = Path("alembic/versions/0024_growth_campaign.py").read_text(encoding="utf-8")
    assert 'revision = "growth_campaign_024"' in migration
    assert 'down_revision = "growth_funnel_023"' in migration
    assert "DROP TABLE" not in migration.upper()
    final_day = Path("alembic/versions/0025_growth_campaign_day30.py").read_text(encoding="utf-8")
    assert 'revision = "growth_campaign_day30_025"' in final_day
    assert 'down_revision = "growth_campaign_024"' in final_day
    assert '"day_number": 30' in final_day
    assert "DROP TABLE" not in final_day.upper()


def test_campaign_defines_29_generated_days_plus_persistent_day_30():
    source = Path("app/routers/growth_agent.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    blueprint = next(node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CAMPAIGN_BLUEPRINT" for t in node.targets))
    assert isinstance(blueprint.value, (ast.List, ast.Tuple))
    assert len(blueprint.value.elts) == 29
    final_day = Path("alembic/versions/0025_growth_campaign_day30.py").read_text(encoding="utf-8")
    assert '"day_number": 30' in final_day


def test_campaign_is_idempotently_seeded_and_status_is_bounded():
    source = Path("app/routers/growth_agent.py").read_text(encoding="utf-8")
    assert "if idx in existing:" in source
    assert '"planned", "active", "done", "skipped"' in source
    assert 'router.post("/campaign/init")' in source
    assert 'router.patch("/campaign/day/{day_number}")' in source
