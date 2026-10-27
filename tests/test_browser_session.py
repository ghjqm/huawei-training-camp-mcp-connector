from src.huawei_health_mcp.auth.browser_session import (
    extract_auth_headers,
    has_required_header_names,
    is_target_request,
    safe_page_url,
)


def test_accepts_only_exact_https_activity_endpoint():
    assert is_target_request(
        "https://hihealthbase-drcn.things.dbankcloud.cn/healthrunninggroup/v1/activityRecord:query"
    )
    assert not is_target_request(
        "http://hihealthbase-drcn.things.dbankcloud.cn/healthrunninggroup/v1/activityRecord:query"
    )
    assert not is_target_request(
        "https://example.test/healthrunninggroup/v1/activityRecord:query"
    )
    assert not is_target_request(
        "https://hihealthbase-drcn.things.dbankcloud.cn/healthrunninggroup/v1/healthRecords"
    )


def test_checks_header_names_without_needing_values():
    assert has_required_header_names(["Authorization", "x-client-id"])
    assert not has_required_header_names(["Authorization"])
    assert not has_required_header_names(None)


def test_extracts_only_supported_auth_headers():
    headers = extract_auth_headers({
        "Authorization": "Bearer synthetic",
        "X-Client-Id": "synthetic-client",
        "x-version": "synthetic-version",
        "Cookie": "synthetic-cookie",
        "x-unrelated-secret": "must-not-be-copied",
    })
    assert headers == {
        "Authorization": "Bearer synthetic",
        "x-client-id": "synthetic-client",
        "x-version": "synthetic-version",
        "Cookie": "synthetic-cookie",
    }


def test_page_url_discards_query_and_fragment():
    assert safe_page_url("https://camp.example/path?ticket=private#section") == (
        "https://camp.example/path"
    )
    assert safe_page_url("http://camp.example/path") is None
    assert safe_page_url("https://user:pass@camp.example/path") is None
