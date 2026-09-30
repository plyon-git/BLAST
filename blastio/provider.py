"""Twilio REST adapter. Verification is read-only; configuration is operator-owned.

REST facts are deliberately separated from Console review and end-to-end webhook
health. This module never registers campaigns, changes senders or alters webhooks.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import Fernet, InvalidToken

API_ROOT = "https://api.twilio.com/2010-04-01"
MESSAGING_ROOT = "https://messaging.twilio.com/v1"
REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=3.0, pool=2.0)
MAX_PAGES = 25
MAX_RESOURCES = 1000
SOURCES = {
    "accounts": "https://www.twilio.com/docs/iam/api/account",
    "keys": "https://www.twilio.com/docs/iam/api-keys",
    "restricted_keys": "https://www.twilio.com/docs/iam/api-keys/restricted-api-keys",
    "services": "https://www.twilio.com/docs/messaging/api/service-resource",
    "senders": "https://www.twilio.com/docs/messaging/api/phonenumber-resource",
    "phone_numbers": "https://www.twilio.com/docs/phone-numbers/api/incomingphonenumber-resource",
    "campaigns": "https://www.twilio.com/docs/messaging/api/usapptoperson-resource",
    "messages": "https://www.twilio.com/docs/messaging/api/message-resource",
    "signatures": "https://www.twilio.com/docs/usage/security",
}


class ProviderError(RuntimeError):
    """Only sanitized details may be persisted in audit or attempt records."""

    uncertain = False

    def __init__(self, detail: str, error_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code


class ProviderRejected(ProviderError):
    """No Message was accepted, or the local request was never submitted."""


class ProviderUnknown(ProviderError):
    """Submission may have been accepted. Never blindly retry this attempt."""

    uncertain = True


@dataclass(frozen=True)
class Credentials:
    account_sid: str
    api_key_sid: str = ""
    secret: str = field(default="", repr=False)
    auth_token: str = field(default="", repr=False)
    service_sid: str = ""
    verification: dict = field(default_factory=dict, repr=False)


def _settings():
    from .config import settings
    return settings


def _sid(value: str, prefixes: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"(?:" + prefixes + r")[a-fA-F0-9]{32}", value))


def _cipher(key: str | bytes | None = None) -> Fernet:
    key = key if key is not None else _settings().encryption_key
    if not key:
        raise ProviderRejected("Server credential encryption key is not configured.")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError):
        raise ProviderRejected("Server credential encryption key is invalid.") from None


def encrypt_secret(value: str, key: str | bytes | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderRejected("A nonempty credential is required.")
    return _cipher(key).encrypt(value.encode()).decode()


def decrypt_secret(value: str, key: str | bytes | None = None) -> str:
    try:
        return _cipher(key).decrypt(value.encode()).decode()
    except (InvalidToken, UnicodeError, AttributeError):
        raise ProviderRejected("Saved credentials cannot be decrypted with this server key.") from None


def _validate_credentials(creds: Credentials) -> None:
    if not _sid(creds.account_sid, "AC"):
        raise ProviderRejected("Account SID must be a valid AC identifier.")
    if creds.service_sid and not _sid(creds.service_sid, "MG"):
        raise ProviderRejected("Messaging Service SID must be a valid MG identifier.")
    if not creds.auth_token or not re.fullmatch(r"[a-fA-F0-9]{32}", creds.auth_token):
        raise ProviderRejected("The real Account Auth Token is required for webhook signatures.")
    if creds.api_key_sid:
        if not _sid(creds.api_key_sid, "SK") or not creds.secret:
            raise ProviderRejected("A valid API Key SID and its secret are both required.")
    elif creds.secret:
        raise ProviderRejected("An API secret requires its API Key SID.")


def _credential_row(conn, environment: str):
    return conn.execute("SELECT * FROM credentials WHERE environment=?", (environment,)).fetchone()


def load_credentials(conn, environment: str = "production") -> Credentials:
    row = _credential_row(conn, environment)
    if not row:
        raise ProviderRejected("No credentials are connected for this environment.")
    creds = Credentials(
        account_sid=row["account_sid"], api_key_sid=row["api_key_sid"] or "",
        secret=decrypt_secret(row["secret_encrypted"]) if row["secret_encrypted"] else "",
        auth_token=decrypt_secret(row["auth_token_encrypted"]),
        service_sid=row["service_sid"] or "", verification=json.loads(row["verification"] or "{}"),
    )
    _validate_credentials(creds)
    return creds


def masked_credentials(conn, environment: str = "production") -> dict:
    row = _credential_row(conn, environment)
    if not row:
        return {"environment": environment, "connected": False, "verification": {}}
    return {
        "environment": environment, "connected": True,
        "account_sid": row["account_sid"], "api_key_sid": row["api_key_sid"] or "",
        "service_sid": row["service_sid"] or "", "api_key_secret": "********" if row["secret_encrypted"] else "",
        "auth_token": "********", "verified_at": row["verified_at"],
        "verification": json.loads(row["verification"] or "{}"),
    }


def save_credentials(conn, values: Mapping[str, Any], actor: str, environment: str = "production") -> dict:
    """Local storage only. Caller supplies authenticated admin identity and transaction.

    Replacement invalidates verification and Console readiness. Empty secret inputs
    preserve an existing secret only when its corresponding SID is unchanged.
    """
    from .db import audit, now, set_setting
    if environment not in {"production", "simulation"}:
        raise ProviderRejected("Unknown credential environment.")
    current = _credential_row(conn, environment)
    account = str(values.get("account_sid", current["account_sid"] if current else "")).strip()
    api_key = str(values.get("api_key_sid", current["api_key_sid"] if current else "")).strip()
    service = str(values.get("service_sid", current["service_sid"] if current else "")).strip()
    secret = values.get("api_key_secret", values.get("secret", "")) or ""
    auth_token = values.get("auth_token", "") or ""
    if secret == "********":
        secret = ""
    if auth_token == "********":
        auth_token = ""
    if not secret and api_key and current and account == current["account_sid"] and api_key == current["api_key_sid"] and current["secret_encrypted"]:
        secret = decrypt_secret(current["secret_encrypted"])
    if not auth_token and current and account == current["account_sid"]:
        auth_token = decrypt_secret(current["auth_token_encrypted"])
    creds = Credentials(account, api_key, str(secret), str(auth_token), service)
    _validate_credentials(creds)
    stamp = now()
    conn.execute(
        "INSERT INTO credentials(environment,account_sid,api_key_sid,secret_encrypted,auth_token_encrypted,service_sid,verification,verified_at,created_at) "
        "VALUES(?,?,?,?,?,?,?,NULL,?) ON CONFLICT(environment) DO UPDATE SET account_sid=excluded.account_sid,api_key_sid=excluded.api_key_sid,"
        "secret_encrypted=excluded.secret_encrypted,auth_token_encrypted=excluded.auth_token_encrypted,service_sid=excluded.service_sid,verification='{}',verified_at=NULL",
        (environment, account, api_key, encrypt_secret(creds.secret) if creds.secret else "", encrypt_secret(creds.auth_token), service, "{}", stamp),
    )
    if environment == "production":
        set_setting(conn, "readiness", {})
        conn.execute("UPDATE campaigns SET status='paused',pause_reason='Credentials changed; review and verify connection again' WHERE mode='production' AND status IN ('approved','scheduled')")
    audit(conn, actor, "credentials.replace", "credentials", environment,
          {"account_sid": account, "api_key_sid": api_key, "service_sid": service, "verification_invalidated": True})
    return masked_credentials(conn, environment)


def revoke_credentials(conn, actor: str, environment: str = "production") -> dict:
    """Revoke local application access; provider key revocation remains in Console."""
    from .db import audit, now, set_setting
    conn.execute("DELETE FROM credentials WHERE environment=?", (environment,))
    if environment == "production":
        set_setting(conn, "readiness", {})
    conn.execute("UPDATE campaigns SET status='paused',pause_reason='Application credential access revoked' WHERE mode=? AND status IN ('approved','scheduled')", (environment,))
    conn.execute("UPDATE messages SET state='canceled',reason='Application credential access revoked',updated_at=? WHERE mode=? AND state='queued'", (now(), environment))
    audit(conn, actor, "credentials.revoke_local", "credentials", environment, {"provider_configuration_changed": False})
    return {"environment": environment, "connected": False, "console_key_revocation_required": True}


def webhook_auth_token(conn, environment: str = "production") -> str:
    return load_credentials(conn, environment).auth_token


def verification_review_fingerprint(verification: Mapping[str, Any]) -> str:
    """Bind manual review to stable facts, excluding refresh timestamps.

    A refreshed service pool is not proof that newly added numbers underwent the
    operator's earlier carrier-registration review. Callers invalidate readiness
    when this fingerprint changes, without requiring a review for timestamp churn.
    """
    account = verification.get("account") or {}
    service = verification.get("service") or {}
    webhooks = verification.get("webhooks") or {}
    facts = {
        "gate_checks": {k: verification.get(k) for k in ("account_ok", "service_ok", "campaign_ok", "webhooks_ok", "service_sid", "campaign_id")},
        "account": _only(account, ("sid", "status", "type")),
        "service": _only(service, ("sid", "account_sid", "inbound_request_url", "inbound_method", "use_inbound_webhook_on_number", "sticky_sender")),
        "authorized_senders": sorted(verification.get("authorized_senders", [])),
        "campaigns": sorted([_only(c, ("sid", "campaign_id", "campaign_status", "brand_registration_sid", "us_app_to_person_usecase", "description", "message_flow", "message_samples", "opt_out_keywords", "opt_out_message", "subscriber_opt_in", "mock", "rate_limits")) for c in verification.get("campaigns", [])], key=lambda c: str(c.get("sid"))),
        "webhooks": _only(webhooks, ("expected_inbound_url", "expected_status_url", "inbound_configuration_matches", "status_callback_strategy")),
    }
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_signature(url: str, params, signature: str | None, auth_token: str | None) -> bool:
    """Validate form callbacks against the exact configured external URL.

    Preserve query encoding and all received form fields, including future provider
    fields. FormData/MultiDict and ordinary mappings are supported. Do not rebuild
    this URL from untrusted Forwarded/X-Forwarded-* request headers.
    """
    if not auth_token or not signature or not isinstance(url, str):
        return False
    try:
        data = url
        for name in sorted(set(params.keys())):
            if hasattr(params, "getlist"):
                values = params.getlist(name)
            elif hasattr(params, "getall"):
                values = params.getall(name)
            else:
                value = params[name]
                values = value if isinstance(value, (list, tuple)) else [value]
            for value in sorted(set(values)):
                if not isinstance(name, str) or not isinstance(value, str):
                    return False
                data += name + value
        digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
        expected = base64.b64encode(digest).decode()
        return hmac.compare_digest(expected, signature)
    except (ValueError, TypeError, AttributeError):
        return False


def _only(data: Mapping, names: tuple[str, ...]) -> dict:
    return {name: data.get(name) for name in names}


def _external_base(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderRejected("Production webhook base must be an HTTPS URL without credentials, query or fragment.")
    return value.rstrip("/")


class TwilioProvider:
    environment = "production"

    def __init__(self, environment: str = "production", credentials: Credentials | Mapping | None = None,
                 client: httpx.Client | None = None, public_url: str | None = None):
        if environment != "production":
            raise ProviderRejected("Twilio network access is available only in the production environment.")
        self.environment = environment
        if credentials is None:
            from .db import connect
            conn = connect()
            try:
                credentials = load_credentials(conn, environment)
            finally:
                conn.close()
        if isinstance(credentials, Mapping):
            credentials = Credentials(
                str(credentials.get("account_sid", "")), str(credentials.get("api_key_sid", "")),
                str(credentials.get("api_key_secret", credentials.get("secret", ""))),
                str(credentials.get("auth_token", "")), str(credentials.get("service_sid", "")),
                credentials.get("verification", {}),
            )
        self.credentials = credentials
        _validate_credentials(self.credentials)
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=False, trust_env=False,
                                            transport=httpx.HTTPTransport(retries=0))
        self._owns_client = client is None
        self.public_url = public_url if public_url is not None else _settings().public_url

    def close(self):
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _request(self, method: str, url: str, *, data=None, account_auth: bool = False) -> dict:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"api.twilio.com", "messaging.twilio.com"} or parsed.username or parsed.port:
            raise ProviderRejected("Untrusted provider resource URL was rejected.")
        creds = self.credentials
        username = creds.account_sid if account_auth or not creds.api_key_sid else creds.api_key_sid
        password = creds.auth_token if account_auth or not creds.api_key_sid else creds.secret
        try:
            response = self._client.request(method, url, data=data, auth=(username, password),
                                            timeout=REQUEST_TIMEOUT, follow_redirects=False)
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            raise ProviderRejected("Provider connection failed before message submission.") from None
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            raise ProviderUnknown("Provider response is uncertain; reconcile this attempt without resending.") from None
        except httpx.HTTPError:
            raise ProviderUnknown("Provider transport outcome is uncertain; do not resend.") from None
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            payload = None
        code = payload.get("code") if isinstance(payload, dict) else None
        code = code if isinstance(code, int) else None
        if 400 <= response.status_code < 500:
            raise ProviderRejected(f"Provider explicitly rejected request (HTTP {response.status_code}).", code)
        if not 200 <= response.status_code < 300:
            raise ProviderUnknown(f"Provider request outcome is uncertain (HTTP {response.status_code}); do not resend.", code)
        if not isinstance(payload, dict):
            raise ProviderUnknown("Provider returned an invalid success response; reconcile without resending.")
        return payload

    def _list(self, url: str, key: str) -> list[dict]:
        origin = urlsplit(url)
        prefix = origin.path
        records = []
        seen = set()
        next_url = url + "?PageSize=100"
        for _ in range(MAX_PAGES):
            if next_url in seen:
                raise ProviderRejected("Provider pagination loop prevented complete verification.")
            seen.add(next_url)
            page = self._request("GET", next_url)
            items = page.get(key)
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ProviderRejected("Provider list response did not expose the documented resource shape.")
            records.extend(items)
            if len(records) > MAX_RESOURCES:
                raise ProviderRejected("Verification resource limit reached; narrow the configured sender pool.")
            meta = page.get("meta") or {}
            if not isinstance(meta, dict):
                raise ProviderRejected("Provider pagination metadata has an unsupported shape.")
            next_url = meta.get("next_page_url") or page.get("next_page_uri")
            if not next_url:
                return records
            if isinstance(next_url, str) and next_url.startswith("/"):
                next_url = f"{origin.scheme}://{origin.netloc}" + next_url
            follow = urlsplit(next_url)
            if (follow.scheme, follow.netloc, follow.path) != (origin.scheme, origin.netloc, prefix) or follow.username or follow.fragment:
                raise ProviderRejected("Provider pagination attempted to leave the verified resource.")
        raise ProviderRejected("Provider pagination limit prevented complete verification.")

    def verify(self) -> dict:
        creds = self.credentials
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = {
            "environment": self.environment, "checked_at": stamp,
            "account_ok": False, "service_ok": False, "campaign_ok": False, "webhooks_ok": False,
            "production_eligible": False,
            "authorized_senders": [], "service_sid": creds.service_sid, "campaign_id": None,
            "account": None, "services": [], "service": None, "campaigns": [], "senders": [],
            "webhooks": {}, "checks": {}, "manual_review": [
                "Confirm every sending number's carrier A2P registration and association with the selected campaign in Twilio Console.",
                "Review registered campaign purpose, brand identity, consent flow, samples, and STOP wording against this business's actual messages.",
                "Review Advanced Opt-Out configuration and confirmations in Twilio Console; this integration does not claim an exposed verification API.",
                "Perform signed inbound and delivery callback smoke tests; configured URLs alone do not establish endpoint health.",
                "Confirm campaign-level carrier throughput and number restrictions; pool membership and SMS capability do not prove carrier eligibility.",
            ], "sources": SOURCES,
        }

        def check(name, operation):
            try:
                value = operation()
                result["checks"][name] = {"status": "verified", "checked_at": stamp}
                return value
            except ProviderError as exc:
                result["checks"][name] = {"status": "unverified", "checked_at": stamp,
                                          "reason": exc.detail, "error_code": exc.error_code}
                return None

        # Use Account auth only for the Account resource and signature token check.
        # Message/service endpoints use the supplied API key without token fallback.
        account = check("account", lambda: self._request("GET", f"{API_ROOT}/Accounts/{creds.account_sid}.json", account_auth=True))
        if account:
            result["account"] = _only(account, ("sid", "friendly_name", "status", "type", "date_updated"))
            result["account_ok"] = account.get("sid") == creds.account_sid and account.get("status") == "active" and account.get("type") == "Full"
            returned_token = account.get("auth_token")
            token_ok = not returned_token or hmac.compare_digest(str(returned_token), creds.auth_token)
            result["checks"]["signature_token"] = {"status": "verified" if token_ok else "unverified", "checked_at": stamp,
                                                      "reason": "Account authentication checked without exposing returned credentials."}
            result["account_ok"] = result["account_ok"] and token_ok
        services = check("services", lambda: self._list(f"{MESSAGING_ROOT}/Services", "services"))
        if services is not None:
            result["services"] = [_only(s, ("sid", "account_sid", "friendly_name", "date_updated")) for s in services if s.get("account_sid") == creds.account_sid]
        if not creds.service_sid:
            result["checks"]["service"] = {"status": "unverified", "reason": "Select an existing Messaging Service.", "checked_at": stamp}
            return result
        service_url = f"{MESSAGING_ROOT}/Services/{creds.service_sid}"
        service = check("service", lambda: self._request("GET", service_url))
        if service:
            result["service"] = _only(service, ("sid", "account_sid", "friendly_name", "inbound_request_url", "inbound_method", "status_callback", "use_inbound_webhook_on_number", "sticky_sender", "smart_encoding", "validity_period", "date_updated"))
            result["service_ok"] = service.get("sid") == creds.service_sid and service.get("account_sid") == creds.account_sid
        campaigns = check("campaigns", lambda: self._list(service_url + "/Compliance/Usa2p", "compliance"))
        if campaigns is not None:
            result["campaigns"] = [_only(c, ("sid", "account_sid", "messaging_service_sid", "campaign_id", "campaign_status", "brand_registration_sid", "us_app_to_person_usecase", "description", "message_samples", "message_flow", "opt_out_keywords", "opt_out_message", "subscriber_opt_in", "mock", "rate_limits", "date_updated", "errors")) for c in campaigns]
            eligible = [c for c in campaigns if c.get("account_sid") == creds.account_sid and c.get("messaging_service_sid") == creds.service_sid and c.get("campaign_status") == "VERIFIED" and c.get("mock") is False and c.get("campaign_id")]
            result["campaign_ok"] = len(eligible) == 1
            if result["campaign_ok"]:
                result["campaign_id"] = eligible[0]["campaign_id"]
        numbers = check("sender_pool", lambda: self._list(service_url + "/PhoneNumbers", "phone_numbers"))
        expected_inbound = expected_status = None
        try:
            base = _external_base(self.public_url)
            expected_inbound = base + "/webhooks/twilio/inbound"
            expected_status = base + "/webhooks/twilio/status"
        except ProviderError as exc:
            result["checks"]["public_url"] = {"status": "unverified", "reason": exc.detail, "checked_at": stamp}
        inbound_matches = []
        if numbers is not None:
            for number in numbers:
                sender = _only(number, ("sid", "phone_number", "account_sid", "service_sid", "country_code"))
                sender.update({"verified": False, "carrier_registration": "manual_review", "checked_at": stamp})
                if not _sid(number.get("sid", ""), "PN"):
                    sender["reason"] = "Invalid provider number identifier."
                    result["senders"].append(sender)
                    continue
                phone = check("sender:" + number["sid"], lambda sid=number["sid"]: self._request("GET", f"{API_ROOT}/Accounts/{creds.account_sid}/IncomingPhoneNumbers/{sid}.json"))
                if phone:
                    sender.update(_only(phone, ("capabilities", "sms_url", "sms_method", "sms_application_sid", "status")))
                    capabilities = phone.get("capabilities")
                    sms_capable = isinstance(capabilities, dict) and capabilities.get("sms") is True
                    sender["verified"] = (number.get("account_sid") == creds.account_sid and number.get("service_sid") == creds.service_sid and phone.get("account_sid") == creds.account_sid and phone.get("sid") == number.get("sid") and phone.get("phone_number") == number.get("phone_number") and sms_capable and bool(re.fullmatch(r"\+[1-9][0-9]{7,14}", str(number.get("phone_number", "")))))
                    if service and service.get("use_inbound_webhook_on_number") is True:
                        inbound_ok = phone.get("sms_url") == expected_inbound and phone.get("sms_method") == "POST" and not phone.get("sms_application_sid")
                    elif service and service.get("use_inbound_webhook_on_number") is False:
                        inbound_ok = bool(service and service.get("inbound_request_url") == expected_inbound and service.get("inbound_method") == "POST")
                    else:
                        inbound_ok = False  # Unknown routing must not imply service routing.
                    sender["inbound_configured"] = bool(expected_inbound and inbound_ok)
                    if sender["verified"]:
                        result["authorized_senders"].append(number["phone_number"])
                        inbound_matches.append(sender["inbound_configured"])
                else:
                    sender["reason"] = "Number ownership/capability request could not be verified."
                result["senders"].append(sender)
        default_status_matches = bool(expected_status and service and service.get("status_callback") == expected_status)
        # Outbound requests supply their own StatusCallback, which Twilio documents
        # as overriding the service default for that message. Do not require an
        # operator to change an existing service default used by another app.
        status_ok = bool(expected_status)
        inbound_ok = bool(inbound_matches and all(inbound_matches))
        result["webhooks"] = {"expected_inbound_url": expected_inbound, "expected_status_url": expected_status,
                              "inbound_configuration_matches": inbound_ok, "status_configuration_matches": status_ok,
                              "service_default_status_callback_matches": default_status_matches,
                              "status_callback_strategy": "per_message_override",
                              "service_default_status_callback": service.get("status_callback") if service else None,
                              "end_to_end_health": "requires_signed_smoke_test", "checked_at": stamp}
        result["webhooks_ok"] = inbound_ok and status_ok and bool(account)
        result["authorized_senders"] = sorted(set(result["authorized_senders"]))
        result["production_eligible"] = False  # Console review and health are separate gates.
        return result

    def send(self, message: Mapping[str, Any]) -> dict:
        creds = self.credentials
        service = message.get("service_sid")
        sender = message.get("sender")
        recipient = message.get("to", message.get("phone"))
        if not _sid(str(service or ""), "MG") or service != creds.service_sid:
            raise ProviderRejected("Message service does not match the connected Messaging Service.")
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", str(recipient or "")) or not re.fullmatch(r"\+[1-9][0-9]{7,14}", str(sender or "")):
            raise ProviderRejected("Verified E.164 recipient and sender are required.")
        if not message.get("body") or not isinstance(message["body"], str):
            raise ProviderRejected("The final rendered message body is required.")
        snapshot = creds.verification
        if snapshot and (snapshot.get("service_sid") != service or sender not in snapshot.get("authorized_senders", [])):
            raise ProviderRejected("The sender is not authorized by the saved service verification.")
        try:
            message_id = int(message["id"])
            if message_id <= 0:
                raise ValueError
            validity = int(message["validity_period"])
            if validity <= 1:
                raise ValueError
            expires = datetime.fromisoformat(str(message["expires_at"]).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                raise ValueError
            at = datetime.now(timezone.utc)
            local = at.astimezone(ZoneInfo(str(message["timezone"])))
            policy = message.get("policy", message.get("policy_settings", {})) or {}
            end_hour = int(policy.get("window_end", 18))
            start_hour = int(policy.get("window_start", 9))
            if not 0 <= start_hour < end_hour <= 24 or not start_hour <= local.hour < end_hour:
                raise ValueError
            if end_hour == 24:
                from datetime import timedelta
                end = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)
            else:
                end = local.replace(hour=end_hour, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            validity = min(validity, 36000, int(policy.get("max_provider_validity_seconds", 300)),
                           math.floor((expires - at).total_seconds()), math.floor((end - at).total_seconds()))
            if validity <= 1:
                raise ValueError
        except (KeyError, ValueError, TypeError, OverflowError):
            raise ProviderRejected("Message expiration, timezone or provider queue validity is missing or outside the sending window.") from None
        base = _external_base(self.public_url)
        callback = base + "/webhooks/twilio/status?" + urlencode({"message_id": message_id})
        payload = {"To": recipient, "From": sender, "MessagingServiceSid": service, "Body": message["body"],
                   "StatusCallback": callback, "ValidityPeriod": str(validity), "SmartEncoded": "false"}
        data = self._request("POST", f"{API_ROOT}/Accounts/{creds.account_sid}/Messages.json", data=payload)
        if (not _sid(data.get("sid", ""), "SM|MM") or data.get("account_sid") != creds.account_sid
                or data.get("to") != recipient or data.get("messaging_service_sid") != service
                or data.get("from") not in {sender, None}
                or data.get("status") not in {"accepted", "queued", "sending", "sent", "delivered", "failed", "undelivered"}):
            raise ProviderUnknown("Provider success response could not be bound to this message; reconcile without resending.")
        # A queued resource is provider acceptance, not evidence of handset delivery.
        return _only(data, ("sid", "status", "from", "to", "messaging_service_sid", "price", "price_unit", "error_code", "num_segments"))

    def fetch_message(self, sid: str) -> dict:
        if not _sid(sid, "SM|MM"):
            raise ProviderRejected("A valid Twilio message SID is required for reconciliation.")
        data = self._request("GET", f"{API_ROOT}/Accounts/{self.credentials.account_sid}/Messages/{sid}.json")
        if data.get("sid") != sid or data.get("account_sid") != self.credentials.account_sid:
            raise ProviderRejected("Reconciliation response did not match the connected account and message.")
        return _only(data, ("sid", "account_sid", "status", "from", "to", "body", "messaging_service_sid", "price", "price_unit", "error_code", "num_segments", "date_created", "date_sent", "date_updated"))


class SimulationProvider:
    """No credentials, sockets, database changes, or real delivery claims."""

    environment = "simulation"

    def verify(self) -> dict:
        return {"environment": "simulation", "simulated": True, "account_ok": False, "service_ok": False,
                "campaign_ok": False, "webhooks_ok": False, "authorized_senders": [], "production_eligible": False,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def send(self, message: Mapping[str, Any]) -> dict:
        return {"sid": "SIM" + uuid.uuid4().hex, "status": "accepted", "from": message.get("sender"),
                "to": message.get("to", message.get("phone")), "price": "0", "price_unit": "USD", "simulated": True}

    def fetch_message(self, sid: str) -> dict:
        raise ProviderRejected("Simulation has no external message history; outcomes remain synthetic.")
