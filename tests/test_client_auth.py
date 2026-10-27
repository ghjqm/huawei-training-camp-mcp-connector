import httpx

from src.huawei_health_mcp.client import HTCClient, HTCHTTPError


class RotatingAuth:
    def __init__(self, can_recover=True):
        self.current = "Bearer stale"
        self.can_recover = can_recover
        self.recover_calls = 0
        self.expired_calls = 0

    def get_headers(self):
        return {"Authorization": self.current, "x-client-id": "synthetic-client"}

    def mark_expired(self):
        self.expired_calls += 1

    def recover(self):
        self.recover_calls += 1
        if self.can_recover:
            self.current = "Bearer fresh"
            return True
        return False


def test_401_recovers_and_retries_exactly_once():
    received = []

    def handler(request):
        received.append(request.headers["authorization"])
        if request.headers["authorization"] == "Bearer stale":
            return httpx.Response(401, text="unauthorized")
        return httpx.Response(200, json={"ok": True})

    auth = RotatingAuth()
    client = HTCClient(
        auth_manager=auth,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.query_activity_records("1", "2") == {"ok": True}
    assert received == ["Bearer stale", "Bearer fresh"]
    assert auth.recover_calls == 1
    assert auth.expired_calls == 1


def test_failed_recovery_does_not_retry_forever():
    requests = 0

    def handler(request):
        nonlocal requests
        requests += 1
        return httpx.Response(401, text="unauthorized")

    auth = RotatingAuth(can_recover=False)
    client = HTCClient(
        auth_manager=auth,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        client.query_activity_records("1", "2")
    except HTCHTTPError as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("Expected 401")
    assert requests == 1
    assert auth.recover_calls == 1
