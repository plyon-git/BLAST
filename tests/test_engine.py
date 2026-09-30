from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import threading

import pytest

from blastio import engine, policy
from blastio.config import settings
from blastio.db import connect, init_db, now, set_setting, transaction
from blastio.provider import ProviderRejected, ProviderUnknown


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "engine.sqlite3"))
    monkeypatch.setattr(settings, "business_name", "101XVC")
    monkeypatch.setattr(settings, "mode", "simulation")
    init_db()
    conn = connect()
    set_setting(conn, "policy", {"window_start": 0, "window_end": 24, "max_per_minute": 100, "max_recipient_24h": 10, "max_recipient_7d": 30})
    yield conn
    conn.close()


def campaign(db, phone="+12025550111", consent=True, sender="+12025550199", template_status="approved", contact_id=None):
    stamp = now()
    if contact_id is None:
        contact_id = db.execute("INSERT INTO contacts(phone,first_name,timezone,timezone_source,created_at,updated_at) VALUES(?,'Alice','UTC','manual_verified',?,?)", (phone, stamp, stamp)).lastrowid
        if consent:
            past = policy.iso(datetime.now(timezone.utc) - timedelta(days=1))
            db.execute("INSERT INTO consents(contact_id,business,channel,purpose,source,occurred_at,disclosure_version,evidence_ref,status,verified_by,verified_at,created_at) VALUES(?,'101XVC','sms','seller_outreach','signed form',?,'v1','evidence://1','verified','reviewer',?,?)", (contact_id, past, stamp, stamp))
    tid = db.execute("INSERT INTO templates(name,version,body,status,approved_by,approved_at,created_at) VALUES(?,1,'101XVC: Hi {{first_name}}, still interested? Reply STOP to unsubscribe',?,'reviewer',?,?)", ("template" + phone + str(db.execute("SELECT count(*) FROM templates").fetchone()[0]), template_status, stamp, stamp)).lastrowid
    cid = db.execute("INSERT INTO campaigns(name,template_id,status,mode,sender,approved_by,approved_at,created_at) VALUES('Example',?,'approved','simulation',?,'reviewer',?,?)", (tid, sender, stamp, stamp)).lastrowid
    db.execute("INSERT INTO campaign_contacts VALUES(?,?,NULL)", (cid, contact_id))
    return cid, contact_id


def queued(db, **kwargs):
    cid, contact_id = campaign(db, **kwargs)
    engine.enqueue_campaign(db, cid, "reviewer")
    row = db.execute("SELECT * FROM messages WHERE campaign_id=?", (cid,)).fetchone()
    return dict(row), cid, contact_id


class RecordingProvider:
    environment = "simulation"

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def send(self, message):
        self.calls.append(dict(message))
        if self.error:
            raise self.error
        return {"sid": "SIM" + str(message["id"]), "status": "accepted", "from": message["sender"]}


def test_missing_consent_and_unapproved_template_never_submit(db):
    provider = RecordingProvider()
    row, _, _ = queued(db, consent=False)
    assert row["state"] == "blocked"
    row, _, _ = queued(db, phone="+12025550112", template_status="draft")
    assert row["state"] == "blocked"
    assert engine.dispatch_one(provider) is False
    assert provider.calls == []


def test_suppression_cancels_all_business_senders_and_survives_cloning(db):
    row, cid, contact_id = queued(db)
    second, _, _ = queued(db, sender="+12025550198", contact_id=contact_id)
    result = engine.suppress(db, "+12025550111", "STOP")
    assert result["canceled"] == 2
    clone, _ = campaign(db, contact_id=contact_id)
    counts = engine.enqueue_campaign(db, clone, "reviewer")
    assert counts["blocked"] == 1
    assert db.execute("SELECT count(*) FROM suppressions WHERE active=1").fetchone()[0] == 1
    assert engine.dispatch_one(RecordingProvider()) is False


def test_stop_wins_between_intent_commit_and_final_gate(db, monkeypatch):
    row, _, _ = queued(db)
    real_transaction = transaction
    calls = 0

    @contextmanager
    def intervening_transaction():
        nonlocal calls
        with real_transaction() as conn:
            yield conn
        calls += 1
        if calls == 1:
            with real_transaction() as conn:
                engine.process_inbound(conn, {"MessageSid": "SMSTOP", "From": "+12025550111", "To": "+12025550199", "Body": "STOP"})

    monkeypatch.setattr(engine, "transaction", intervening_transaction)
    provider = RecordingProvider()
    assert engine.dispatch_one(provider)
    assert provider.calls == []
    assert db.execute("SELECT state FROM messages WHERE id=?", (row["id"],)).fetchone()[0] == "canceled"
    assert db.execute("SELECT state FROM attempts").fetchone()[0] == "canceled"


def test_stop_during_bounded_submission_cancels_future_but_cannot_recall(db):
    row, _, contact_id = queued(db)
    other, _, _ = queued(db, contact_id=contact_id)
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    errors = []

    class HeldProvider(RecordingProvider):
        def send(self, message):
            entered.set()
            assert release.wait(3)
            return super().send(message)

    def dispatch():
        try:
            engine.dispatch_one(HeldProvider())
        except Exception as exc:
            errors.append(exc)

    def stop():
        try:
            with transaction() as conn:
                engine.process_inbound(conn, {"MessageSid": "SMSTOP2", "From": "+12025550111", "To": "+12025550198", "Body": "Don't text me"})
            stopped.set()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=dispatch)
    worker.start()
    assert entered.wait(3)
    stopper = threading.Thread(target=stop)
    stopper.start()
    assert not stopped.wait(0.1)
    release.set()
    worker.join(4)
    stopper.join(4)
    assert not errors
    assert stopped.is_set()
    assert db.execute("SELECT state FROM messages WHERE id=?", (row["id"],)).fetchone()[0] == "accepted"
    assert db.execute("SELECT state FROM messages WHERE id=?", (other["id"],)).fetchone()[0] == "canceled"


def test_ambiguous_timeout_is_unknown_paused_and_never_retried(db):
    row, cid, _ = queued(db)
    provider = RecordingProvider(ProviderUnknown("timeout"))
    assert engine.dispatch_one(provider)
    assert db.execute("SELECT state FROM messages WHERE id=?", (row["id"],)).fetchone()[0] == "unknown"
    assert db.execute("SELECT state FROM attempts").fetchone()[0] == "unknown"
    assert db.execute("SELECT status FROM campaigns WHERE id=?", (cid,)).fetchone()[0] == "paused"
    assert engine.dispatch_one(provider) is False
    assert len(provider.calls) == 1
    assert engine.reconcile_unknown(row["id"], provider)["reconciled"] is False


def test_provider_rejection_is_distinct_from_unknown(db):
    row, _, _ = queued(db)
    provider = RecordingProvider(ProviderRejected("Rejected", 21610))
    engine.dispatch_one(provider)
    assert db.execute("SELECT state FROM messages WHERE id=?", (row["id"],)).fetchone()[0] == "failed"
    assert db.execute("SELECT active FROM suppressions").fetchone()[0] == 1


def test_crash_intent_is_committed_then_recovers_unknown(db):
    row, cid, _ = queued(db)

    class CrashProvider(RecordingProvider):
        def send(self, message):
            # Separate connection can observe committed dispatch intent during I/O.
            reader = connect()
            try:
                assert reader.execute("SELECT state FROM messages WHERE id=?", (message["id"],)).fetchone()[0] == "dispatching"
                assert reader.execute("SELECT state FROM attempts").fetchone()[0] == "dispatching"
            finally:
                reader.close()
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        engine.dispatch_one(CrashProvider())
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "dispatching"
    old = policy.iso(datetime.now(timezone.utc) - timedelta(minutes=1))
    db.execute("UPDATE messages SET claimed_at=?", (old,))
    assert engine.recover_stale(db) == 1
    assert engine.recover_stale(db) == 0
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "unknown"
    assert db.execute("SELECT status FROM campaigns WHERE id=?", (cid,)).fetchone()[0] == "paused"


def test_duplicate_and_out_of_order_callbacks_are_monotone(db):
    row, _, _ = queued(db)
    engine.dispatch_one(RecordingProvider())
    sid = "SIM" + str(row["id"])
    assert engine.process_status(db, {"MessageSid": sid, "MessageStatus": "delivered"})["applied"]
    assert engine.process_status(db, {"MessageSid": sid, "MessageStatus": "sent"})["state"] == "delivered"
    assert engine.process_status(db, {"MessageSid": sid, "MessageStatus": "failed", "ErrorCode": "30003"})["state"] == "delivered"
    assert engine.process_status(db, {"MessageSid": sid, "MessageStatus": "delivered"})["duplicate"]


def test_callback_reconciles_unknown_with_no_response_sid(db):
    row, _, _ = queued(db)
    engine.dispatch_one(RecordingProvider(ProviderUnknown("timeout")))
    result = engine.process_status(db, {"MessageSid": "SMlate", "MessageStatus": "delivered"}, row["id"])
    assert result["applied"]
    assert db.execute("SELECT provider_sid,state FROM messages").fetchone()[:] == ("SMlate", "delivered")
    assert db.execute("SELECT status FROM campaigns").fetchone()[0] == "paused"  # review before resume


@pytest.mark.parametrize("body,classification", [("STOP", "opt_out"), ("remove me", "opt_out"), ("don't text me", "opt_out"), ("please stop", "opt_out"), ("leave me alone", "opt_out"), ("Remove my number", "opt_out"), ("Wrong number", "wrong_number"), ("This is spam", "complaint"), ("START", "renewal_review"), ("Yes, tell me more", "interested"), ("No thanks", "negative"), ("What?", "review")])
def test_inbound_deterministic_hold_and_no_optout_engagement(db, body, classification):
    row, _, contact_id = queued(db)
    params = {"MessageSid": "SMinbound", "From": "+12025550111", "To": "+12025550199", "Body": body}
    result = engine.process_inbound(db, params)
    assert result["classification"] == classification
    assert result["automated_reply"] is False
    assert db.execute("SELECT reply_hold FROM contacts WHERE id=?", (contact_id,)).fetchone()[0] == 1
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "canceled"
    assert engine.process_inbound(db, params)["duplicate"]
    assert db.execute("SELECT count(*) FROM inbound").fetchone()[0] == 1
    assert (db.execute("SELECT count(*) FROM suppressions WHERE active=1").fetchone()[0] == 1) == (classification in {"opt_out", "complaint", "wrong_number"})


def test_positive_reply_cannot_restore_and_old_evidence_rejected(db):
    row, _, contact_id = queued(db)
    engine.suppress(db, "+12025550111", "STOP")
    engine.process_inbound(db, {"MessageSid": "SMstart", "From": "+12025550111", "To": "+12025550199", "Body": "START"})
    assert db.execute("SELECT active FROM suppressions").fetchone()[0] == 1
    consent_id = db.execute("SELECT id FROM consents").fetchone()[0]
    with pytest.raises(ValueError, match="revoked"):
        engine.restore_consent(db, consent_id, "reviewer")
    old = policy.iso(datetime.now(timezone.utc) - timedelta(days=1))
    fresh = db.execute("INSERT INTO consents(contact_id,business,channel,purpose,source,occurred_at,disclosure_version,evidence_ref,status,created_at) VALUES(?,'101XVC','sms','seller_outreach','signed form',?,'v2','evidence://renewal','pending',?)", (contact_id, old, now())).lastrowid
    with pytest.raises(ValueError, match="postdate"):
        engine.restore_consent(db, fresh, "reviewer")
    # Use distinct timestamps without sleeps: suppression is yesterday; renewed
    # evidence is an hour old and therefore demonstrably postdates revocation.
    db.execute("UPDATE suppression_events SET created_at=? WHERE action='suppress'", (old,))
    newer = policy.iso(datetime.now(timezone.utc) - timedelta(hours=1))
    db.execute("UPDATE consents SET occurred_at=? WHERE id=?", (newer, fresh))
    result = engine.restore_consent(db, fresh, "reviewer")
    assert result["suppression_cleared"]
    assert policy.eligibility(db, contact_id) == []
    assert db.execute("SELECT reply_hold FROM contacts").fetchone()[0] == 1
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "canceled"


def test_kill_switch_stops_dispatch_and_queue_deduplicates(db):
    row, cid, _ = queued(db)
    assert engine.enqueue_campaign(db, cid, "reviewer")["duplicates"] == 1
    set_setting(db, "global_pause", True)
    provider = RecordingProvider()
    engine.dispatch_one(provider)
    assert provider.calls == []
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "queued"


def test_expiration_and_throughput_defer_without_send_or_attempt(db):
    row, _, _ = queued(db)
    set_setting(db, "policy", {"window_start": 0, "window_end": 24, "max_per_minute": 0})
    provider = RecordingProvider()
    engine.dispatch_one(provider)
    assert not provider.calls
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "queued"
    assert db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
    db.execute("UPDATE messages SET expires_at=?", (policy.iso(datetime.now(timezone.utc) - timedelta(seconds=1)),))
    engine.dispatch_one(provider)
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "expired"


def test_filtering_threshold_pauses_without_automatic_resume(db):
    row, cid, _ = queued(db)
    set_setting(db, "policy", {"window_start": 0, "window_end": 24, "max_per_minute": 100, "health_min_sample": 1, "max_filter_rate": 0.05})
    engine.dispatch_one(RecordingProvider())
    engine.process_status(db, {"MessageSid": "SIM" + str(row["id"]), "MessageStatus": "undelivered", "ErrorCode": "30007"})
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "filtered"
    assert db.execute("SELECT status FROM campaigns WHERE id=?", (cid,)).fetchone()[0] == "paused"
    engine.apply_health_controls(db)
    assert db.execute("SELECT status FROM campaigns WHERE id=?", (cid,)).fetchone()[0] == "paused"


def test_overdue_real_provider_callbacks_pause_even_if_self_probe_fresh(db):
    row, cid, _ = queued(db)
    old = policy.iso(datetime.now(timezone.utc) - timedelta(hours=1))
    db.execute("UPDATE campaigns SET mode='production' WHERE id=?", (cid,))
    db.execute("UPDATE messages SET mode='production',state='accepted',accepted_at=?", (old,))
    set_setting(db, "callback_health_at", now())
    engine.apply_health_controls(db)
    assert db.execute("SELECT status FROM campaigns WHERE id=?", (cid,)).fetchone()[0] == "paused"


def test_campaign_pause_preserves_pending_queue_and_requires_review(db):
    row, cid, _ = queued(db)
    engine.pause_campaign(db, cid, "Filtering review")
    provider = RecordingProvider()
    assert engine.dispatch_one(provider) is False
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "queued"
    assert provider.calls == []


def test_auth_restriction_globally_pauses_other_campaigns(db):
    row, cid, _ = queued(db)
    other, _, _ = queued(db, phone="+12025550112")
    provider = RecordingProvider(ProviderRejected("Permission denied", 20403))
    engine.dispatch_one(provider)
    assert engine.dispatch_one(provider) is False
    assert len(provider.calls) == 1
    assert db.execute("SELECT state FROM messages WHERE id=?", (other["id"],)).fetchone()[0] == "queued"


def test_immediate_provider_delivery_outcome_is_recorded_without_callback_health(db):
    row, _, _ = queued(db)

    class ImmediateProvider(RecordingProvider):
        def send(self, message):
            result = super().send(message)
            result.update(status="undelivered", error_code=30007)
            return result

    engine.dispatch_one(ImmediateProvider())
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "filtered"
    assert db.execute("SELECT count(*) FROM settings WHERE key='callback_health_at'").fetchone()[0] == 0


def test_simulated_inbound_does_not_establish_production_health(db):
    engine.process_inbound(db, {"MessageSid": "SIM-inbound", "From": "+12025550111", "To": "+12025550199", "Body": "STOP"})
    assert db.execute("SELECT count(*) FROM settings WHERE key='inbound_health_at'").fetchone()[0] == 0


def test_generic_yes_is_interest_but_cannot_undo_suppression(db):
    row, _, _ = queued(db)
    assert engine.process_inbound(db, {"MessageSid": "SMYES1", "From": "+12025550111", "To": "+12025550199", "Body": "YES"})["classification"] == "interested"
    engine.suppress(db, "+12025550111", "STOP")
    assert engine.process_inbound(db, {"MessageSid": "SMYES2", "From": "+12025550111", "To": "+12025550199", "Body": "YES"})["classification"] == "renewal_review"
    assert db.execute("SELECT active FROM suppressions").fetchone()[0] == 1


def test_unknown_callback_binding_rejects_other_recipient(db):
    row, _, _ = queued(db)
    engine.dispatch_one(RecordingProvider(ProviderUnknown("timeout")))
    with transaction() as conn:
        with pytest.raises(ValueError, match="recipient"):
            engine.process_status(conn, {"MessageSid": "SMother", "MessageStatus": "delivered", "To": "+12025550112"}, row["id"])
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "unknown"


def test_enqueue_while_globally_paused_preserves_safe_queue(db):
    cid, _ = campaign(db)
    set_setting(db, "global_pause", True)
    result = engine.enqueue_campaign(db, cid, "reviewer")
    assert result["queued"] == 1
    assert result["blocked"] == 0
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "queued"
    assert engine.dispatch_one(RecordingProvider()) is False
    set_setting(db, "global_pause", False)
    provider = RecordingProvider()
    assert engine.dispatch_one(provider)
    assert len(provider.calls) == 1


def test_global_pause_never_hides_missing_consent_during_enqueue(db):
    cid, _ = campaign(db, consent=False)
    set_setting(db, "global_pause", True)
    result = engine.enqueue_campaign(db, cid, "reviewer")
    assert result["blocked"] == 1
    assert result["queued"] == 0


def test_future_projected_staleness_can_wait_only_with_current_health(db):
    message = {}
    assert engine._enqueue_wait_reason(db, message, "inbound health is stale or unavailable") is False
    set_setting(db, "inbound_health_at", now())
    assert engine._enqueue_wait_reason(db, message, "inbound health is stale or unavailable") is True
    set_setting(db, "inbound_health_at", policy.iso(datetime.now(timezone.utc) - timedelta(hours=1)))
    assert engine._enqueue_wait_reason(db, message, "inbound health is stale or unavailable") is False


def test_future_production_queue_requires_health_now_then_rechecks_at_dispatch(db, monkeypatch):
    import json
    cid, _ = campaign(db)
    monkeypatch.setattr(settings, "mode", "production")
    monkeypatch.setattr(settings, "allow_production", True)
    stamp = now()
    later = policy.iso(datetime.now(timezone.utc) + timedelta(hours=2))
    db.execute("UPDATE campaigns SET mode='production',service_sid='MGverified',scheduled_at=? WHERE id=?", (later, cid))
    verification = dict(account_ok=True, service_ok=True, campaign_ok=True, webhooks_ok=True, authorized_senders=["+12025550199"], service_sid="MGverified", campaign_id="verified-campaign")
    db.execute("INSERT INTO credentials VALUES('production','ACtest','SKtest','encrypted','encrypted','MGverified',?,?,?)", (json.dumps(verification), stamp, stamp))
    set_setting(db, "readiness", dict(advanced_optout_reviewed=True, sender_associations_reviewed=True, campaign_content_reviewed=True, smoke_test_reviewed=True, reviewed_by="reviewer", reviewed_at=stamp))
    for key in ("inbound_health_at", "callback_health_at", "suppression_health_at"):
        set_setting(db, key, stamp)
    output = engine.enqueue_campaign(db, cid, "reviewer")
    assert output["queued"] == 1
    message = db.execute("SELECT * FROM messages WHERE campaign_id=?", (cid,)).fetchone()
    assert any("inbound health is stale" in r for r in policy.gate(db, message, policy.timestamp(later)))
    assert "Awaiting operational" in message["reason"]


def test_unmatched_callback_replays_when_unknown_sid_is_later_evidenced(db):
    row, _, _ = queued(db)
    engine.dispatch_one(RecordingProvider(ProviderUnknown("timeout")))
    params = {"MessageSid": "SMlost", "MessageStatus": "delivered"}
    assert engine.process_status(db, params)["matched"] is False
    db.execute("UPDATE messages SET provider_sid='SMlost' WHERE id=?", (row["id"],))
    replay = engine.process_status(db, params, row["id"])
    assert replay["duplicate"]
    assert replay["applied"]
    assert db.execute("SELECT state FROM messages").fetchone()[0] == "delivered"


def test_terminal_duplicate_status_can_refresh_cost_without_regression(db):
    row, _, _ = queued(db)
    engine.dispatch_one(RecordingProvider())
    params = {"MessageSid": "SIM" + str(row["id"]), "MessageStatus": "delivered"}
    engine.process_status(db, params)
    replay = engine.process_status(db, {**params, "Price": "-0.015"})
    assert replay["duplicate"]
    assert replay["cost_updated"]
    assert replay["state"] == "delivered"
    assert db.execute("SELECT actual_cost FROM messages").fetchone()[0] == 0.015


def test_duplicate_optout_status_does_not_revoke_documented_renewal(db):
    row, _, contact_id = queued(db)
    engine.dispatch_one(RecordingProvider())
    params = {"MessageSid": "SIM" + str(row["id"]), "MessageStatus": "failed", "ErrorCode": "21610"}
    engine.process_status(db, params)
    old = policy.iso(datetime.now(timezone.utc) - timedelta(days=1))
    db.execute("UPDATE suppression_events SET created_at=? WHERE action='suppress'", (old,))
    renewed = db.execute("INSERT INTO consents(contact_id,business,channel,purpose,source,occurred_at,disclosure_version,evidence_ref,status,created_at) VALUES(?,'101XVC','sms','seller_outreach','signed form',?,'v2','evidence://renewal','pending',?)", (contact_id, policy.iso(datetime.now(timezone.utc) - timedelta(hours=1)), now())).lastrowid
    engine.restore_consent(db, renewed, "reviewer")
    assert engine.process_status(db, params)["duplicate"]
    delayed = engine.process_status(db, {**params, "MessageStatus": "undelivered"})
    assert not delayed["applied"]
    assert db.execute("SELECT active FROM suppressions").fetchone()[0] == 0
    assert policy.eligibility(db, contact_id) == []
