"""Durable send engine, authoritative suppression and deterministic inbox events.

One local SQLite file, one worker. A durable dispatching intent commits before
provider I/O. A short BEGIN IMMEDIATE then serializes the final gate and bounded
provider submission against suppression. A process crash leaves unknown intent,
never a silently retried request. Accepted provider messages cannot be recalled.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import settings
from .db import audit, connect, get_setting, now, set_setting, transaction
from .policy import asdict, eligibility, gate, iso, policy_settings, render, sms_metrics, timestamp

OPTOUT_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
OPTIN_WORDS = {"START", "UNSTOP"}
STATUS_MAP = {"accepted": "accepted", "scheduled": "accepted", "queued": "accepted", "sending": "sent", "sent": "sent", "delivered": "delivered", "undelivered": "failed", "failed": "failed", "canceled": "canceled"}
RANK = {"queued": 0, "dispatching": 1, "unknown": 1, "accepted": 2, "sent": 3, "delivered": 4, "failed": 4, "filtered": 4, "canceled": 4, "expired": 4, "blocked": 4}
TERMINAL = {"delivered", "failed", "filtered", "canceled", "expired", "blocked"}
RESTRICTION_ERRORS = {"20003", "20403", "20429", "30004", "30034", "30035", "30036", "30037", "30038", "21606", "21608"}
TRANSIENT_REASONS = ("Outside the confirmed recipient-local", "Recipient-local sending window is closing", "Recipient frequency limit", "Campaign daily volume limit", "Account daily volume limit", "Account daily spending limit", "Account per-minute throughput limit", "Message scheduled time")


def _transient(reasons):
    return bool(reasons) and all(r.startswith(TRANSIENT_REASONS) for r in reasons)


def _enqueue_wait_reason(conn, message, reason):
    if reason == "Global kill switch is active" or reason.startswith(TRANSIENT_REASONS):
        return True
    # Readiness must exist now; scheduling a future send does not certify that
    # today's probes/verification will remain fresh at that future instant.
    # Keep it queued and recheck actual freshness immediately before submission.
    current = datetime.now(timezone.utc)
    for key in ("inbound_health_at", "callback_health_at", "suppression_health_at"):
        label = key.removesuffix("_at").replace("_", " ") + " is stale or unavailable"
        if reason == label:
            checked = get_setting(conn, key, None)
            return bool(checked and 0 <= (current - timestamp(checked)).total_seconds() <= settings.health_ttl)
    if reason == "Twilio connection verification is stale":
        cred = conn.execute("SELECT verified_at FROM credentials WHERE environment='production'").fetchone()
        return bool(cred and cred["verified_at"] and 0 <= (current - timestamp(cred["verified_at"])).total_seconds() <= settings.verification_ttl)
    return False


def _event(conn, key, kind, payload):
    cursor = conn.execute("INSERT OR IGNORE INTO webhook_events(event_key,kind,payload,created_at) VALUES(?,?,?,?)", (key, kind, json.dumps(payload), now()))
    return cursor.rowcount == 1


def suppress(conn, phone, reason, actor="system"):
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone or ""):
        raise ValueError("Suppression requires a normalized E.164 phone")
    stamp = now()
    conn.execute("INSERT INTO suppressions(phone,reason,active,created_at,updated_at) VALUES(?,?,1,?,?) ON CONFLICT(phone) DO UPDATE SET reason=excluded.reason,active=1,updated_at=excluded.updated_at", (phone, reason, stamp, stamp))
    conn.execute("INSERT INTO suppression_events(phone,action,reason,actor,created_at) VALUES(?,'suppress',?,?,?)", (phone, reason, str(actor), stamp))
    contact = conn.execute("SELECT id FROM contacts WHERE phone=?", (phone,)).fetchone()
    canceled = 0
    if contact:
        conn.execute("UPDATE consents SET status='revoked' WHERE contact_id=? AND status<>'revoked'", (contact["id"],))
        conn.execute("UPDATE contacts SET reply_hold=1,updated_at=? WHERE id=?", (stamp, contact["id"]))
        canceled = conn.execute("UPDATE messages SET state='canceled',reason=?,updated_at=? WHERE contact_id=? AND state IN('queued','blocked','dispatching') AND provider_sid IS NULL", (reason, stamp, contact["id"])).rowcount
        conn.execute("UPDATE attempts SET state='canceled',detail=?,updated_at=? WHERE state='dispatching' AND message_id IN(SELECT id FROM messages WHERE contact_id=? AND state='canceled')", (reason, stamp, contact["id"]))
    audit(conn, actor, "suppress", "phone", phone, {"reason": reason, "canceled": canceled, "provider_accepted_messages_cannot_be_recalled": True})
    return {"phone": phone, "suppressed": True, "canceled": canceled}


def restore_consent(conn, consent_id, actor):
    row = conn.execute("SELECT c.*,t.phone FROM consents c JOIN contacts t ON t.id=c.contact_id WHERE c.id=?", (consent_id,)).fetchone()
    if not row:
        raise ValueError("Consent record does not exist")
    c = dict(row)
    if not all(c.get(k) for k in ("business", "channel", "purpose", "source", "occurred_at", "disclosure_version", "evidence_ref")):
        raise ValueError("Complete documented consent evidence is required")
    if c["business"].casefold().strip() != settings.business_name.casefold().strip() or c["channel"].lower() != "sms" or c["purpose"] != settings.purpose:
        raise ValueError("Consent must cover this business, SMS and this messaging purpose")
    if c["status"] == "revoked":
        raise ValueError("Create a new renewed-consent evidence record; revoked history is immutable")
    occurred = timestamp(c["occurred_at"])
    if occurred > datetime.now(timezone.utc):
        raise ValueError("Consent evidence cannot be dated in the future")
    latest = conn.execute("SELECT created_at FROM suppression_events WHERE phone=? AND action='suppress' ORDER BY id DESC LIMIT 1", (c["phone"],)).fetchone()
    if latest and occurred <= timestamp(latest["created_at"]):
        raise ValueError("Renewed consent evidence must postdate the most recent suppression")
    stamp = now()
    conn.execute("UPDATE consents SET status='verified',verified_by=?,verified_at=? WHERE id=?", (str(actor), stamp, consent_id))
    if latest:
        conn.execute("UPDATE suppressions SET active=0,updated_at=? WHERE phone=?", (stamp, c["phone"]))
        conn.execute("INSERT INTO suppression_events(phone,action,reason,actor,created_at) VALUES(?,'restore',?,?,?)", (c["phone"], f"Renewed documented consent {consent_id}", str(actor), stamp))
    # Existing sequences remain held. Renewed consent does not revive old messages.
    audit(conn, actor, "verify_renewed_consent", "consent", consent_id, {"phone": c["phone"], "suppression_cleared": bool(latest)})
    return {"verified": True, "consent_id": consent_id, "suppression_cleared": bool(latest)}


def enqueue_campaign(conn, campaign_id, actor, kind="automated"):
    if kind not in {"automated", "manual"}:
        raise ValueError("Unsupported message kind")
    campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not campaign:
        raise ValueError("Campaign does not exist")
    campaign = dict(campaign)
    templates = [campaign["template_id"], *json.loads(campaign["variant_ids"] or "[]")]
    p = policy_settings(conn)
    scheduled = timestamp(campaign["scheduled_at"]) if campaign["scheduled_at"] else datetime.now(timezone.utc)
    expires = scheduled + timedelta(seconds=p["max_queue_age_seconds"])
    counts = {"queued": 0, "blocked": 0, "duplicates": 0, "reasons": {}}
    members = conn.execute("SELECT cc.*,c.phone FROM campaign_contacts cc JOIN contacts c ON c.id=cc.contact_id WHERE campaign_id=? ORDER BY c.id", (campaign_id,)).fetchall()
    for member in members:
        # Stable assignment, never unreviewed generation/randomization at send time.
        template_id = templates[member["contact_id"] % len(templates)]
        template = conn.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
        contact = conn.execute("SELECT * FROM contacts WHERE id=?", (member["contact_id"],)).fetchone()
        prop = conn.execute("SELECT * FROM properties WHERE id=? AND contact_id=?", (member["property_id"], member["contact_id"])).fetchone() if member["property_id"] else None
        error = None
        try:
            body = render(template["body"], contact, prop) if template else ""
        except ValueError as exc:
            body, error = "", str(exc)
        metrics = sms_metrics(body)
        stamp = now()
        cursor = conn.execute("INSERT OR IGNORE INTO messages(campaign_id,contact_id,property_id,template_id,kind,mode,state,body,sender,service_sid,scheduled_at,expires_at,segments,estimated_cost,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?)",
                              (campaign_id, member["contact_id"], member["property_id"], template_id, kind, campaign["mode"], body, campaign["sender"], campaign["service_sid"], iso(scheduled), iso(expires), metrics["segments"], metrics["estimated_cost"], stamp, stamp))
        if cursor.rowcount != 1:
            counts["duplicates"] += 1
            continue
        message_id = cursor.lastrowid
        message = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        reasons = gate(conn, message, at=scheduled)
        if error:
            reasons.insert(0, error)
        must_block = [reason for reason in reasons if not _enqueue_wait_reason(conn, message, reason)]
        if must_block:
            counts["blocked"] += 1
            for reason in reasons:
                counts["reasons"][reason] = counts["reasons"].get(reason, 0) + 1
            conn.execute("UPDATE messages SET state='blocked',reason=? WHERE id=?", ("; ".join(dict.fromkeys(reasons)), message_id))
        else:
            counts["queued"] += 1
            if reasons:
                conn.execute("UPDATE messages SET reason=? WHERE id=?", ("Awaiting operational policy recheck: " + "; ".join(reasons), message_id))
    audit(conn, actor, "enqueue_campaign", "campaign", campaign_id, counts)
    return counts


def pause_campaign(conn, campaign_id, reason, actor="worker"):
    changed = conn.execute("UPDATE campaigns SET status='paused',pause_reason=? WHERE id=? AND status<>'paused'", (reason, campaign_id)).rowcount
    if changed:
        audit(conn, actor, "pause_campaign", "campaign", campaign_id, {"reason": reason})


def _restriction_pause(conn, campaign_id, code):
    reason = "Provider restriction requires review: " + code
    pause_campaign(conn, campaign_id, reason)
    if code in {"20003", "20403", "20429"}:
        set_setting(conn, "global_pause", True)
        set_setting(conn, "pause_reason", reason)
        audit(conn, "worker", "global_pause_provider_restriction", "business", settings.business_name, {"error_code": code})


def apply_health_controls(conn):
    p = policy_settings(conn)
    since = iso(datetime.now(timezone.utc) - timedelta(hours=24))
    for campaign in conn.execute("SELECT id,mode FROM campaigns WHERE status IN('approved','scheduled')").fetchall():
        if campaign["mode"] == "production":
            stale_before = iso(datetime.now(timezone.utc) - timedelta(seconds=settings.health_ttl))
            unresolved = conn.execute("SELECT count(*) FROM messages WHERE campaign_id=? AND state IN('accepted','sent') AND COALESCE(accepted_at,claimed_at,created_at)<=?", (campaign["id"], stale_before)).fetchone()[0]
            if unresolved:
                pause_campaign(conn, campaign["id"], "Provider delivery callbacks are overdue; review actual Twilio callback path and unresolved messages")
                continue
        n, filtered = conn.execute("SELECT count(*),COALESCE(sum(state='filtered'),0) FROM messages WHERE campaign_id=? AND state IN('accepted','sent','delivered','failed','filtered') AND COALESCE(accepted_at,created_at)>=?", (campaign["id"], since)).fetchone()
        optouts = conn.execute("SELECT count(DISTINCT i.from_phone) FROM inbound i JOIN contacts c ON c.id=i.contact_id JOIN campaign_contacts cc ON cc.contact_id=c.id WHERE cc.campaign_id=? AND i.classification IN('opt_out','complaint') AND i.created_at>=?", (campaign["id"], since)).fetchone()[0]
        if n >= p["health_min_sample"] and filtered / max(n, 1) >= p["max_filter_rate"]:
            pause_campaign(conn, campaign["id"], "Configured filtering incident threshold exceeded")
        elif n >= p["health_min_sample"] and optouts / max(n, 1) >= p["max_optout_rate"]:
            pause_campaign(conn, campaign["id"], "Configured opt-out incident threshold exceeded")


def _mark_blocked(conn, message, reasons):
    reason = "; ".join(reasons)
    conn.execute("UPDATE messages SET state='blocked',reason=?,updated_at=? WHERE id=?", (reason, now(), message["id"]))
    conn.execute("UPDATE attempts SET state='blocked',detail=?,updated_at=? WHERE message_id=? AND state='dispatching'", (reason, now(), message["id"]))
    if any("health" in r or "safeguards" in r for r in reasons):
        pause_campaign(conn, message["campaign_id"], reason)
    audit(conn, "worker", "dispatch_blocked", "message", message["id"], {"reasons": reasons})


def dispatch_one(provider=None):
    """Dispatch at most one message. A timeout/unknown outcome is never retried."""
    from .provider import ProviderRejected, SimulationProvider, TwilioProvider
    with transaction() as conn:
        apply_health_controls(conn)
        stamp = now()
        expired = conn.execute("UPDATE messages SET state='expired',reason='Durable queue validity expired',updated_at=? WHERE state='queued' AND expires_at<=?", (stamp, stamp)).rowcount
        if expired:
            audit(conn, "worker", "expire_queue", "queue", "all", {"count": expired})
        if get_setting(conn, "global_pause", False):
            return False
        row = conn.execute("SELECT m.* FROM messages m JOIN campaigns c ON c.id=m.campaign_id WHERE m.state='queued' AND m.scheduled_at<=? AND c.status IN('approved','scheduled') AND COALESCE(c.pause_reason,'')='' ORDER BY m.scheduled_at,m.id LIMIT 1", (stamp,)).fetchone()
        if not row:
            return False
        reasons = gate(conn, row)
        if reasons:
            if _transient(reasons):
                due = iso(datetime.now(timezone.utc) + timedelta(seconds=60))
                conn.execute("UPDATE messages SET scheduled_at=?,reason=?,updated_at=? WHERE id=?", (due, "Awaiting policy window/capacity: " + "; ".join(reasons), now(), row["id"]))
                audit(conn, "worker", "defer_without_submission", "message", row["id"], {"reasons": reasons, "next_check": due, "expiry_unchanged": True})
                return True
            _mark_blocked(conn, row, reasons)
            return True
        conn.execute("UPDATE messages SET state='dispatching',claimed_at=?,updated_at=? WHERE id=? AND state='queued'", (stamp, stamp, row["id"]))
        attempt = conn.execute("INSERT INTO attempts(message_id,state,created_at,updated_at) VALUES(?,'dispatching',?,?)", (row["id"], stamp, stamp)).lastrowid
        message_id = row["id"]
        audit(conn, "worker", "dispatch_intent", "message", message_id, {"attempt": attempt, "mode": row["mode"]})
    # Deliberate commit boundary: a crash from here onward recovers as unknown.
    with transaction() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if row["state"] != "dispatching":
            return True  # STOP/cancellation won the lock between intent and send.
        reasons = gate(conn, row)
        if reasons:
            if any("kill switch" in r or "Campaign is paused" in r or "Campaign is not approved" in r for r in reasons):
                detail = "Paused before provider submission; durable queue remains held"
                conn.execute("UPDATE messages SET state='queued',claimed_at=NULL,reason=?,updated_at=? WHERE id=?", (detail, now(), message_id))
                conn.execute("UPDATE attempts SET state='canceled',detail=?,updated_at=? WHERE id=?", (detail, now(), attempt))
                audit(conn, "worker", "pause_before_submission", "message", message_id, {"provider_submission": False})
                return True
            _mark_blocked(conn, row, reasons)
            return True
        message = dict(row)
        contact = conn.execute("SELECT * FROM contacts WHERE id=?", (message["contact_id"],)).fetchone()
        message.update(phone=contact["phone"], to=contact["phone"], timezone=contact["timezone"])
        p = policy_settings(conn)
        local = datetime.now(timezone.utc).astimezone(ZoneInfo(contact["timezone"]))
        end = local.replace(hour=int(p["window_end"]) % 24, minute=0, second=0, microsecond=0)
        if p["window_end"] == 24:
            end += timedelta(days=1)
        remaining = min((timestamp(message["expires_at"]) - datetime.now(timezone.utc)).total_seconds(), (end - local).total_seconds(), p["max_provider_validity_seconds"])
        if remaining < 1:
            _mark_blocked(conn, row, ["Sending window or queue validity has ended"])
            return True
        message.update(validity_period=max(1, int(remaining)), policy=p, status_callback=f"{settings.public_url}/webhooks/twilio/status?message_id={message_id}")
        active_provider = provider
        owns_provider = provider is None
        try:
            if active_provider is None:
                active_provider = SimulationProvider() if message["mode"] == "simulation" else TwilioProvider(environment="production")
            if getattr(active_provider, "environment", message["mode"]) != message["mode"]:
                raise ProviderRejected("Provider environment differs from approved message")
            result = active_provider.send(message)
            if not isinstance(result, dict) or not result.get("sid"):
                raise RuntimeError("Provider response did not identify the accepted message")
            sid = str(result["sid"])
            # The response reports acceptance, not delivery. Later status callbacks
            # and read-only reconciliation advance the durable state.
            actual = _price(result.get("price"))
            conn.execute("UPDATE messages SET state='accepted',provider_sid=?,accepted_at=?,sender=COALESCE(?,sender),actual_cost=?,reason=NULL,updated_at=? WHERE id=?", (sid, now(), result.get("from"), actual, now(), message_id))
            conn.execute("UPDATE attempts SET state='accepted',provider_sid=?,updated_at=? WHERE id=?", (sid, now(), attempt))
            audit(conn, "worker", "provider_accepted", "message", message_id, {"sid": sid, "mode": message["mode"]})
            reported_status = result.get("status")
            if reported_status in STATUS_MAP:
                process_status(conn, {"MessageSid": sid, "MessageStatus": reported_status, "ErrorCode": result.get("error_code") or "", "Price": result.get("price")}, message_id, update_health=False)
        except ProviderRejected as exc:
            code = str(getattr(exc, "error_code", "") or "")
            state = "filtered" if code == "30007" else "failed"
            detail = getattr(exc, "detail", None) or "Provider rejected the message; review diagnostics"
            conn.execute("UPDATE messages SET state=?,reason=?,updated_at=? WHERE id=?", (state, detail, now(), message_id))
            conn.execute("UPDATE attempts SET state=?,error_code=?,detail=?,updated_at=? WHERE id=?", (state, code, detail, now(), attempt))
            if code == "21610":
                suppress(conn, contact["phone"], "Provider reports recipient opt-out")
            if code in RESTRICTION_ERRORS:
                _restriction_pause(conn, message["campaign_id"], code)
            elif not code:
                pause_campaign(conn, message["campaign_id"], "Provider setup or pre-submission connectivity failed; review before continuing")
            audit(conn, "worker", "provider_rejected", "message", message_id, {"error_code": code, "state": state})
        except Exception as exc:
            # Do not log raw exception strings: provider/HTTP errors may contain
            # URLs or secrets. An arbitrary exception after dispatch is uncertain.
            sid = getattr(exc, "provider_sid", None)
            detail = "Provider acceptance is unknown; reconcile without retrying"
            conn.execute("UPDATE messages SET state='unknown',provider_sid=COALESCE(?,provider_sid),reason=?,updated_at=? WHERE id=?", (sid, detail, now(), message_id))
            conn.execute("UPDATE attempts SET state='unknown',provider_sid=?,detail=?,updated_at=? WHERE id=?", (sid, detail, now(), attempt))
            pause_campaign(conn, message["campaign_id"], detail)
            audit(conn, "worker", "provider_unknown", "message", message_id, {"exception_type": type(exc).__name__, "retry_permitted": False})
        finally:
            if owns_provider and active_provider is not None and hasattr(active_provider, "close"):
                active_provider.close()
        apply_health_controls(conn)
    return True


def recover_stale(conn):
    """Crash recovery quarantines abandoned intents, never puts them back in queue."""
    cutoff = iso(datetime.now(timezone.utc) - timedelta(seconds=30))
    rows = conn.execute("SELECT * FROM messages WHERE state='dispatching' AND claimed_at<=?", (cutoff,)).fetchall()
    for row in rows:
        detail = "Worker interrupted after durable dispatch intent; provider acceptance is unknown"
        conn.execute("UPDATE messages SET state='unknown',reason=?,updated_at=? WHERE id=?", (detail, now(), row["id"]))
        conn.execute("UPDATE attempts SET state='unknown',detail=?,updated_at=? WHERE message_id=? AND state='dispatching'", (detail, now(), row["id"]))
        pause_campaign(conn, row["campaign_id"], detail)
        audit(conn, "worker", "recover_unknown", "message", row["id"], {"retry_permitted": False})
    return len(rows)


def classify_inbound(body, opt_out_type=""):
    text = re.sub(r"\s+", " ", body.strip().lower().replace("’", "'"))
    keyword = text.strip(".!?,;:").upper()
    if opt_out_type.upper() == "STOP" or keyword in OPTOUT_WORDS or re.search(r"\b(please )?(stop|unsubscribe)( please)?[.!?]*$", text):
        return "opt_out"
    if re.search(r"\b(remove me|remove my (number|contact)|take me off|delete my (number|contact)|do not (text|contact|message)|don't (text|contact|message)|stop (texting|messaging|contacting|sending)|no more (texts|messages)|unsubscribe me|leave me alone|go away)\b", text):
        return "opt_out"
    if re.search(r"\b(wrong (number|person)|not the owner|you have the wrong|not my (house|property|number))\b", text):
        return "wrong_number"
    if re.search(r"\b(report(ing)? (you|this)|harass(ing|ment)?|spam|complaint|illegal)\b", text):
        return "complaint"
    if opt_out_type.upper() == "START" or keyword in OPTIN_WORDS:
        return "renewal_review"  # never restores suppression automatically
    if opt_out_type.upper() == "HELP" or keyword in {"HELP", "INFO"}:
        return "help"
    if re.search(r"\b(not interested|no thanks|not selling|don't want|do not want)\b", text):
        return "negative"
    if re.search(r"\b(yes|interested|tell me more|let's talk|call me|make (me )?an offer)\b", text):
        return "interested"
    return "review"


def process_inbound(conn, params):
    sid = str(params.get("MessageSid") or params.get("SmsSid") or "")
    phone, to_phone = str(params.get("From") or ""), str(params.get("To") or "")
    body = str(params.get("Body") or "")[:10000]
    if not sid or not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
        raise ValueError("Inbound event requires a provider SID and normalized sender")
    if not _event(conn, "inbound:" + sid, "inbound", {"sid": sid, "from": phone, "to": to_phone, "opt_out_type": params.get("OptOutType", "")}):
        existing = conn.execute("SELECT * FROM inbound WHERE provider_sid=?", (sid,)).fetchone()
        return {"duplicate": True, "classification": existing["classification"] if existing else "review"}
    classification = classify_inbound(body, str(params.get("OptOutType") or ""))
    contact = conn.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
    already_suppressed = conn.execute("SELECT 1 FROM suppressions WHERE phone=? AND active=1", (phone,)).fetchone()
    if already_suppressed and classification == "interested":
        classification = "renewal_review"
    contact_id = contact["id"] if contact else None
    lead_status = "interested" if classification == "interested" else "suppressed" if classification in {"opt_out", "complaint", "wrong_number"} else "review"
    inbound_id = conn.execute("INSERT INTO inbound(provider_sid,contact_id,from_phone,to_phone,body,classification,lead_status,created_at) VALUES(?,?,?,?,?,?,?,?)", (sid, contact_id, phone, to_phone, body, classification, lead_status, now())).lastrowid
    if contact:
        conn.execute("UPDATE contacts SET reply_hold=1,updated_at=? WHERE id=?", (now(), contact_id))
        conn.execute("UPDATE messages SET state='canceled',reason='Recipient replied; inbox review required',updated_at=? WHERE contact_id=? AND kind='automated' AND state IN('queued','blocked','dispatching') AND provider_sid IS NULL", (now(), contact_id))
        conn.execute("UPDATE attempts SET state='canceled',detail='Recipient replied',updated_at=? WHERE state='dispatching' AND message_id IN(SELECT id FROM messages WHERE contact_id=? AND state='canceled')", (now(), contact_id))
    if classification in {"opt_out", "wrong_number", "complaint"}:
        suppress(conn, phone, {"opt_out": "Recipient opted out", "wrong_number": "Recipient reports wrong number", "complaint": "Recipient complaint"}[classification])
    simulated = sid.startswith("SIM") or params.get("AccountSid") == "SIM"
    set_setting(conn, "simulation_inbound_health_at" if simulated else "inbound_health_at", now())
    set_setting(conn, "simulation_suppression_health_at" if simulated else "suppression_health_at", now())
    audit(conn, "webhook", "inbound_received", "inbound", inbound_id, {"classification": classification, "contact_id": contact_id})
    apply_health_controls(conn)
    # Empty TwiML is returned by the route. Twilio manages keyword confirmations.
    return {"id": inbound_id, "classification": classification, "duplicate": False, "automated_reply": False}


def _price(value):
    try:
        number = abs(float(value))
        return number if number < 10000 else None
    except (TypeError, ValueError):
        return None


def process_status(conn, params, message_id=None, update_health=True):
    sid = str(params.get("MessageSid") or params.get("SmsSid") or "")
    status = str(params.get("MessageStatus") or params.get("SmsStatus") or "").lower()
    code = str(params.get("ErrorCode") or "")
    if not sid or status not in STATUS_MAP:
        raise ValueError("Unsupported or incomplete status callback")
    key = "status:" + hashlib.sha256(json.dumps([sid, status, code], separators=(",", ":")).encode()).hexdigest()
    row = conn.execute("SELECT * FROM messages WHERE provider_sid=?", (sid,)).fetchone()
    if row is None and message_id is not None:
        candidate = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if candidate and candidate["state"] in {"dispatching", "unknown"} and not candidate["provider_sid"]:
            row = candidate
    if row is not None and message_id is not None and row["id"] != int(message_id):
        raise ValueError("Status callback message identity does not match provider SID")
    if row is not None:
        recipient = conn.execute("SELECT phone FROM contacts WHERE id=?", (row["contact_id"],)).fetchone()
        if params.get("To") and (not recipient or params["To"] != recipient["phone"]):
            raise ValueError("Status callback recipient does not match the persisted message")
        if params.get("From") and row["sender"] and params["From"] != row["sender"]:
            raise ValueError("Status callback sender does not match the persisted message")
        if params.get("MessagingServiceSid") and params["MessagingServiceSid"] != row["service_sid"]:
            raise ValueError("Status callback service does not match the persisted message")
    duplicate = not _event(conn, key, "status", {"sid": sid, "status": status, "error_code": code})
    if update_health:
        set_setting(conn, "simulation_callback_health_at" if sid.startswith("SIM") else "callback_health_at", now())
    if row is None:
        if not duplicate:
            audit(conn, "webhook", "unmatched_status", "provider_message", sid, {"status": status, "error_code": code})
        return {"duplicate": duplicate, "matched": False}
    prior_event = conn.execute("SELECT payload FROM webhook_events WHERE event_key=?", (key,)).fetchone()
    prior_payload = json.loads(prior_event["payload"])
    was_processed = prior_payload.get("processed_message_id") == row["id"]
    # Legacy events without a processing marker are already handled if their SID
    # belongs to a known final/progressed message. Unknown intents can replay a
    # previously unmatched callback after evidence binds the same SID.
    if duplicate and "processed_message_id" not in prior_payload and row["state"] not in {"unknown", "dispatching"}:
        was_processed = True
    next_state = "filtered" if code == "30007" else STATUS_MAP[status]
    old_state = row["state"]
    changed = old_state not in TERMINAL and RANK[next_state] >= RANK.get(old_state, 0) and (not duplicate or old_state in {"unknown", "dispatching"})
    if changed:
        conn.execute("UPDATE messages SET state=?,provider_sid=?,accepted_at=COALESCE(accepted_at,?),actual_cost=COALESCE(?,actual_cost),reason=?,updated_at=? WHERE id=?", (next_state, sid, now(), _price(params.get("Price")), "Provider error " + code if code else None, now(), row["id"]))
        conn.execute("UPDATE attempts SET state=?,provider_sid=?,error_code=?,updated_at=? WHERE message_id=? AND state IN('dispatching','unknown','accepted','sent')", (next_state, sid, code, now(), row["id"]))
    cost = _price(params.get("Price"))
    cost_updated = cost is not None and cost != row["actual_cost"]
    if cost_updated:
        conn.execute("UPDATE messages SET actual_cost=?,updated_at=? WHERE id=?", (cost, now(), row["id"]))
    if code == "21610" and not was_processed and _event(conn, "provider-optout:" + sid, "provider_optout", {"sid": sid, "message_id": row["id"]}):
        contact = conn.execute("SELECT phone FROM contacts WHERE id=?", (row["contact_id"],)).fetchone()
        if contact:
            suppress(conn, contact["phone"], "Provider reports recipient opt-out")
    if code in RESTRICTION_ERRORS and not was_processed and _event(conn, "provider-restriction:" + sid + ":" + code, "provider_restriction", {"sid": sid, "error_code": code, "message_id": row["id"]}):
        _restriction_pause(conn, row["campaign_id"], code)
    prior_payload["processed_message_id"] = row["id"]
    conn.execute("UPDATE webhook_events SET payload=? WHERE event_key=?", (json.dumps(prior_payload), key))
    if not duplicate or changed or cost_updated:
        audit(conn, "webhook", "delivery_status", "message", row["id"], {"status": next_state, "previous": old_state, "applied": changed, "error_code": code, "duplicate": duplicate, "cost_updated": cost_updated})
    apply_health_controls(conn)
    return {"duplicate": duplicate, "matched": True, "state": next_state if changed else old_state, "applied": changed, "cost_updated": cost_updated}


def reconcile_unknown(message_id, provider=None):
    """Fetch a known SID only. Absence of a SID never authorizes resubmission."""
    from .provider import TwilioProvider
    conn = connect()
    try:
        message = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not message or message["state"] not in {"unknown", "delivered", "failed", "filtered"}:
            raise ValueError("Reconcile an unknown attempt or refresh costs for a terminal outcome")
        if not message["provider_sid"]:
            return {"reconciled": False, "reason": "No provider SID is known. Review Twilio logs and attach evidence; do not retry."}
        sid = message["provider_sid"]
    finally:
        conn.close()
    active_provider = provider or TwilioProvider()
    try:
        result = active_provider.fetch_message(sid)
    finally:
        if provider is None and hasattr(active_provider, "close"):
            active_provider.close()
    with transaction() as conn:
        output = process_status(conn, {"MessageSid": sid, "MessageStatus": result.get("status"), "Price": result.get("price"), "ErrorCode": result.get("error_code", ""), "To": result.get("to"), "From": result.get("from"), "MessagingServiceSid": result.get("messaging_service_sid")}, message_id, update_health=False)
        audit(conn, "operator", "reconcile_unknown", "message", message_id, {"sid": sid, "result": output})
    return {"reconciled": output.get("applied", False) or output.get("cost_updated", False), **output}
