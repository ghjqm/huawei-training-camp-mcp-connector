"""Report whether this Windows user has a decryptable local credential set."""

from __future__ import annotations

from src.huawei_health_mcp.auth.credential_store import CredentialStore, CredentialStoreError


def main() -> int:
    try:
        credentials = CredentialStore().load()
    except CredentialStoreError:
        print("DPAPI_STATUS=ERROR (encrypted credentials unavailable)")
        return 2
    if credentials is None:
        print("DPAPI_STATUS=REAUTH_REQUIRED (no local credentials)")
        return 1
    print("DPAPI_STATUS=VALID (decryptable; no network request made)")
    print(f"RECOVERY_ROUTE={'available' if credentials.page_url else 'unavailable'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
