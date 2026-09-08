import ast
from pathlib import Path


def test_growth_models_define_persistent_funnel_tables():
    source = Path("app/models/growth.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    tables = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    }
    assert {"growth_prospects", "growth_touches", "growth_experiments"} <= tables


def test_growth_migration_is_single_forward_revision_from_current_head():
    source = Path("alembic/versions/0023_growth_funnel.py").read_text(encoding="utf-8")
    assert 'revision = "growth_funnel_023"' in source
    assert 'down_revision = "push_subscriptions_022"' in source
    assert "DROP TABLE" not in source.upper()


def test_contact_stage_requires_human_approval_in_router():
    source = Path("app/routers/growth_agent.py").read_text(encoding="utf-8")
    assert 'stage == "contacted"' in source
    assert 'approved_by_human' in source
    assert 'router.post("/campaign/init")' in source
