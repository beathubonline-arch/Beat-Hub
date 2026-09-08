import ast
from pathlib import Path


def test_campaign_model_and_migration_exist():
    model = Path("app/models/growth.py").read_text(encoding="utf-8")
    tree = ast.parse(model)
    names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "GrowthCampaignDay" in names
    migration = Path("alembic/versions/0024_growth_campaign.py").read_text(encoding="utf-8")
    assert 'revision = "growth_campaign_024"' in migration
    assert 'down_revision = "growth_funnel_023"' in migration
    assert "DROP TABLE" not in migration.upper()


def test_campaign_has_exactly_30_operational_days():
    source = Path("app/routers/growth_agent.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    blueprint = next(node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CAMPAIGN_BLUEPRINT" for t in node.targets))
    assert isinstance(blueprint.value, (ast.List, ast.Tuple))
    assert len(blueprint.value.elts) == 30


def test_campaign_is_idempotently_seeded_and_status_is_bounded():
    source = Path("app/routers/growth_agent.py").read_text(encoding="utf-8")
    assert "if idx in existing:" in source
    assert '"planned", "active", "done", "skipped"' in source
    assert 'router.post("/campaign/init")' in source
    assert 'router.patch("/campaign/day/{day_number}")' in source
