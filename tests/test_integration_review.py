"""Cross-module regressions against the operator API; no provider send is made."""
import base64
import hashlib
import hmac
import importlib
import json
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from blastio import engine, provider
from blastio.app import app
from blastio.auth import hash_password
from blastio.config import settings
from blastio.db import get_setting, init_db, now, set_setting, transaction

app_module = importlib.import_module('blastio.app')


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    for key, value in {'db_path': str(tmp_path / 'integration.sqlite3'),
                       'encryption_key': Fernet.generate_key().decode(),
                       'public_url': 'https://testserver', 'mode': 'simulation',
                       'allow_production': False, 'cookie_secure': True,
                       'business_name': '101XVC'}.items():
        monkeypatch.setattr(settings, key, value)
    init_db()
    with transaction() as conn:
        password = hash_password('correct-password-123')
        for role in ('admin', 'reviewer', 'operator', 'viewer'):
            conn.execute('INSERT INTO users(email,password_hash,role,created_at) VALUES(?,?,?,?)',
                         (role + '@example.test', password, role, now()))
        set_setting(conn, 'policy', {'window_start': 0, 'window_end': 24,
                                    'max_per_minute': 100, 'max_recipient_24h': 10,
                                    'max_recipient_7d': 30})
    with TestClient(app, base_url='https://testserver') as client:
        yield client


def login(client, role='admin'):
    response = client.post('/api/login', json={'email': role + '@example.test',
                                               'password': 'correct-password-123'})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf']


def saved_credentials(client, secret='initial-secret'):
    data = {'environment': 'production', 'account_sid': 'AC' + '1' * 32,
            'api_key_sid': 'SK' + '2' * 32, 'api_secret': secret,
            'auth_token': 'a' * 32, 'service_sid': 'MG' + '3' * 32}
    response = client.post('/api/twilio', json=data)
    assert response.status_code == 200, response.text
    return data


def verification():
    return {'account_ok': True, 'service_ok': True, 'campaign_ok': True,
            'webhooks_ok': True, 'service_sid': 'MG' + '3' * 32,
            'campaign_id': 'campaign-reviewed', 'authorized_senders': ['+12025550199'],
            'account': {'sid': 'AC' + '1' * 32, 'status': 'active', 'type': 'Full'},
            'service': {'sid': 'MG' + '3' * 32, 'account_sid': 'AC' + '1' * 32,
                        'inbound_request_url': 'https://testserver/webhooks/twilio/inbound',
                        'inbound_method': 'POST', 'sticky_sender': True,
                        'use_inbound_webhook_on_number': False},
            'campaigns': [{'sid': 'QE' + '4' * 32, 'campaign_status': 'VERIFIED',
                           'description': 'Reviewed seller outreach', 'message_samples': ['Reviewed sample']}],
            'webhooks': {'expected_inbound_url': 'https://testserver/webhooks/twilio/inbound',
                         'expected_status_url': 'https://testserver/webhooks/twilio/status',
                         'inbound_configuration_matches': True,
                         'status_callback_strategy': 'per_message_override'},
            'checks': {}, 'checked_at': now()}


def readiness():
    return {'advanced_optout_reviewed': True, 'sender_associations_reviewed': True,
            'campaign_content_reviewed': True, 'smoke_test_reviewed': True,
            'reviewed_by': 'admin@example.test', 'reviewed_at': now(),
            'review_note': 'Manual review of all production setup evidence'}


def seed_campaign(mode='simulation', states=('queued',)):
    stamp = now()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    with transaction() as conn:
        body = '101XVC: Would you like to discuss your property? Reply STOP to unsubscribe'
        tid = conn.execute('INSERT INTO templates(name,version,body,status,approved_by,approved_at,created_at) VALUES(?,1,?,\'approved\',?,?,?)',
                           ('Reviewed ' + mode, body, 'reviewer@example.test', stamp, stamp)).lastrowid
        cid = conn.execute('INSERT INTO campaigns(name,template_id,status,mode,service_sid,sender,scheduled_at,approved_by,approved_at,created_at) VALUES(?,?,\'scheduled\',?,?,?,?,?,?,?)',
                           ('Reviewed campaign', tid, mode, 'MG' + '3' * 32,
                            '+12025550199', stamp, 'reviewer@example.test', stamp, stamp)).lastrowid
        mids, contacts = [], []
        for index, state in enumerate(states):
            contact = conn.execute('INSERT INTO contacts(phone,first_name,timezone,timezone_source,created_at,updated_at) VALUES(?,?,\'UTC\',\'manual_verified\',?,?)',
                                   ('+120255501' + str(index + 10), 'Synthetic', stamp, stamp)).lastrowid
            contacts.append(contact)
            conn.execute('INSERT INTO consents(contact_id,business,channel,purpose,source,occurred_at,disclosure_version,evidence_ref,status,verified_by,verified_at,created_at) VALUES(?,\'101XVC\',\'sms\',\'seller_outreach\',\'signed record\',?,\'v1\',\'evidence://consent/123\',\'verified\',?,?,?)',
                         (contact, past, 'reviewer@example.test', stamp, stamp))
            conn.execute('INSERT INTO campaign_contacts VALUES(?,?,NULL)', (cid, contact))
            mid = conn.execute('INSERT INTO messages(campaign_id,contact_id,template_id,mode,state,body,sender,service_sid,scheduled_at,expires_at,claimed_at,segments,estimated_cost,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (cid, contact, tid, mode, state, body, '+12025550199',
                                'MG' + '3' * 32, stamp, expires,
                                stamp if state in ('unknown', 'accepted', 'sent') else None,
                                1, .015, stamp, stamp)).lastrowid
            mids.append(mid)
            if state == 'unknown':
                conn.execute('INSERT INTO attempts(message_id,state,created_at,updated_at) VALUES(?,\'unknown\',?,?)', (mid, stamp, stamp))
        return cid, contacts, mids


def install_readonly_provider(monkeypatch, result):
    calls = []

    class ReadonlyProvider:
        def __init__(self, *args, **kwargs):
            calls.append(('constructed',))

        def verify(self):
            calls.append(('verify',))
            return deepcopy(result)

        def fetch_message(self, sid):
            calls.append(('fetch', sid))
            return deepcopy(result)

        def send(self, *args):
            pytest.fail('Integration review must never submit a provider message')

        def close(self):
            calls.append(('closed',))

    monkeypatch.setattr(provider, 'TwilioProvider', ReadonlyProvider)
    return calls


def test_campaign_resume_requeues_only_unsubmitted_and_preserves_unknown_and_canceled(api_client):
    client = api_client
    login(client, 'reviewer')
    cid, contacts, mids = seed_campaign(states=('blocked', 'unknown', 'canceled'))
    with transaction() as conn:
        conn.execute("UPDATE campaigns SET status='paused',pause_reason='Operator review' WHERE id=?", (cid,))
    response = client.post(f'/api/campaigns/{cid}/resume', json={'review_note': 'Cause reviewed and corrected with evidence'})
    assert response.status_code == 200, response.text
    assert response.json()['requeued'] == 1
    with transaction() as conn:
        assert [r[0] for r in conn.execute('SELECT state FROM messages ORDER BY id')] == ['queued', 'unknown', 'canceled']
        assert conn.execute('SELECT state FROM attempts').fetchone()[0] == 'unknown'
    assert client.post(f'/api/campaigns/{cid}/resume', json={'review_note': 'Same reviewed incident again'}).status_code == 400


def test_campaign_and_recipient_resume_require_reviewer_and_valid_evidence(api_client):
    client = api_client
    login(client, 'operator')
    cid, contacts, mids = seed_campaign()
    for path in (f'/api/campaigns/{cid}/resume', f'/api/contacts/{contacts[0]}/resume-automation'):
        assert client.post(path, json={'review_note': 'Review done with evidence'}).status_code == 403
    login(client, 'reviewer')
    with transaction() as conn:
        engine.process_inbound(conn, {'MessageSid': 'SIM-review-hold', 'AccountSid': 'SIM',
                                     'From': '+12025550110', 'To': '+12025550199',
                                     'Body': 'What does this mean?'})
    response = client.post(f'/api/contacts/{contacts[0]}/resume-automation', json={'review_note': 'Reviewed reply and existing evidence with recipient'})
    assert response.status_code == 200, response.text
    with transaction() as conn:
        assert conn.execute('SELECT reply_hold FROM contacts').fetchone()[0] == 0
        assert conn.execute('SELECT state FROM messages').fetchone()[0] == 'canceled'
        engine.suppress(conn, '+12025550110', 'Recipient opted out')
    response = client.post(f'/api/contacts/{contacts[0]}/resume-automation', json={'review_note': 'Toggle cannot substitute for renewed consent'})
    assert response.status_code == 400
    with transaction() as conn:
        assert conn.execute('SELECT reply_hold FROM contacts').fetchone()[0] == 1
        assert conn.execute('SELECT active FROM suppressions').fetchone()[0] == 1


def test_provider_verification_refresh_preserves_review_but_changed_facts_invalidate(api_client, monkeypatch):
    client = api_client
    login(client)
    saved_credentials(client)
    cid, _, _ = seed_campaign(mode='production')
    facts = verification()
    with transaction() as conn:
        conn.execute('UPDATE credentials SET verification=?,verified_at=?', (json.dumps(facts), now()))
        set_setting(conn, 'readiness', readiness())
        set_setting(conn, 'global_pause', False)
    refreshed = deepcopy(facts)
    refreshed['checked_at'] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    install_readonly_provider(monkeypatch, refreshed)
    assert client.post('/api/twilio/production/verify', json={}).status_code == 200
    with transaction() as conn:
        assert get_setting(conn, 'readiness')['sender_associations_reviewed'] is True
        assert get_setting(conn, 'global_pause') is False
    changed = deepcopy(refreshed)
    changed['authorized_senders'].append('+12025550198')
    install_readonly_provider(monkeypatch, changed)
    assert client.post('/api/twilio/production/verify', json={}).status_code == 200
    with transaction() as conn:
        assert get_setting(conn, 'readiness') == {}
        assert get_setting(conn, 'global_pause') is True
        assert conn.execute('SELECT status FROM campaigns WHERE id=?', (cid,)).fetchone()[0] == 'paused'
        assert json.loads(conn.execute('SELECT verification FROM credentials').fetchone()[0])['authorized_senders'] == changed['authorized_senders']


def test_concurrent_credential_replacement_cannot_accept_old_verification(api_client, monkeypatch):
    client = api_client
    login(client)
    saved_credentials(client)
    entered, release = threading.Event(), threading.Event()
    response = []
    errors = []

    class HeldReadonlyProvider:
        def __init__(self, *args, **kwargs):
            self.credentials = kwargs['credentials']

        def verify(self):
            entered.set()
            assert release.wait(5)
            return verification()

        def close(self):
            pass

    monkeypatch.setattr(provider, 'TwilioProvider', HeldReadonlyProvider)

    def run_verify():
        try:
            response.append(client.post('/api/twilio/production/verify', json={}))
        except BaseException as exc:
            errors.append(exc)

    task = threading.Thread(target=run_verify)
    task.start()
    assert entered.wait(5)
    saved_credentials(client, secret='replacement-secret')
    release.set()
    task.join(5)
    assert not task.is_alive() and not errors
    assert response[0].status_code == 409, response[0].text
    with transaction() as conn:
        current = dict(conn.execute('SELECT * FROM credentials').fetchone())
        assert provider.decrypt_secret(current['secret_encrypted']) == 'replacement-secret'
        assert current['verified_at'] is None and json.loads(current['verification']) == {}
        assert get_setting(conn, 'readiness') == {}
        assert get_setting(conn, 'global_pause') is True


def signed(url, data, token):
    value = url + ''.join(key + data[key] for key in sorted(data))
    return base64.b64encode(hmac.new(token.encode(), value.encode(), hashlib.sha1).digest()).decode()


def test_signed_probe_rolls_back_synthetic_suppression_and_delivery_events(api_client):
    client = api_client
    login(client)
    credentials = saved_credentials(client)
    cid, contacts, mids = seed_campaign()
    with transaction() as conn:
        # A real-looking existing contact at the probe number remains intact.
        conn.execute('UPDATE contacts SET phone=? WHERE id=?', ('+12025550198', contacts[0]))
        set_setting(conn, 'probe_nonce', 'nonce-review-123')
        before = {table: [dict(row) for row in conn.execute('SELECT * FROM ' + table)]
                  for table in ('contacts', 'consents', 'messages', 'attempts', 'inbound',
                                'suppressions', 'suppression_events', 'webhook_events', 'audit_log')}
    data = {'AccountSid': credentials['account_sid'], 'ProbeNonce': 'nonce-review-123'}
    for kind in ('inbound', 'status'):
        route = '/webhooks/twilio/probe/' + kind
        signature = signed('https://testserver' + route, data, credentials['auth_token'])
        response = client.post(route, data=data, headers={'X-Twilio-Signature': signature})
        assert response.status_code == 200, response.text
    with transaction() as conn:
        after = {table: [dict(row) for row in conn.execute('SELECT * FROM ' + table)] for table in before}
        assert before == after
        for key in ('inbound_health_at', 'callback_health_at', 'suppression_health_at'):
            assert get_setting(conn, key)
    data['ProbeNonce'] = 'incorrect-nonce'
    signature = signed('https://testserver/webhooks/twilio/probe/inbound', data, credentials['auth_token'])
    assert client.post('/webhooks/twilio/probe/inbound', data=data, headers={'X-Twilio-Signature': signature}).status_code == 403


def remote_for(mid, status='delivered', price='-0.0075'):
    with transaction() as conn:
        row = dict(conn.execute('SELECT m.*,c.phone FROM messages m JOIN contacts c ON c.id=m.contact_id WHERE m.id=?', (mid,)).fetchone())
    return {'sid': 'SM' + '7' * 32, 'status': status, 'to': row['phone'],
            'from': row['sender'], 'body': row['body'], 'messaging_service_sid': row['service_sid'],
            'date_created': row['claimed_at'], 'price': price, 'error_code': None}


def test_reconciliation_binds_exact_unknown_attempt_without_retry(api_client, monkeypatch):
    client = api_client
    login(client, 'reviewer')
    cid, contacts, mids = seed_campaign(mode='production', states=('unknown',))
    remote = remote_for(mids[0])
    calls = install_readonly_provider(monkeypatch, remote)
    response = client.post(f'/api/messages/{mids[0]}/reconcile', json={'provider_sid': remote['sid'], 'review_note': 'Reviewed exact recipient body sender service and attempt time'})
    assert response.status_code == 200, response.text
    assert response.json()['reconciled'] is True and response.json()['resubmitted'] is False
    assert calls == [('constructed',), ('fetch', remote['sid']), ('closed',)]
    with transaction() as conn:
        row = conn.execute('SELECT state,provider_sid,actual_cost FROM messages').fetchone()
        assert row[:] == ('delivered', remote['sid'], .0075)
        assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0] == 1
        assert get_setting(conn, 'callback_health_at') is None, 'A read-only lookup does not verify actual callback delivery'
    repeated = client.post(f'/api/messages/{mids[0]}/reconcile', json={'provider_sid': remote['sid'], 'review_note': 'Same evidence cannot cause a retry'})
    assert repeated.status_code == 200 and repeated.json()['resubmitted'] is False
    with transaction() as conn:
        assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0] == 1
        assert conn.execute('SELECT state FROM messages').fetchone()[0] == 'delivered'


@pytest.mark.parametrize('field,value', [('to', '+12025550188'), ('from', '+12025550198'),
                                         ('body', 'Different content'), ('messaging_service_sid', 'MG' + '9' * 32),
                                         ('date_created', '2020-01-01T01:00:00+00:00')])
def test_reconciliation_rejects_wrong_attempt_binding(api_client, monkeypatch, field, value):
    client = api_client
    login(client, 'reviewer')
    cid, contacts, mids = seed_campaign(mode='production', states=('unknown',))
    remote = remote_for(mids[0])
    remote[field] = value
    install_readonly_provider(monkeypatch, remote)
    response = client.post(f'/api/messages/{mids[0]}/reconcile', json={'provider_sid': remote['sid'], 'review_note': 'Provider record examined against exact attempt'})
    assert response.status_code == 400, response.text
    with transaction() as conn:
        assert conn.execute('SELECT state,provider_sid FROM messages').fetchone()[:] == ('unknown', None)
        assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0] == 1


def test_reconciliation_can_bind_a_previously_unmatched_callback(api_client, monkeypatch):
    client = api_client
    login(client, 'reviewer')
    cid, contacts, mids = seed_campaign(mode='production', states=('unknown',))
    remote = remote_for(mids[0])
    with transaction() as conn:
        result = engine.process_status(conn, {'MessageSid': remote['sid'], 'MessageStatus': 'delivered'})
        assert result['matched'] is False
    install_readonly_provider(monkeypatch, remote)
    response = client.post(f'/api/messages/{mids[0]}/reconcile', json={'provider_sid': remote['sid'], 'review_note': 'Provider logs bind this previously unmatched callback'})
    assert response.status_code == 200, response.text
    with transaction() as conn:
        assert conn.execute('SELECT state,provider_sid FROM messages').fetchone()[:] == ('delivered', remote['sid'])


def test_reconciliation_refreshes_available_cost_for_duplicate_status(api_client, monkeypatch):
    client = api_client
    login(client, 'reviewer')
    cid, contacts, mids = seed_campaign(mode='production', states=('unknown',))
    remote = remote_for(mids[0], status='sent')
    with transaction() as conn:
        assert engine.process_status(conn, {'MessageSid': remote['sid'], 'MessageStatus': 'sent'}, message_id=mids[0])['applied']
        assert conn.execute('SELECT actual_cost FROM messages').fetchone()[0] is None
    install_readonly_provider(monkeypatch, remote)
    response = client.post(f'/api/messages/{mids[0]}/reconcile', json={'review_note': 'Fetched final known message price from provider ledger'})
    assert response.status_code == 200, response.text
    with transaction() as conn:
        assert conn.execute('SELECT state,actual_cost FROM messages').fetchone()[:] == ('sent', .0075)
