def test_growth_models_import_and_register_tables():
    from app.models.growth import GrowthExperiment, GrowthProspect, GrowthTouch

    assert GrowthProspect.__tablename__ == "growth_prospects"
    assert GrowthTouch.__tablename__ == "growth_touches"
    assert GrowthExperiment.__tablename__ == "growth_experiments"
