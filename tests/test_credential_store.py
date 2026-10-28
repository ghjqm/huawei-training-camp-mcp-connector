import os

import pytest

from src.huawei_health_mcp.auth.credential_store import (
    CredentialStore,
    CredentialStoreError,
    StoredCredentials,
)
from src.huawei_health_mcp.auth.browser_session import profile_path


def test_shared_state_overrides_host_appdata(monkeypatch, tmp_path):
    shared = tmp_path / "shared"
    monkeypatch.setenv("HTC_STATE_DIR", str(shared))
    for host in ("desktop-package", "scheduled-task"):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / host))
        assert CredentialStore().path == shared / "credentials.dpapi"
        assert profile_path() == shared / "browser-profile"


def test_shared_state_rejects_relative_path(monkeypatch):
    monkeypatch.setenv("HTC_STATE_DIR", "relative-state")
    with pytest.raises(CredentialStoreError, match="absolute"):
        CredentialStore()


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_dpapi_store_round_trip_and_ciphertext(tmp_path):
    store = CredentialStore(tmp_path / "creds.dpapi")
    credentials = StoredCredentials(
        headers={"Authorization": "Bearer synthetic-secret", "x-client-id": "synthetic-id"},
        page_url="https://camp.example/training",
    )
    store.save(credentials)
    ciphertext = store.path.read_bytes()
    assert b"synthetic-secret" not in ciphertext
    assert b"synthetic-id" not in ciphertext
    assert store.load() == credentials
    store.clear()
    assert store.load() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_store_rejects_unrecognized_headers(tmp_path):
    store = CredentialStore(tmp_path / "creds.dpapi")
    with pytest.raises(CredentialStoreError):
        store.save(StoredCredentials({
            "Authorization": "Bearer synthetic",
            "x-client-id": "synthetic-id",
            "x-unrelated-secret": "do-not-save",
        }))
