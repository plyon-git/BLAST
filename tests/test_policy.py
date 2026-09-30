from datetime import datetime, timedelta, timezone

import pytest

from blastio import policy
from blastio.config import settings
from blastio.db import connect, init_db, now, set_setting


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "policy.sqlite3"))
    monkeypatch.setattr(settings, "business_name", "101XVC")
    monkeypatch.setattr(settings, "mode", "simulation")
    monkeypatch.setattr(settings, "allow_production", False)
    init_db()
    conn = connect()
    yield conn
    conn.close()


def valid_message(db):
    stamp = now()
    at = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    occurred = policy.iso(at - timedelta(days=1))
    cid = db.execute("INSERT INTO contacts(phone,first_name,timezone,timezone_source,created_at,updated_at) VALUES('+12025550111','Alice','UTC','manual_verified',?,?)", (stamp, stamp)).lastrowid
    db.execute("INSERT INTO consents(contact_id,business,channel,purpose,source,occurred_at,disclosure_version,evidence_ref,status,verified_by,verified_at,created_at) VALUES(?,'101XVC','sms','seller_outreach','signed form',?,'v1','evidence://1','verified','reviewer',?,?)", (cid, occurred, stamp, stamp))
    body = "101XVC: Hi {{first_name}}, are you still interested? Reply STOP to unsubscribe"
    tid = db.execute("INSERT INTO templates(name,version,body,status,approved_by,approved_at,created_at) VALUES('Example',1,?,'approved','reviewer',?,?)", (body, stamp, stamp)).lastrowid
    campaign = db.execute("INSERT INTO campaigns(name,template_id,status,mode,approved_by,approved_at,created_at) VALUES('Example',?,'approved','simulation','reviewer',?,?)", (tid, stamp, stamp)).lastrowid
    message = dict(id=-1, campaign_id=campaign, contact_id=cid, property_id=None, template_id=tid, kind="automated", mode="simulation", state="queued", body=policy.render(body, {"first_name": "Alice"}), sender=None, service_sid=None, scheduled_at=policy.iso(at), expires_at=policy.iso(at + timedelta(hours=1)))
    return message, at


def test_valid_simulation_keeps_real_policy_gates(db):
    message, at = valid_message(db)
    assert policy.gate(db, message, at) == []
    db.execute("DELETE FROM consents")
    assert any("Verified consent" in r for r in policy.gate(db, message, at))


def test_unverified_and_wrong_scope_consent_are_insufficient(db):
    message, at = valid_message(db)
    for column, value in (("status", "pending"), ("purpose", "investor_outreach"), ("business", "Other Business"), ("channel", "email"), ("evidence_ref", "")):
        old = db.execute(f"SELECT {column} FROM consents").fetchone()[0]
        db.execute(f"UPDATE consents SET {column}=?", (value,))
        assert any("Verified consent" in r for r in policy.gate(db, message, at)), column
        db.execute(f"UPDATE consents SET {column}=?", (old,))


def test_timezone_uncertainty_even_in_simulation(db):
    message, at = valid_message(db)
    db.execute("UPDATE contacts SET timezone_source='area_code'")
    assert any("timezone must be confirmed" in r for r in policy.gate(db, message, at))


def test_dst_uses_recipient_actual_offset(db):
    message, _ = valid_message(db)
    db.execute("UPDATE contacts SET timezone='America/New_York'")
    for date, before, after in (("2026-03-08", 12, 13), ("2026-11-01", 13, 14)):
        message["scheduled_at"] = date + "T00:00:00+00:00"
        message["expires_at"] = date + "T23:59:59+00:00"
        assert any("Outside" in r for r in policy.gate(db, message, datetime.fromisoformat(f"{date}T{before}:30:00+00:00")))
        assert not any("Outside" in r for r in policy.gate(db, message, datetime.fromisoformat(f"{date}T{after}:30:00+00:00")))


def test_merge_fields_are_literal_and_required():
    assert policy.render("Hi {{first_name}} {{last_name}}", {}) == "Hi there "
    assert policy.render("{{first_name}}", {"first_name": "$(never executed)"}) == "$(never executed)"
    for body, contact in (("{{street_address}}", {}), ("{{unknown}}", {}), ("{{broken", {}), ("{{first_name}}", {"first_name": "{{business_name}}"}), ("{{first_name}}", {"first_name": "Alice\nSTOP"})):
        with pytest.raises(ValueError):
            policy.render(body, contact)


def test_sms_encoding_units_and_segments():
    assert policy.sms_metrics("a" * 160)["segments"] == 1
    assert policy.sms_metrics("a" * 161)["segments"] == 2
    assert policy.sms_metrics("^" * 80)["units"] == 160
    assert policy.sms_metrics("^" * 81)["segments"] == 2
    assert policy.sms_metrics("😀" * 35)["units"] == 70
    assert policy.sms_metrics("😀" * 36)["segments"] == 2
    assert policy.sms_metrics("“" * 71)["encoding"] == "UCS-2"


def test_unapproved_template_and_body_tampering(db):
    message, at = valid_message(db)
    message["body"] += " We previously spoke."
    assert any("Persisted body" in r for r in policy.gate(db, message, at))
    db.execute("UPDATE templates SET status='draft'")
    assert any("Template version requires approval" in r for r in policy.gate(db, message, at))
    with pytest.raises(Exception, match="immutable"):
        db.execute("UPDATE templates SET body='mutated'")


def test_manual_reply_obeys_suppression_and_kill_switch(db):
    message, at = valid_message(db)
    message["kind"] = "manual"
    db.execute("UPDATE contacts SET reply_hold=1")
    assert policy.gate(db, message, at) == []
    set_setting(db, "global_pause", True)
    assert "Global kill switch is active" in policy.gate(db, message, at)
    db.execute("INSERT INTO suppressions VALUES('+12025550111','stop',1,?,?)", (now(), now()))
    assert any("Suppressed" in r for r in policy.gate(db, message, at))


def test_frequency_and_cost_limits_include_other_campaigns(db):
    message, at = valid_message(db)
    stamp = policy.iso(at - timedelta(minutes=2))
    db.execute("INSERT INTO messages(campaign_id,contact_id,template_id,mode,state,body,scheduled_at,expires_at,accepted_at,estimated_cost,created_at,updated_at) VALUES(?,?,?,'simulation','unknown','accepted?',?,?,?,1,?,?)", (message["campaign_id"], message["contact_id"], message["template_id"], stamp, message["expires_at"], stamp, stamp, stamp))
    assert any("Recipient frequency" in r for r in policy.gate(db, message, at))
    set_setting(db, "policy", {"max_daily_cost": 0.5})
    assert any("spending" in r for r in policy.gate(db, message, at))


def test_broken_suppression_store_fails_closed(db):
    message, at = valid_message(db)
    db.execute("DROP TABLE suppressions")
    assert any("safeguards are unavailable" in r for r in policy.gate(db, message, at))


def test_production_requires_setup_and_rejects_synthetic(db, monkeypatch):
    message, at = valid_message(db)
    message["mode"] = "production"
    db.execute("UPDATE campaigns SET mode='production'")
    db.execute("UPDATE consents SET evidence_ref='simulation://fixture'")
    reasons = policy.gate(db, message, at)
    assert any("Verified consent" in r for r in reasons)
    assert "Production is not enabled in the deployment" in reasons
    assert "Production Twilio connection is missing" in reasons
    assert any("health" in r for r in reasons)


def test_conservative_optout_and_identification(db):
    message, at = valid_message(db)
    message["body"] = "Hi Alice, interested? Reply QUIT to unsubscribe"
    reasons = policy.gate(db, message, at)
    assert any("identify" in r for r in reasons)
    assert any("STOP" in r for r in reasons)


def test_production_sender_cannot_change_across_campaigns(db):
    message, at = valid_message(db)
    stamp = policy.iso(at - timedelta(days=1))
    db.execute("UPDATE campaigns SET mode='production',sender='+12025550199',service_sid='MGtest'")
    message.update(mode="production", sender="+12025550199", service_sid="MGtest")
    db.execute("INSERT INTO messages(campaign_id,contact_id,template_id,mode,state,body,sender,scheduled_at,expires_at,accepted_at,created_at,updated_at) VALUES(?,?,?,'production','delivered','prior','+12025550198',?,?,?,?,?)", (message["campaign_id"], message["contact_id"], message["template_id"], stamp, message["expires_at"], stamp, stamp, stamp))
    assert any("existing conversation sender" in r for r in policy.gate(db, message, at))
    db.execute("UPDATE messages SET state='filtered'")
    assert any("existing conversation sender" in r for r in policy.gate(db, message, at))
    db.execute("UPDATE messages SET state='failed'")
    assert any("existing conversation sender" in r for r in policy.gate(db, message, at))


def test_production_complete_gates_then_stale_health_blocks(db, monkeypatch):
    import json
    message, _ = valid_message(db)
    at = datetime.now(timezone.utc)
    stamp = policy.iso(at)
    monkeypatch.setattr(settings, "mode", "production")
    monkeypatch.setattr(settings, "allow_production", True)
    message.update(mode="production", sender="+12025550199", service_sid="MGverified", scheduled_at=stamp, expires_at=policy.iso(at + timedelta(hours=1)))
    db.execute("UPDATE campaigns SET mode='production',sender='+12025550199',service_sid='MGverified'")
    set_setting(db, "policy", {"window_start": 0, "window_end": 24})
    verification = dict(account_ok=True, service_ok=True, campaign_ok=True, webhooks_ok=True, authorized_senders=["+12025550199"], service_sid="MGverified", campaign_id="verified-carrier-campaign")
    db.execute("INSERT INTO credentials VALUES('production','ACtest','SKtest','encrypted','encrypted','MGverified',?,?,?)", (json.dumps(verification), stamp, stamp))
    set_setting(db, "readiness", dict(advanced_optout_reviewed=True, sender_associations_reviewed=True, campaign_content_reviewed=True, smoke_test_reviewed=True, reviewed_by="reviewer", reviewed_at=stamp))
    for key in ("inbound_health_at", "callback_health_at", "suppression_health_at"):
        set_setting(db, key, stamp)
    assert policy.gate(db, message, at) == []
    set_setting(db, "inbound_health_at", policy.iso(at - timedelta(seconds=settings.health_ttl + 1)))
    assert any("inbound health" in r for r in policy.gate(db, message, at))
    db.execute("UPDATE credentials SET verification='{}'")
    assert any("Twilio campaign verification" in r for r in policy.gate(db, message, at))
