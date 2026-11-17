from datetime import date
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from scripts import query_ercot_system_demand as query


def test_query_archive_uses_local_credentials_and_preserves_response(monkeypatch):
    monkeypatch.setattr(query, "load_dotenv", lambda path: None)
    monkeypatch.setenv("API_KEY_ERCOT_PRIMARY", "test-key")
    monkeypatch.setenv("USERNAME_ERCOT", "test-user")
    monkeypatch.setenv("PASSWORD_ERCOT", "test-password")
    requests = []
    expected = {"_meta": {"totalRecords": 1}, "archives": [{"docId": 123}]}

    def fake_request(request):
        requests.append(request)
        return {"id_token": "test-token"} if len(requests) == 1 else expected

    monkeypatch.setattr(query, "request_json", fake_request)
    result = query.query_demand(date(2014, 8, 1), date(2014, 8, 1), 1, 100)
    assert result == expected
    assert requests[0].full_url == query.TOKEN_URL
    assert parse_qs(requests[0].data.decode())["username"] == ["test-user"]
    parsed = urlparse(requests[1].full_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == query.ARCHIVE_URL
    assert parse_qs(parsed.query) == {
        "postDatetimeFrom": ["2014-08-01T00:00:00"],
        "postDatetimeTo": ["2014-08-01T23:59:59"],
        "page": ["1"],
        "size": ["100"],
    }
    assert requests[1].get_header("Authorization") == "Bearer test-token"
    assert requests[1].get_header("Ocp-apim-subscription-key") == "test-key"


def test_query_data_endpoint_uses_delivery_dates(monkeypatch):
    monkeypatch.setattr(query, "load_dotenv", lambda path: None)
    monkeypatch.setenv("ERCOT_API_SUBSCRIPTION_KEY", "test-key")
    monkeypatch.setenv("ERCOT_API_USERNAME", "test-user")
    monkeypatch.setenv("ERCOT_API_PASSWORD", "test-password")
    requests = []
    expected = {"_meta": {"totalRecords": 1}, "fields": [{"name": "demand"}], "data": [[123.0]]}

    def fake_request(request):
        requests.append(request)
        return {"id_token": "test-token"} if len(requests) == 1 else expected

    monkeypatch.setattr(query, "request_json", fake_request)
    result = query.query_demand(date(2014, 8, 1), date(2014, 8, 1), 1, 100, endpoint="data")
    assert result == expected
    parsed = urlparse(requests[1].full_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == query.DEMAND_URL
    assert parse_qs(parsed.query) == {
        "deliveryDateFrom": ["2014-08-01"],
        "deliveryDateTo": ["2014-08-01"],
        "page": ["1"],
        "size": ["100"],
    }


def test_http_errors_do_not_expose_response_or_request_secrets(monkeypatch):
    def fail(request, timeout):
        raise HTTPError(request.full_url, 401, "test-secret", Message(), None)

    monkeypatch.setattr(query, "urlopen", fail)
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        query.request_json(query.Request("https://example.invalid/?password=test-secret"))
    assert "test-secret" not in str(error.value)


def test_missing_subscription_key_fails_before_network(monkeypatch):
    monkeypatch.setattr(query, "load_dotenv", lambda path: None)
    monkeypatch.delenv("API_KEY_ERCOT_PRIMARY", raising=False)
    monkeypatch.delenv("ERCOT_API_SUBSCRIPTION_KEY", raising=False)
    with pytest.raises(ValueError, match="API_KEY_ERCOT_PRIMARY"):
        query.query_demand(date(2014, 1, 1), date(2014, 1, 1), 1, 100)


def test_interactive_prompt_does_not_contaminate_json_stdout(monkeypatch, capsys):
    monkeypatch.setattr(query, "load_dotenv", lambda path: None)
    monkeypatch.setenv("API_KEY_ERCOT_PRIMARY", "test-key")
    monkeypatch.delenv("USERNAME_ERCOT", raising=False)
    monkeypatch.delenv("PASSWORD_ERCOT", raising=False)
    monkeypatch.delenv("ERCOT_API_USERNAME", raising=False)
    monkeypatch.delenv("ERCOT_API_PASSWORD", raising=False)
    monkeypatch.setattr("builtins.input", lambda: "test-user")
    monkeypatch.setattr(query, "getpass", lambda prompt: "test-password")
    monkeypatch.setattr(query, "request_json", lambda request: {"id_token": "test-token"})
    query.query_demand(date(2014, 1, 1), date(2014, 1, 1), 1, 100)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERCOT username" in captured.err