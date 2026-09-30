import base64, hashlib, hmac
from datetime import datetime,timezone,timedelta
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from blastio.app import app
from blastio.config import settings
from blastio.db import init_db,transaction,now
from blastio.auth import hash_password

@pytest.fixture
def client(tmp_path,monkeypatch):
    for key,value in {'db_path':str(tmp_path/'api.sqlite3'),'encryption_key':Fernet.generate_key().decode(),'public_url':'http://testserver','mode':'simulation','allow_production':False}.items():monkeypatch.setattr(settings,key,value)
    init_db()
    with transaction() as conn:
        for role in ('admin','reviewer','operator','viewer'):conn.execute('INSERT INTO users(email,password_hash,role,created_at) VALUES(?,?,?,?)',(role+'@example.test',hash_password('correct-password-123'),role,now()))
    with TestClient(app) as c:yield c

def login(c,role='admin'):
    r=c.post('/api/login',json={'email':role+'@example.test','password':'correct-password-123'});assert r.status_code==200,r.text
    c.headers.update({'X-CSRF-Token':r.json()['csrf']});return r

def import_contact(c):
    r=c.post('/api/imports/preview',files={'file':('test.csv',b'first_name,phone,street_address,city,state,zip\nAlex,+12025550101,100 Main St,Austin,TX,78701\n','text/csv')},data={'region':'US','mapping':'{}'});assert r.status_code==200,r.text
    assert c.post('/api/imports/'+str(r.json()['id'])+'/commit',json={}).status_code==200
    return c.get('/api/contacts').json()[0]['id']

def verify_recipient(c,cid):
    consent={'business':'101XVC','channel':'sms','purpose':'seller_outreach','source':'signed website consent','occurred_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),'disclosure_version':'v1','evidence_ref':'evidence://signed/form/1'}
    r=c.post(f'/api/contacts/{cid}/consents',json=consent);assert r.status_code==200,r.text
    r=c.post('/api/consents/'+str(r.json()['id'])+'/verify',json={'review_note':'Reviewed signed form and subject-specific permission'});assert r.status_code==200,r.text
    r=c.post(f'/api/contacts/{cid}/timezone',json={'timezone':'UTC','timezone_source':'Recipient confirmed UTC in signed preference record'});assert r.status_code==200,r.text

def test_auth_csrf_and_roles(client):
    c=client;assert c.get('/api/contacts').status_code==401
    r=login(c,'viewer');assert 'httponly' in r.headers['set-cookie'].lower() and 'samesite=strict' in r.headers['set-cookie'].lower()
    assert c.post('/api/templates',json={'name':'Test','body':'test'}).status_code==403
    login(c);c.headers.pop('X-CSRF-Token');assert c.post('/api/templates',json={'name':'Test','body':'test'}).status_code==403
    assert c.get('/api/me').status_code==200

def test_origin_rejection_and_login_throttle(client):
    c=client
    assert c.post('/api/login',json={'email':'admin@example.test','password':'correct-password-123'},headers={'Origin':'https://attacker.test'}).status_code==403
    for _ in range(10):assert c.post('/api/login',json={'email':'admin@example.test','password':'wrong'}).status_code==401
    assert c.post('/api/login',json={'email':'admin@example.test','password':'correct-password-123'}).status_code==429

def test_evidence_preview_approval_clone_and_reimport_suppression(client):
    c=client;login(c);cid=import_contact(c);assert c.get(f'/api/contacts/{cid}').json()['eligibility']
    verify_recipient(c,cid)
    r=c.post('/api/templates',json={'name':'Inquiry','body':'Hi {{first_name}}, this is {{business_name}}. Would you like to discuss {{street_address}}? Reply STOP to unsubscribe'});tid=r.json()['id']
    assert c.post(f'/api/templates/{tid}/approve',json={}).status_code==200
    assert c.post('/api/preview',json={'template_id':tid,'contact_id':cid}).json()['body'].startswith('Hi Alex, this is 101XVC')
    campaign=c.post('/api/campaigns',json={'name':'Test','template_id':tid,'contact_ids':[cid]}).json()['id']
    r=c.post(f'/api/campaigns/{campaign}/validate',json={});assert r.json()['valid'],r.text
    assert c.post(f'/api/campaigns/{campaign}/approve',json={}).status_code==200
    assert c.post(f'/api/contacts/{cid}/suppress',json={'reason':'Recipient opted out'}).status_code==200
    cloned=c.post(f'/api/campaigns/{campaign}/clone',json={}).json()['id']
    result=c.post(f'/api/campaigns/{cloned}/validate',json={}).json();assert not result['valid'] and any('Suppressed' in r for r in result['reasons'])
    import_contact(c);assert any('Suppressed' in r for r in c.get(f'/api/contacts/{cid}').json()['eligibility'])

def creds():return {'environment':'production','account_sid':'AC'+'1'*32,'api_key_sid':'SK'+'2'*32,'api_secret':'test-secret-never-log','auth_token':'a'*32,'service_sid':'MG'+'3'*32}

def test_credentials_encrypted_masked_and_locally_revoked(client):
    c=client;login(c);payload=creds();r=c.post('/api/twilio',json=payload);assert r.status_code==200,r.text
    with transaction() as conn:
        saved=dict(conn.execute('SELECT * FROM credentials').fetchone());assert payload['api_secret'] not in saved['secret_encrypted'] and payload['auth_token'] not in saved['auth_token_encrypted']
    for route in ('/api/settings','/api/audit','/api/evidence/export'):
        data=c.get(route).text;assert payload['api_secret'] not in data and payload['auth_token'] not in data and saved['secret_encrypted'] not in data
    assert c.post('/api/twilio/production/revoke',json={}).status_code==200
    assert c.get('/api/settings').json()['credentials']==[]

def signature(url,data,token):return base64.b64encode(hmac.new(token.encode(),(url+''.join(k+data[k] for k in sorted(data))).encode(),hashlib.sha1).digest()).decode()

def test_webhook_signature_and_duplicate_stop(client):
    c=client;login(c);cid=import_contact(c);payload=creds();assert c.post('/api/twilio',json=payload).status_code==200
    data={'AccountSid':payload['account_sid'],'MessageSid':'SM'+'4'*32,'From':'+12025550101','To':'+12025550199','Body':'STOP','OptOutType':'STOP'}
    assert c.post('/webhooks/twilio/inbound',data=data,headers={'X-Twilio-Signature':'invalid'}).status_code==403
    assert not c.get(f'/api/contacts/{cid}').json()['suppression']
    sig=signature('http://testserver/webhooks/twilio/inbound',data,payload['auth_token'])
    for _ in range(2):
        r=c.post('/webhooks/twilio/inbound',data=data,headers={'X-Twilio-Signature':sig});assert r.status_code==200 and r.text=='<Response/>'
    d=c.get(f'/api/contacts/{cid}').json();assert d['suppression']['active']==1 and len(d['suppression_history'])==1
    assert len(c.get('/api/inbox').json())==1

def test_manual_reply_missing_consent_and_readiness_gates(client):
    c=client;login(c);cid=import_contact(c)
    incoming=c.post('/api/simulation/inbound',json={'phone':'+12025550101','body':'Interested, tell me more'}).json()
    r=c.post('/api/inbox/'+str(incoming['id'])+'/reply',json={'body':'101XVC: Here are the next steps. Reply STOP to unsubscribe'});assert r.status_code==200,r.text
    assert r.json()['blocked']==1 and c.get('/api/messages').json()[0]['state']=='blocked'
    assert c.put('/api/settings',json={'readiness':{'smoke_test_reviewed':True}}).status_code==400

def test_global_pause_requires_admin_review(client):
    c=client;login(c,'operator');assert c.post('/api/operations/pause',json={'reason':'Investigating complaint'}).status_code==200
    assert c.post('/api/operations/resume',json={'review_note':'Review is complete'}).status_code==403
    login(c);assert c.post('/api/operations/resume',json={'review_note':'Reviewed cause and corrected configuration'}).status_code==200
    assert not c.get('/api/settings').json()['global_pause']
