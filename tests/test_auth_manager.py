from src.huawei_health_mcp.auth.auth_manager import AuthManager, AuthStatus
from src.huawei_health_mcp.auth.credential_store import StoredCredentials


class MemoryStore:
    def __init__(self, stored=None):
        self.stored = stored

    def load(self):
        return self.stored

    def save(self, credentials):
        self.stored = credentials


class FakeEnvironment:
    def __init__(self, headers=None):
        self.headers = headers or {}

    def get_headers(self):
        return dict(self.headers)


class FakeBrowser:
    def __init__(self, refreshed):
        self.refreshed = refreshed
        self.seen_start_url = None

    def refresh(self, start_url):
        self.seen_start_url = start_url
        return self.refreshed


def test_stored_credentials_precede_environment_fallback():
    store = MemoryStore(StoredCredentials(
        {"Authorization": "Bearer stored", "x-client-id": "stored-client"},
        "https://camp.example/training",
    ))
    manager = AuthManager(
        store,
        browser_provider=FakeBrowser(None),
        environment_provider=FakeEnvironment({
            "Authorization": "Bearer env", "x-client-id": "env-client"
        }),
    )
    assert manager.get_headers()["Authorization"] == "Bearer stored"
    assert manager.status == AuthStatus.VALID


def test_recovery_saves_refreshed_credentials_and_updates_status():
    store = MemoryStore(StoredCredentials(
        {"Authorization": "Bearer old", "x-client-id": "old-client"},
        "https://camp.example/training",
    ))
    new = StoredCredentials(
        {"Authorization": "Bearer new", "x-client-id": "new-client"},
        "https://camp.example/training",
    )
    browser = FakeBrowser(new)
    manager = AuthManager(store, browser_provider=browser, environment_provider=FakeEnvironment())
    manager.mark_expired()
    assert manager.status == AuthStatus.EXPIRED
    assert manager.recover() is True
    assert browser.seen_start_url == "https://camp.example/training"
    assert store.stored == new
    assert manager.get_headers()["Authorization"] == "Bearer new"
    assert manager.status == AuthStatus.VALID


def test_no_credentials_requires_manual_authentication():
    manager = AuthManager(MemoryStore(), environment_provider=FakeEnvironment())
    try:
        manager.get_headers()
    except Exception as exc:
        assert "scripts/auth_login.py" in str(exc)
    else:
        raise AssertionError("Expected missing-credentials error")
    assert manager.status == AuthStatus.REAUTH_REQUIRED
