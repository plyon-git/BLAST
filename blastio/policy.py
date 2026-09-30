"""One fail-closed policy gate shared by all outbound paths.

Schedules, frequency and incident thresholds are reviewable product safeguards;
they are not assertions about universally safe carrier or legal limits.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings
from .db import get_setting

DEFAULT_POLICY = {
    "window_start": 9, "window_end": 18, "max_recipient_24h": 1,
    "max_recipient_7d": 3, "max_account_daily": 100,
    "max_campaign_daily": 100, "max_per_minute": 1,
    "max_daily_cost": 10.0, "max_campaign_cost": 10.0,
    "max_segments": 3, "max_queue_age_seconds": 3600,
    "max_provider_validity_seconds": 300, "health_min_sample": 20,
    "max_filter_rate": 0.05, "max_optout_rate": 0.05,
}
MERGE_FIELDS = {"first_name", "last_name", "street_address", "city", "state", "zip", "property_id", "business_name"}
MERGE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z_0-9]*)\s*\}\}")
GSM_BASIC = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM_EXT = set("\f^{}\\[~]|€")
ATTEMPTED_STATES = ("dispatching", "unknown", "accepted", "sent", "delivered", "failed", "filtered")
TRUSTED_TIMEZONE_SOURCES = {"recipient", "recipient_confirmed", "verified", "manual_verified", "documented", "synthetic"}


def asdict(row):
    return dict(row) if row is not None else {}


def timestamp(value):
    if isinstance(value, datetime):
        dt = value
    elif value:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp is required")
    if dt.tzinfo is None:
        raise ValueError("timestamp must include its timezone")
    return dt.astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def policy_settings(conn):
    policy = dict(DEFAULT_POLICY)
    value = get_setting(conn, "policy", {})
    if not isinstance(value, dict):
        raise ValueError("Policy configuration is invalid")
    policy.update(value)
    for name, value in policy.items():
        if name in DEFAULT_POLICY and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError(f"Invalid policy limit: {name}")
        if name in DEFAULT_POLICY and name not in {"max_daily_cost", "max_campaign_cost", "max_filter_rate", "max_optout_rate"} and int(value) != value:
            raise ValueError(f"Policy setting requires a whole number: {name}")
    if not 0 <= policy["window_start"] < policy["window_end"] <= 24:
        raise ValueError("Sending schedule must be a same-day window")
    if policy["max_provider_validity_seconds"] > 300:
        raise ValueError("Provider queue validity must be at most 300 seconds")
    return policy


def render(body, contact, property=None, business_name=None):
    """Literal, allow-listed substitution; imported values are never executable."""
    values = asdict(contact)
    values.update({k: v for k, v in asdict(property).items() if k in MERGE_FIELDS})
    values["business_name"] = business_name or settings.business_name
    values["first_name"] = values.get("first_name") or "there"
    values["last_name"] = values.get("last_name") or ""
    if not isinstance(body, str) or not body.strip() or len(body) > 5000:
        raise ValueError("Message body is missing or too long")

    def replace(match):
        name = match.group(1)
        if name not in MERGE_FIELDS:
            raise ValueError(f"Unknown merge field: {name}")
        value = values.get(name)
        if value is None or (name not in {"first_name", "last_name"} and str(value).strip() == ""):
            raise ValueError(f"Missing merge field: {name}")
        value = str(value).strip()
        if len(value) > 300 or any(ord(c) < 32 or ord(c) == 127 for c in value) or "{{" in value or "}}" in value:
            raise ValueError(f"Unsafe merge value: {name}")
        return value

    rendered = MERGE.sub(replace, body)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("Malformed or unresolved merge field")
    if any(ord(c) < 32 and c not in "\r\n\t" for c in rendered):
        raise ValueError("Message contains unsupported control characters")
    return rendered


def sms_metrics(body):
    gsm = all(c in GSM_BASIC or c in GSM_EXT for c in body)
    units = sum(2 if c in GSM_EXT else 1 for c in body) if gsm else len(body.encode("utf-16-be")) // 2
    single, multipart = (160, 153) if gsm else (70, 67)
    segments = 0 if not units else 1 if units <= single else math.ceil(units / multipart)
    return {"encoding": "GSM-7" if gsm else "UCS-2", "characters": len(body), "units": units, "segments": segments,
            "estimated_cost": round(segments * settings.segment_cost, 6)}


def eligibility(conn, contact_id, purpose="seller_outreach", allow_synthetic=True):
    contact = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not contact:
        return ["Contact no longer exists"]
    reasons = []
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", contact["phone"] or ""):
        reasons.append("Recipient phone is not normalized E.164")
    try:
        if contact["timezone_source"] not in TRUSTED_TIMEZONE_SOURCES:
            raise ValueError("unconfirmed")
        ZoneInfo(contact["timezone"])
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        reasons.append("Recipient timezone must be confirmed; property and area code do not prove timezone")
    suppression = conn.execute("SELECT * FROM suppressions WHERE phone=? AND active=1", (contact["phone"],)).fetchone()
    if suppression:
        reasons.append(f"Suppressed: {suppression['reason']}")
    records = conn.execute("SELECT * FROM consents WHERE contact_id=? ORDER BY occurred_at DESC,id DESC", (contact_id,)).fetchall()
    latest_suppression = conn.execute("SELECT created_at FROM suppression_events WHERE phone=? AND action='suppress' ORDER BY id DESC LIMIT 1", (contact["phone"],)).fetchone()
    valid = False
    now_dt = datetime.now(timezone.utc)
    for row in records:
        c = dict(row)
        if c["business"].casefold().strip() != settings.business_name.casefold().strip() or c["channel"].lower() != "sms" or c["purpose"] != purpose:
            continue
        if c["status"] == "revoked":
            break
        if c["status"] != "verified":
            continue
        if not allow_synthetic and c["evidence_ref"].startswith("simulation://"):
            continue
        if not all(c.get(k) for k in ("source", "occurred_at", "disclosure_version", "evidence_ref", "verified_by", "verified_at")):
            continue
        try:
            occurred = timestamp(c["occurred_at"])
            if occurred > now_dt or timestamp(c["verified_at"]) < occurred:
                continue
            if latest_suppression and occurred <= timestamp(latest_suppression["created_at"]):
                continue
        except (TypeError, ValueError):
            continue
        valid = True
        break
    if not valid:
        reasons.append("Verified consent with complete evidence for this business and purpose is required")
    return reasons


def _recent_count(conn, field, value, since, message_id, mode):
    placeholders = ",".join("?" for _ in ATTEMPTED_STATES)
    return conn.execute(f"SELECT count(*) FROM messages WHERE {field}=? AND mode=? AND id<>? AND state IN ({placeholders}) AND COALESCE(accepted_at,claimed_at,created_at)>=?",
                        (value, mode, message_id, *ATTEMPTED_STATES, iso(since))).fetchone()[0]


def gate(conn, message, at=None):
    """Return concrete blocking reasons. Any unexpected failure is fail-closed."""
    m = asdict(message)
    at = timestamp(at) if at is not None else datetime.now(timezone.utc)
    reasons = []
    try:
        p = policy_settings(conn)
        if get_setting(conn, "global_pause", False):
            reasons.append("Global kill switch is active")
        campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (m.get("campaign_id"),)).fetchone()
        if not campaign:
            return ["Campaign no longer exists"]
        c = dict(campaign)
        if c["status"] not in {"approved", "scheduled"} or not c.get("approved_by") or not c.get("approved_at"):
            reasons.append("Campaign is not approved for dispatch")
        if c.get("pause_reason"):
            reasons.append("Campaign is paused: " + c["pause_reason"])
        if m.get("mode") not in {"simulation", "production"} or c["mode"] != m.get("mode"):
            reasons.append("Campaign and message environments do not match")
        if m.get("state") not in {"queued", "dispatching"}:
            reasons.append("Message is not in a dispatchable state")
        if m.get("scheduled_at") and timestamp(m["scheduled_at"]) > at:
            reasons.append("Message scheduled time has not arrived")
        if m.get("expires_at") and timestamp(m["expires_at"]) <= at:
            reasons.append("Message has expired")
        reasons.extend(eligibility(conn, m.get("contact_id"), settings.purpose, allow_synthetic=m.get("mode") != "production"))
        contact_row = conn.execute("SELECT * FROM contacts WHERE id=?", (m.get("contact_id"),)).fetchone()
        if not contact_row:
            return list(dict.fromkeys(reasons))
        contact = dict(contact_row)
        if contact["reply_hold"] and m.get("kind") != "manual":
            reasons.append("Recipient replied; automated outreach is held for inbox review")
        try:
            if contact.get("timezone_source") not in TRUSTED_TIMEZONE_SOURCES:
                raise ValueError("unconfirmed")
            zone = ZoneInfo(contact["timezone"])
            local = at.astimezone(zone)
            hour = local.hour + local.minute / 60 + local.second / 3600
            if not p["window_start"] <= hour < p["window_end"]:
                reasons.append("Outside the confirmed recipient-local sending window")
            if p["window_end"] - hour <= 1 / 3600:
                reasons.append("Recipient-local sending window is closing")
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            reasons.append("Recipient timezone must be confirmed; property and area code do not prove timezone")
        template = conn.execute("SELECT * FROM templates WHERE id=?", (m.get("template_id"),)).fetchone()
        allowed_templates = [c["template_id"], *json.loads(c.get("variant_ids") or "[]")]
        if m.get("template_id") not in allowed_templates:
            reasons.append("Message template is not in the approved campaign variant library")
        if not template or template["status"] != "approved" or not template["approved_by"] or not template["approved_at"]:
            reasons.append("Template version requires approval")
        if template:
            prop = conn.execute("SELECT * FROM properties WHERE id=? AND contact_id=?", (m.get("property_id"), m.get("contact_id"))).fetchone() if m.get("property_id") else None
            try:
                exact = render(template["body"], contact, prop)
                if exact != m.get("body"):
                    reasons.append("Persisted body no longer matches the approved template and record")
            except ValueError as exc:
                reasons.append(str(exc))
        body = m.get("body") or ""
        if settings.business_name.casefold() not in body.casefold():
            reasons.append("Message must clearly identify the business")
        lines = get_setting(conn, "approved_optout_lines", ["Reply STOP to unsubscribe"])
        if not isinstance(lines, list) or not any(isinstance(line, str) and re.search(r"\bSTOP\b", line, re.I) and line.casefold() in body.casefold() for line in lines):
            reasons.append("Reviewed STOP unsubscribe instruction is required")
        metrics = sms_metrics(body)
        if not math.isfinite(settings.segment_cost) or settings.segment_cost <= 0:
            raise ValueError("Configured segment estimate must be a positive finite number")
        if not body or metrics["segments"] > p["max_segments"]:
            reasons.append("Message is empty or exceeds the configured segment limit")
        midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
        for hours, key in ((24, "max_recipient_24h"), (168, "max_recipient_7d")):
            if _recent_count(conn, "contact_id", m["contact_id"], at - timedelta(hours=hours), m.get("id", -1), m.get("mode")) >= p[key]:
                reasons.append(f"Recipient frequency limit reached ({hours} hours)")
        if _recent_count(conn, "campaign_id", m["campaign_id"], midnight, m.get("id", -1), m.get("mode")) >= p["max_campaign_daily"]:
            reasons.append("Campaign daily volume limit reached")
        placeholders = ",".join("?" for _ in ATTEMPTED_STATES)
        aggregate = conn.execute(f"SELECT count(*) AS n,COALESCE(sum(COALESCE(actual_cost,estimated_cost)),0) AS cost FROM messages WHERE mode=? AND id<>? AND state IN ({placeholders}) AND COALESCE(accepted_at,claimed_at,created_at)>=?", (m.get("mode"), m.get("id", -1), *ATTEMPTED_STATES, iso(midnight))).fetchone()
        if aggregate["n"] >= p["max_account_daily"]:
            reasons.append("Account daily volume limit reached")
        if aggregate["cost"] + metrics["estimated_cost"] > p["max_daily_cost"]:
            reasons.append("Account daily spending limit reached")
        cost = conn.execute(f"SELECT COALESCE(sum(COALESCE(actual_cost,estimated_cost)),0) FROM messages WHERE campaign_id=? AND mode=? AND id<>? AND state IN ({placeholders})", (m["campaign_id"], m.get("mode"), m.get("id", -1), *ATTEMPTED_STATES)).fetchone()[0]
        if cost + metrics["estimated_cost"] > p["max_campaign_cost"]:
            reasons.append("Campaign spending limit reached")
        minute_count = conn.execute(f"SELECT count(*) FROM messages WHERE mode=? AND id<>? AND state IN ({placeholders}) AND COALESCE(accepted_at,claimed_at,created_at)>=?", (m.get("mode"), m.get("id", -1), *ATTEMPTED_STATES, iso(at - timedelta(seconds=60)))).fetchone()[0]
        if minute_count >= p["max_per_minute"]:
            reasons.append("Account per-minute throughput limit reached")
        if m.get("sender") != c.get("sender") or m.get("service_sid") != c.get("service_sid"):
            reasons.append("Message sender/service differs from approved campaign routing")
        if m.get("mode") == "production":
            prior = conn.execute("SELECT sender FROM messages WHERE contact_id=? AND mode='production' AND id<>? AND (accepted_at IS NOT NULL OR provider_sid IS NOT NULL OR (claimed_at IS NOT NULL AND state IN('unknown','accepted','sent','delivered','failed','filtered'))) ORDER BY id LIMIT 1", (m["contact_id"], m.get("id", -1))).fetchone()
            if prior and prior["sender"] != m.get("sender"):
                reasons.append("Keep the existing conversation sender; a new number cannot bypass recipient or provider restrictions")
            if contact.get("timezone_source") == "synthetic":
                reasons.append("Synthetic recipient timezone evidence cannot authorize production")
            if settings.mode != "production" or not settings.allow_production:
                reasons.append("Production is not enabled in the deployment")
            cred = conn.execute("SELECT * FROM credentials WHERE environment='production'").fetchone()
            if not cred:
                reasons.append("Production Twilio connection is missing")
            else:
                cred = dict(cred)
                verification = json.loads(cred.get("verification") or "{}")
                if not cred.get("verified_at") or (at - timestamp(cred["verified_at"])).total_seconds() > settings.verification_ttl:
                    reasons.append("Twilio connection verification is stale")
                for name in ("account_ok", "service_ok", "campaign_ok", "webhooks_ok"):
                    if verification.get(name) is not True:
                        reasons.append("Twilio " + name.removesuffix("_ok") + " verification is incomplete")
                if not m.get("service_sid") or verification.get("service_sid") != m.get("service_sid"):
                    reasons.append("Messaging Service is not the verified service")
                if m.get("sender") not in verification.get("authorized_senders", []):
                    reasons.append("Sender is not verified and authorized for this service")
            review = get_setting(conn, "readiness", {})
            if not isinstance(review, dict) or not all(review.get(k) is True for k in ("advanced_optout_reviewed", "sender_associations_reviewed", "campaign_content_reviewed", "smoke_test_reviewed")) or not review.get("reviewed_by") or not review.get("reviewed_at"):
                reasons.append("Production manual setup and authorized smoke-test review is incomplete")
            for health_key in ("inbound_health_at", "suppression_health_at", "callback_health_at"):
                checked = get_setting(conn, health_key, None)
                if not checked or (at - timestamp(checked)).total_seconds() > settings.health_ttl:
                    reasons.append(health_key.removesuffix("_at").replace("_", " ") + " is stale or unavailable")
        return list(dict.fromkeys(reasons))
    except Exception:
        # A broken suppression store, invalid configuration or corrupt evidence is
        # never treated as permission to send. Detailed failures go to diagnostics.
        return list(dict.fromkeys(reasons + ["Essential policy safeguards are unavailable; operator review is required"]))
