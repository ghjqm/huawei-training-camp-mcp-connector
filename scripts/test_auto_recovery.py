"""Exercise one real 401 recovery without replacing the user's saved token.

The test uses a temporary DPAPI store containing a synthetic invalid bearer,
then opens the persisted Training Camp route and retries once. On success, the
freshly captured credentials replace the real local store. No response data or
credential values are printed.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from src.huawei_health_mcp.auth.auth_manager import AuthManager
from src.huawei_health_mcp.auth.credential_store import (
    CredentialStore,
    StoredCredentials,
)
from src.huawei_health_mcp.auth.providers import BrowserSessionProvider
from src.huawei_health_mcp.client import HTCClient


def main() -> int:
    try:
        local_store = CredentialStore()
        current = local_store.load()
        if current is None or not current.page_url:
            print("AUTO_RECOVERY_CHECK=FAIL (run scripts.auth_login first)")
            return 1

        local_data = os.environ.get("LOCALAPPDATA")
        if not local_data:
            print("AUTO_RECOVERY_CHECK=FAIL (Windows user profile unavailable)")
            return 1

        with tempfile.TemporaryDirectory(
            prefix="htc-recovery-test-", dir=local_data
        ) as temp_dir:
            test_store = CredentialStore(Path(temp_dir) / "test.dpapi")
            test_store.save(StoredCredentials(
                headers={
                    "Authorization": "Bearer synthetic-invalid-recovery-test",
                    "x-client-id": current.headers["x-client-id"],
                },
                page_url=current.page_url,
            ))
            manager = AuthManager(
                test_store,
                browser_provider=BrowserSessionProvider(test_store, timeout_seconds=60),
            )
            client = HTCClient(auth_manager=manager, timeout=30)
            end_ms = int(time.time() * 1000)
            start_ms = end_ms - 30 * 24 * 60 * 60 * 1000
            try:
                client.query_activity_records(
                    str(start_ms), str(end_ms), limit=1
                )
            finally:
                client.close()

            refreshed = test_store.load()
            if refreshed is None or refreshed.headers.get("Authorization") == (
                "Bearer synthetic-invalid-recovery-test"
            ):
                print("AUTO_RECOVERY_CHECK=FAIL (fresh credentials were not captured)")
                return 1
            local_store.save(refreshed)

        print("AUTO_RECOVERY_CHECK=SUCCESS (one 401 recovery and one retry succeeded)")
        return 0
    except Exception:
        print("AUTO_RECOVERY_CHECK=FAIL (recovery or retried request failed)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
