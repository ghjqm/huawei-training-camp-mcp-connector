import asyncio
import os
import time

import pytest

from src.huawei_health_mcp.auth.auth_manager import AuthManager, AuthStatus
from src.huawei_health_mcp.auth.cookie_store import (
    SessionCookieStore,
    cookies_to_restore,
    export_cookies,
    filter_cookies,
    restore_cookies,
)
from src.huawei_health_mcp.auth.credential_store import StoredCredentials
from src.huawei_health_mcp.auth.keeper import KeeperChannel


def _cookie(name, domain, expires=-1, value="synthetic-value"):
    return {"name": name, "value": value, "domain": domain, "path": "/",
            "expires": expires, "httpOnly": True, "secure": True, "sameSite": "Lax"}


def test_filter_keeps_only_huawei_login_domains():
    cookies = [
        _cookie("CASTGC", ".id5.cloud.huawei.com"),
        _cookie("HWWAFSESID", "health.cloud.huawei.com"),
        _cookie("x", "hihealthbase-drcn.things.dbankcloud.cn"),
        _cookie("tracker", ".example.com"),
        _cookie("evil", "huawei.com.evil.test"),
        _cookie("evil2", "nothuawei.com"),
    ]
    names = [c["name"] for c in filter_cookies(cookies)]
    assert names == ["CASTGC", "HWWAFSESID", "x"]


def test_restore_selects_session_cookies_only():
    cookies = [
        _cookie("CASTGC", ".id5.cloud.huawei.com", expires=-1),
        _cookie("remember", ".id5.cloud.huawei.com", expires=time.time() + 86400),
    ]
    assert [c["name"] for c in cookies_to_restore(cookies)] == ["CASTGC"]


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_cookie_store_round_trip_is_encrypted(tmp_path):
    store = SessionCookieStore(tmp_path / "cookies.dpapi")
    saved = store.save([_cookie("CASTGC", ".id5.cloud.huawei.com", value="secret-ticket"),
                        _cookie("other", ".example.com")])
    assert saved == 1
    assert b"secret-ticket" not in store.path.read_bytes()
    loaded = store.load()
    assert [c["name"] for c in loaded] == ["CASTGC"]
    assert loaded[0]["value"] == "secret-ticket"
    store.clear()
    assert store.load() == []


class FakeContext:
    def __init__(self, cookies=None):
        self._cookies = cookies or []
        self.added = None

    async def cookies(self):
        return list(self._cookies)

    async def add_cookies(self, cookies):
        self.added = cookies


class MemoryCookieStore:
    def __init__(self, cookies=None):
        self.cookies = cookies or []

    def load(self):
        return list(self.cookies)

    def save(self, cookies):
        self.cookies = filter_cookies(cookies)
        return len(self.cookies)


def test_export_then_restore_into_fresh_context():
    store = MemoryCookieStore()
    old = FakeContext([_cookie("CASTGC", ".id5.cloud.huawei.com")])
    assert asyncio.run(export_cookies(old, store)) == 1
    fresh = FakeContext()
    assert asyncio.run(restore_cookies(fresh, store)) == 1
    assert fresh.added[0]["name"] == "CASTGC"


def test_restore_and_export_are_best_effort():
    class Broken:
        def load(self):
            raise RuntimeError("boom")

        def save(self, cookies):
            raise RuntimeError("boom")

    assert asyncio.run(restore_cookies(FakeContext(), Broken())) == 0
    assert asyncio.run(export_cookies(FakeContext(), Broken())) == 0
    assert asyncio.run(restore_cookies(FakeContext(), None)) == 0


def test_keeper_heartbeat_and_requests(tmp_path):
    channel = KeeperChannel(tmp_path)
    assert not channel.is_alive()
    channel.beat()
    assert channel.is_alive()
    channel.heartbeat_path.write_text(f"{time.time() - 120:.3f}", encoding="ascii")
    assert not channel.is_alive()
    assert not channel.take_request()
    channel.request_refresh()
    assert channel.take_request()
    assert not channel.take_request()
    channel.beat()
    channel.stop()
    assert not channel.is_alive()


# --- AuthManager coordination ------------------------------------------------

class MemoryStore:
    def __init__(self, stored=None):
        self.stored = stored

    def load(self):
        return self.stored

    def save(self, credentials):
        self.stored = credentials


class FailingBrowser:
    def __init__(self):
        self.calls = 0

    def refresh(self, start_url):
        self.calls += 1
        return None


class FakeEnv:
    def get_headers(self):
        return {}


def _creds(token):
    return StoredCredentials({"Authorization": token, "x-client-id": "c"}, "https://camp.example/t")


def test_recover_uses_credentials_refreshed_by_another_process():
    store = MemoryStore(_creds("Bearer old"))
    browser = FailingBrowser()
    manager = AuthManager(store, browser_provider=browser, environment_provider=FakeEnv())
    manager.get_headers()
    store.stored = _creds("Bearer from-keeper")
    assert manager.recover() is True
    assert browser.calls == 0
    assert manager.get_headers()["Authorization"] == "Bearer from-keeper"


class ScriptedKeeper:
    def __init__(self, store, deliver):
        self.store = store
        self.deliver = deliver
        self.requests = 0

    def is_alive(self):
        return True

    def request_refresh(self):
        self.requests += 1
        if self.deliver:
            self.store.stored = _creds("Bearer keeper-fresh")


def test_recover_delegates_to_live_keeper_without_opening_browser():
    store = MemoryStore(_creds("Bearer old"))
    browser = FailingBrowser()
    keeper = ScriptedKeeper(store, deliver=True)
    manager = AuthManager(store, browser_provider=browser, environment_provider=FakeEnv(),
                          keeper=keeper, keeper_wait_seconds=5)
    manager.get_headers()
    assert manager.recover() is True
    assert keeper.requests == 1
    assert browser.calls == 0
    assert manager.status == AuthStatus.VALID


def test_recover_reports_reauth_when_keeper_cannot_refresh():
    store = MemoryStore(_creds("Bearer old"))
    browser = FailingBrowser()
    keeper = ScriptedKeeper(store, deliver=False)
    manager = AuthManager(store, browser_provider=browser, environment_provider=FakeEnv(),
                          keeper=keeper, keeper_wait_seconds=1.5)
    manager.get_headers()
    assert manager.recover() is False
    assert browser.calls == 0
    assert manager.status == AuthStatus.REAUTH_REQUIRED


def test_provider_tries_headless_before_visible(monkeypatch, tmp_path):
    from src.huawei_health_mcp.auth.credential_store import CredentialStore
    from src.huawei_health_mcp.auth.providers import BrowserSessionProvider

    provider = BrowserSessionProvider(CredentialStore(tmp_path / "c.dpapi"))
    assert provider.cookie_store.path == tmp_path / "session-cookies.dpapi"
    modes = []

    async def fake_capture(start_url, *, headless=False, timeout_seconds=None):
        modes.append(headless)
        return None if headless else _creds("Bearer visible")

    monkeypatch.setattr(provider, "_capture", fake_capture)
    assert provider.refresh("https://camp.example/t").headers["Authorization"] == "Bearer visible"
    assert modes == [True, False]

    modes.clear()

    async def headless_ok(start_url, *, headless=False, timeout_seconds=None):
        modes.append(headless)
        return _creds("Bearer silent")

    monkeypatch.setattr(provider, "_capture", headless_ok)
    assert provider.refresh("https://camp.example/t").headers["Authorization"] == "Bearer silent"
    assert modes == [True]
