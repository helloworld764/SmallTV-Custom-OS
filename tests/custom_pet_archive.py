"""Explicit opt-in for retained custom-pet tests; never enables production apps."""
import pytest

@pytest.fixture
def archived_custom_pets(monkeypatch):
    from backend.app import features
    from backend.app.custom_pets import router
    import test_auth,test_telegram
    monkeypatch.setattr(features,'CUSTOM_PETS_ENABLED',True)
    original=test_auth.create_app
    def archived_app(*args,**kwargs):
        app=original(*args,**kwargs);app.include_router(router);return app
    monkeypatch.setattr(test_auth,'create_app',archived_app)
    monkeypatch.setattr(test_telegram,'create_app',archived_app)
