"""Synthetic transport tests; none establish real carrier delivery or eligibility."""
import base64
import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet

from blastio import provider
from blastio.config import settings

AC = "AC" + "a" * 32
SK = "SK" + "b" * 32
MG = "MG" + "c" * 32
PN = "PN" + "d" * 32
SM = "SM" + "e" * 32
AUTH = "f" * 32
SECRET = "synthetic-api-key-secret"
FROM = "+12025550101"
TO = "+12025550102"
PUBLIC = "https://blastio.example.test"


@pytest.fixture
def creds():
    return provider.Credentials(AC, SK, SECRET, AUTH, MG)


@pytest.fixture
def frozen(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            stamp = cls(2026, 9, 30, 16, 59, 30, tzinfo=timezone.utc)
            return stamp.astimezone(tz) if tz else stamp.replace(tzinfo=None)
    monkeypatch.setattr(provider, "datetime", FrozenDateTime)
    return FrozenDateTime.now(timezone.utc)


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE credentials(environment TEXT PRIMARY KEY,account_sid TEXT,api_key_sid TEXT,secret_encrypted TEXT,auth_token_encrypted TEXT,service_sid TEXT,verification TEXT,verified_at TEXT,created_at TEXT);
    CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE campaigns(id INTEGER PRIMARY KEY,status TEXT,mode TEXT,pause_reason TEXT);
    CREATE TABLE messages(id INTEGER PRIMARY KEY,state TEXT,mode TEXT,reason TEXT,updated_at TEXT);
    CREATE TABLE audit_log(id INTEGER PRIMARY KEY,actor TEXT,action TEXT,entity_type TEXT,entity_id TEXT,detail TEXT,created_at TEXT);
    """)
    yield conn
    conn.close()


def signature(token, url, params):
    value = url + "".join(k + v for k, v in sorted(params.items()))
    return base64.b64encode(hmac.new(token.encode(), value.encode(), hashlib.sha1).digest()).decode()


def good_responses(*, campaign_mock=False, per_number=False, number_url=None):
    return {
        f"/2010-04-01/Accounts/{AC}.json": {"sid": AC, "status": "active", "type": "Full", "auth_token": AUTH, "friendly_name": "Synthetic account"},
        "/v1/Services": {"services": [{"sid": MG, "account_sid": AC, "friendly_name": "Existing service"}], "meta": {"next_page_url": None}},
        f"/v1/Services/{MG}": {"sid": MG, "account_sid": AC, "inbound_request_url": PUBLIC + "/webhooks/twilio/inbound", "inbound_method": "POST", "status_callback": PUBLIC + "/webhooks/twilio/status", "use_inbound_webhook_on_number": per_number, "sticky_sender": True},
        f"/v1/Services/{MG}/Compliance/Usa2p": {"compliance": [{"sid": "QE" + "1" * 32, "account_sid": AC, "messaging_service_sid": MG, "campaign_id": "CSYNTHETIC", "campaign_status": "VERIFIED", "mock": campaign_mock}], "meta": {"next_page_url": None}},
        f"/v1/Services/{MG}/PhoneNumbers": {"phone_numbers": [{"sid": PN, "account_sid": AC, "service_sid": MG, "phone_number": FROM}], "meta": {"next_page_url": None}},
        f"/2010-04-01/Accounts/{AC}/IncomingPhoneNumbers/{PN}.json": {"sid": PN, "account_sid": AC, "phone_number": FROM, "capabilities": {"sms": True}, "sms_url": number_url or PUBLIC + "/webhooks/twilio/inbound", "sms_method": "POST", "sms_application_sid": None},
    }


def client_for(responses, requests):
    def handle(request):
        requests.append(request)
        result = responses.get(request.url.path)
        if isinstance(result, httpx.Response):
            return result
        assert result is not None, f"Unexpected provider endpoint {request.url.path}"
        return httpx.Response(200, json=result)
    return httpx.Client(transport=httpx.MockTransport(handle))


def message(at):
    return {"id": 7, "to": TO, "sender": FROM, "service_sid": MG, "body": "101XVC: Authorized synthetic test. Reply STOP to unsubscribe",
            "validity_period": 300, "expires_at": (at + timedelta(minutes=20)).isoformat(), "timezone": "UTC",
            "policy": {"window_start": 9, "window_end": 17, "max_provider_validity_seconds": 300}}


def test_verification_reads_actual_resources_without_mutations_or_secret_leak(creds):
    requests = []
    client = client_for(good_responses(), requests)
    result = provider.TwilioProvider(credentials=creds, client=client, public_url=PUBLIC).verify()
    assert all(request.method == "GET" for request in requests)
    assert len(requests) == 6
    assert result["account_ok"] and result["service_ok"] and result["campaign_ok"] and result["webhooks_ok"]
    assert result["campaign_id"] == "CSYNTHETIC"
    assert result["authorized_senders"] == [FROM]
    assert result["senders"][0]["carrier_registration"] == "manual_review"
    assert result["production_eligible"] is False
    assert "Advanced Opt-Out" in " ".join(result["manual_review"])
    serialized = json.dumps(result)
    assert AUTH not in serialized and SECRET not in serialized and "auth_token" not in serialized
    # Separate real Auth Token checks account/signature credentials. Every messaging
    # and phone read uses the API key rather than elevating a restricted key.
    assert requests[0].headers["authorization"] == "Basic " + base64.b64encode((AC + ":" + AUTH).encode()).decode()
    assert all(r.headers["authorization"] == "Basic " + base64.b64encode((SK + ":" + SECRET).encode()).decode() for r in requests[1:])


@pytest.mark.parametrize("mock", [True, None])
def test_mock_or_unexposed_campaign_status_cannot_be_verified(creds, mock):
    requests = []
    result = provider.TwilioProvider(credentials=creds, client=client_for(good_responses(campaign_mock=mock), requests), public_url=PUBLIC).verify()
    assert result["campaign_ok"] is False
    assert result["campaign_id"] is None


def test_restricted_key_missing_campaign_permission_fails_closed_without_auth_fallback(creds):
    responses = good_responses()
    responses[f"/v1/Services/{MG}/Compliance/Usa2p"] = httpx.Response(403, json={"code": 20003, "message": SECRET + AUTH})
    requests = []
    result = provider.TwilioProvider(credentials=creds, client=client_for(responses, requests), public_url=PUBLIC).verify()
    assert result["campaign_ok"] is False
    assert result["checks"]["campaigns"]["status"] == "unverified"
    calls = [r for r in requests if r.url.path.endswith("/Compliance/Usa2p")]
    assert len(calls) == 1
    assert AUTH not in json.dumps(result) and SECRET not in json.dumps(result)


def test_sender_number_webhook_override_is_effective_configuration(creds):
    requests = []
    result = provider.TwilioProvider(credentials=creds, client=client_for(good_responses(per_number=True, number_url="https://old.example.test/inbound"), requests), public_url=PUBLIC).verify()
    assert result["service_ok"] and result["authorized_senders"] == [FROM]
    assert result["senders"][0]["inbound_configured"] is False
    assert result["webhooks_ok"] is False


def test_unknown_effective_inbound_routing_fails_closed(creds):
    responses = good_responses()
    del responses[f"/v1/Services/{MG}"]["use_inbound_webhook_on_number"]
    result = provider.TwilioProvider(credentials=creds, client=client_for(responses, []), public_url=PUBLIC).verify()
    assert result["webhooks_ok"] is False


def test_manual_readiness_fingerprint_ignores_refresh_time_but_binds_sender_and_campaign_facts(creds):
    facts = provider.TwilioProvider(credentials=creds, client=client_for(good_responses(), []), public_url=PUBLIC).verify()
    original = provider.verification_review_fingerprint(facts)
    facts["checked_at"] = "2027-01-01T00:00:00Z"
    facts["account"]["date_updated"] = "2027-01-01T00:00:00Z"
    facts["checks"]["account"]["checked_at"] = "2027-01-01T00:00:00Z"
    assert provider.verification_review_fingerprint(facts) == original
    facts["authorized_senders"].append("+12025550103")
    assert provider.verification_review_fingerprint(facts) != original
    facts["authorized_senders"].pop()
    facts["campaigns"][0]["message_flow"] = "Changed registered consent flow"
    assert provider.verification_review_fingerprint(facts) != original


def test_sender_pool_membership_without_sms_capability_is_unavailable(creds):
    responses = good_responses()
    responses[f"/2010-04-01/Accounts/{AC}/IncomingPhoneNumbers/{PN}.json"]["capabilities"]["sms"] = False
    result = provider.TwilioProvider(credentials=creds, client=client_for(responses, []), public_url=PUBLIC).verify()
    assert result["authorized_senders"] == [] and result["webhooks_ok"] is False


def test_existing_service_default_callback_can_be_preserved_for_other_operations(creds):
    responses = good_responses()
    responses[f"/v1/Services/{MG}"]["status_callback"] = "https://legacy.example.test/status"
    requests = []
    result = provider.TwilioProvider(credentials=creds, client=client_for(responses, requests), public_url=PUBLIC).verify()
    assert result["webhooks_ok"] is True
    assert result["webhooks"]["service_default_status_callback_matches"] is False
    assert result["webhooks"]["service_default_status_callback"] == "https://legacy.example.test/status"
    assert result["webhooks"]["status_callback_strategy"] == "per_message_override"
    assert all(r.method == "GET" for r in requests)


def test_wrong_account_auth_token_and_trial_account_never_pass_account_gate(creds):
    responses = good_responses()
    responses[f"/2010-04-01/Accounts/{AC}.json"]["auth_token"] = "0" * 32
    result = provider.TwilioProvider(credentials=creds, client=client_for(responses, []), public_url=PUBLIC).verify()
    assert result["account_ok"] is False
    assert result["checks"]["signature_token"]["status"] == "unverified"
    responses[f"/2010-04-01/Accounts/{AC}.json"]["auth_token"] = AUTH
    responses[f"/2010-04-01/Accounts/{AC}.json"]["type"] = "Trial"
    assert provider.TwilioProvider(credentials=creds, client=client_for(responses, []), public_url=PUBLIC).verify()["account_ok"] is False


def test_pagination_does_not_leak_auth_to_provider_supplied_external_url(creds):
    responses = good_responses()
    responses["/v1/Services"]["meta"]["next_page_url"] = "https://evil.example.test/steal"
    requests = []
    result = provider.TwilioProvider(credentials=creds, client=client_for(responses, requests), public_url=PUBLIC).verify()
    assert result["checks"]["services"]["status"] == "unverified"
    assert all(r.url.host in {"api.twilio.com", "messaging.twilio.com"} for r in requests)


def test_valid_signature_uses_all_fields_and_exact_external_url():
    url = PUBLIC + "/webhooks/twilio/status?message_id=7&x=a%2Fb"
    params = {"AccountSid": AC, "MessageSid": SM, "MessageStatus": "delivered", "FutureProviderField": "new", "From": FROM, "To": TO}
    signed = signature(AUTH, url, params)
    assert provider.verify_signature(url, params, signed, AUTH)
    assert not provider.verify_signature(url.replace("a%2Fb", "a/b"), params, signed, AUTH)
    assert not provider.verify_signature(url, {k: v for k, v in params.items() if k != "FutureProviderField"}, signed, AUTH)
    assert not provider.verify_signature(url, params, signed, SECRET)
    assert not provider.verify_signature(url, params, "invalid", AUTH)
    assert not provider.verify_signature(url, params, signed, "")


def test_signature_supports_repeated_form_parameters_without_dropping_values():
    class MultiDict(dict):
        def getlist(self, key):
            return self[key]
    url = PUBLIC + "/webhooks/twilio/inbound"
    fields = MultiDict(Body=["hello", "hello", "STOP"], From=[TO])
    value = url + "BodySTOPBodyhelloFrom" + TO
    signed = base64.b64encode(hmac.new(AUTH.encode(), value.encode(), hashlib.sha1).digest()).decode()
    assert provider.verify_signature(url, fields, signed, AUTH)
    assert not provider.verify_signature(url, {"Body": "hello", "From": TO}, signed, AUTH)


def test_send_is_bounded_uses_exact_body_and_caps_provider_queue_before_local_close(creds, frozen):
    requests = []
    def handle(request):
        requests.append(request)
        assert request.method == "POST"
        fields = parse_qs(request.content.decode())
        assert fields["ValidityPeriod"] == ["30"]
        assert fields["MessagingServiceSid"] == [MG]
        assert fields["From"] == [FROM]
        assert fields["Body"] == [message(frozen)["body"]]
        assert fields["SmartEncoded"] == ["false"]
        assert fields["StatusCallback"] == [PUBLIC + "/webhooks/twilio/status?message_id=7"]
        assert all(value <= 8 for value in request.extensions["timeout"].values())
        return httpx.Response(201, json={"sid": SM, "account_sid": AC, "to": TO, "from": FROM, "messaging_service_sid": MG, "status": "queued"})
    result = provider.TwilioProvider(credentials=creds, client=httpx.Client(transport=httpx.MockTransport(handle)), public_url=PUBLIC).send(message(frozen))
    assert result["sid"] == SM and result["status"] == "queued"
    assert len(requests) == 1


@pytest.mark.parametrize("failure, exception", [(httpx.ReadTimeout, provider.ProviderUnknown), (httpx.WriteTimeout, provider.ProviderUnknown), (httpx.ConnectTimeout, provider.ProviderRejected), (httpx.ConnectError, provider.ProviderRejected)])
def test_transport_failure_classifies_uncertainty_without_retry(creds, frozen, failure, exception):
    calls = []
    def handle(request):
        calls.append(request)
        raise failure("synthetic raw exception " + AUTH + SECRET, request=request)
    p = provider.TwilioProvider(credentials=creds, client=httpx.Client(transport=httpx.MockTransport(handle)), public_url=PUBLIC)
    with pytest.raises(exception) as caught:
        p.send(message(frozen))
    assert len(calls) == 1
    assert caught.value.uncertain == (exception is provider.ProviderUnknown)
    assert AUTH not in str(caught.value) and SECRET not in str(caught.value)


@pytest.mark.parametrize("status, exception", [(400, provider.ProviderRejected), (401, provider.ProviderRejected), (429, provider.ProviderRejected), (500, provider.ProviderUnknown), (503, provider.ProviderUnknown)])
def test_error_http_response_does_not_expose_raw_body_or_retry(creds, frozen, status, exception):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"code": 21610, "message": "secret " + SECRET + AUTH})
    p = provider.TwilioProvider(credentials=creds, client=httpx.Client(transport=httpx.MockTransport(handle)), public_url=PUBLIC)
    with pytest.raises(exception) as caught:
        p.send(message(frozen))
    assert len(calls) == 1 and caught.value.error_code == 21610
    assert SECRET not in caught.value.detail and AUTH not in caught.value.detail


def test_success_response_without_identifier_is_unknown(creds, frozen):
    p = provider.TwilioProvider(credentials=creds, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(201, json={"status": "queued"}))), public_url=PUBLIC)
    with pytest.raises(provider.ProviderUnknown):
        p.send(message(frozen))


def test_fetch_message_exposes_exact_evidence_for_unknown_sid_reconciliation_without_writing(creds):
    calls = []
    expected_body = "101XVC: Authorized synthetic test. Reply STOP to unsubscribe"
    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/2010-04-01/Accounts/{AC}/Messages/{SM}.json"
        return httpx.Response(200, json={
            "sid": SM, "account_sid": AC, "to": TO, "from": FROM,
            "messaging_service_sid": MG, "body": expected_body, "status": "delivered",
            "date_created": "Wed, 30 Sep 2026 16:59:31 +0000",
            "date_sent": "Wed, 30 Sep 2026 16:59:32 +0000",
            "date_updated": "Wed, 30 Sep 2026 16:59:33 +0000", "auth_token": AUTH,
            "secret": SECRET, "subresource_uris": {"media": "private"},
        })
    p = provider.TwilioProvider(credentials=creds, client=httpx.Client(transport=httpx.MockTransport(handle)), public_url=PUBLIC)
    evidence = p.fetch_message(SM)
    assert len(calls) == 1
    assert evidence["body"] == expected_body
    assert evidence["to"] == TO and evidence["from"] == FROM and evidence["messaging_service_sid"] == MG
    assert evidence["date_created"] == "Wed, 30 Sep 2026 16:59:31 +0000"
    assert evidence["date_sent"] == "Wed, 30 Sep 2026 16:59:32 +0000"
    assert AUTH not in json.dumps(evidence) and SECRET not in json.dumps(evidence)
    assert "subresource_uris" not in evidence


def test_fetch_message_rejects_mismatched_provider_account_or_sid(creds):
    p = provider.TwilioProvider(credentials=creds, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"sid": SM, "account_sid": "AC" + "1" * 32}))), public_url=PUBLIC)
    with pytest.raises(provider.ProviderRejected):
        p.fetch_message(SM)


@pytest.mark.parametrize("update", [{"expires_at": "2000-01-01T12:00:00+00:00"}, {"timezone": "Unknown/Uncertain"}, {"validity_period": 0}, {"sender": "invalid"}, {"service_sid": "MG" + "1" * 32}])
def test_missing_schedule_or_unbound_sender_service_never_performs_io(creds, frozen, update):
    def forbidden(request):
        pytest.fail("Invalid message reached network transport")
    p = provider.TwilioProvider(credentials=creds, client=httpx.Client(transport=httpx.MockTransport(forbidden)), public_url=PUBLIC)
    payload = message(frozen)
    payload.update(update)
    with pytest.raises(provider.ProviderRejected):
        p.send(payload)


def test_credentials_are_encrypted_masked_and_never_audited_as_secrets(store):
    public = provider.save_credentials(store, {"account_sid": AC, "api_key_sid": SK, "api_key_secret": SECRET, "auth_token": AUTH, "service_sid": MG}, "admin")
    saved = store.execute("SELECT * FROM credentials").fetchone()
    assert saved["secret_encrypted"] != SECRET and saved["auth_token_encrypted"] != AUTH
    assert provider.decrypt_secret(saved["secret_encrypted"]) == SECRET
    assert provider.decrypt_secret(saved["auth_token_encrypted"]) == AUTH
    audit = json.dumps([dict(r) for r in store.execute("SELECT * FROM audit_log")])
    serialized = json.dumps(public)
    assert SECRET not in audit + serialized and AUTH not in audit + serialized
    assert public["auth_token"] == "********" and public["api_key_secret"] == "********"
    assert SECRET not in repr(provider.load_credentials(store)) and AUTH not in repr(provider.load_credentials(store))


def test_secret_preservation_is_bound_to_original_identifiers_and_rotation_invalidates_review(store):
    values = {"account_sid": AC, "api_key_sid": SK, "api_key_secret": SECRET, "auth_token": AUTH, "service_sid": MG}
    provider.save_credentials(store, values, "admin")
    store.execute("UPDATE credentials SET verification='{}',verified_at='2026-09-30'")
    store.execute("INSERT INTO campaigns VALUES(1,'scheduled','production',NULL)")
    store.execute("UPDATE settings SET value='{\"advanced_opt_out_reviewed\":true}' WHERE key='readiness'")
    values["api_key_secret"] = values["auth_token"] = "********"
    provider.save_credentials(store, values, "admin")
    assert provider.load_credentials(store).secret == SECRET
    assert store.execute("SELECT verified_at FROM credentials").fetchone()[0] is None
    assert store.execute("SELECT value FROM settings WHERE key='readiness'").fetchone()[0] == "{}"
    assert store.execute("SELECT status FROM campaigns").fetchone()[0] == "paused"
    values["api_key_sid"] = "SK" + "1" * 32
    with pytest.raises(provider.ProviderRejected):
        provider.save_credentials(store, values, "admin")


def test_local_revocation_cancels_only_unsent_and_does_not_claim_provider_key_deleted(store):
    provider.save_credentials(store, {"account_sid": AC, "api_key_sid": SK, "api_key_secret": SECRET, "auth_token": AUTH, "service_sid": MG}, "admin")
    store.execute("INSERT INTO messages VALUES(1,'queued','production',NULL,NULL)")
    store.execute("INSERT INTO messages VALUES(2,'accepted','production',NULL,NULL)")
    result = provider.revoke_credentials(store, "admin")
    assert result["console_key_revocation_required"] is True
    assert provider.masked_credentials(store)["connected"] is False
    assert [r[0] for r in store.execute("SELECT state FROM messages ORDER BY id")] == ["canceled", "accepted"]


def test_simulation_never_reads_credentials_or_opens_http(monkeypatch):
    monkeypatch.setattr(provider, "_settings", lambda: pytest.fail("Simulation read production settings"))
    monkeypatch.setattr(provider.httpx, "Client", lambda **kwargs: pytest.fail("Simulation opened HTTP client"))
    p = provider.SimulationProvider()
    assert p.environment == "simulation"
    assert p.send({"sender": FROM, "to": TO})["simulated"] is True
    assert p.verify()["account_ok"] is False


def test_simulation_environment_cannot_construct_twilio(creds):
    with pytest.raises(provider.ProviderRejected):
        provider.TwilioProvider(environment="simulation", credentials=creds)
