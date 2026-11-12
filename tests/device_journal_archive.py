"""Retained journal checks opt in only in isolated tests, never in production."""
import pytest

@pytest.fixture
def archived_device_journal(monkeypatch):
    from backend.app import features
    from backend.app.device_journal import router
    import test_auth
    monkeypatch.setattr(features,'DEVICE_JOURNAL_ENABLED',True)
    original=test_auth.create_app
    def archived_app(*args,**kwargs):
        app=original(*args,**kwargs);app.include_router(router);return app
    monkeypatch.setattr(test_auth,'create_app',archived_app)
