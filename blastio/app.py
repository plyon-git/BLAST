"""Same-origin authenticated operator API. All sends delegate to engine."""
import json
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import settings
from .db import init_db, transaction, connect, audit, now, get_setting, set_setting
from .auth import user_for, check_origin, check_password, make_session, token_hash, DUMMY_PASSWORD_HASH
from . import policy, engine, importer, provider

WRITERS = ('admin','reviewer','operator')
REVIEWERS = ('admin','reviewer')
ADMIN = ('admin',)

class ReviewNote(BaseModel):
    review_note:str=Field(default='',max_length=2000)

@asynccontextmanager
async def lifespan(app):
    if settings.mode not in ('simulation','production'):raise RuntimeError('BLASTIO_MODE must be simulation or production')
    if settings.mode=='production' and (not settings.cookie_secure or not settings.public_url.startswith('https://')):raise RuntimeError('Production requires HTTPS PUBLIC_URL and secure session cookies')
    init_db()
    yield

app = FastAPI(title='101XVC Blastio', version='1.0.0', lifespan=lifespan, docs_url=None, redoc_url=None)

@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.exception_handler(provider.ProviderError)
async def provider_error(request, exc):
    return JSONResponse(status_code=400,content={'detail':exc.detail})

@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse(status_code=400, content={'detail':str(exc)})

@app.exception_handler(sqlite3.IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse(status_code=409, content={'detail':'This change conflicts with an existing record or an immutable version.'})

class Login(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=1024)

@app.post('/api/login')
def login(request: Request, body: Login):
    check_origin(request)
    # A DB-backed throttle avoids trusting spoofable forwarding headers.
    key = 'login_failures:' + body.email.lower()
    with transaction() as conn:
        failures = get_setting(conn,key,[])
        cutoff=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat(timespec='seconds')
        failures=[f for f in failures if f > cutoff]
        if len(failures)>=10:
            raise HTTPException(429,'Too many sign-in attempts; wait 15 minutes')
        user=conn.execute('SELECT * FROM users WHERE email=?',(body.email.lower(),)).fetchone()
        valid=check_password(body.password,user['password_hash']) if user else check_password(body.password,DUMMY_PASSWORD_HASH)
        if not valid:
            set_setting(conn,key,failures+[now()])
        else:
            set_setting(conn,key,[])
            token,csrf=make_session(conn,user['id'])
            audit(conn,user['email'],'login','user',user['id'])
    if not valid:
        raise HTTPException(401,'Email or password is incorrect')
    response=JSONResponse({'user':{'id':user['id'],'email':user['email'],'role':user['role']},'csrf':csrf,'mode':settings.mode,'business_name':settings.business_name})
    response.set_cookie('blastio_session',token,httponly=True,secure=settings.cookie_secure,samesite='strict',max_age=28800,path='/')
    return response

@app.post('/api/logout')
def logout(request: Request):
    user_for(request)
    with transaction() as conn:
        conn.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(request.cookies.get('blastio_session','')),))
    response=JSONResponse({'ok':True});response.delete_cookie('blastio_session',path='/');return response

@app.get('/api/me')
def me(request: Request):
    user=user_for(request);csrf=user.pop('csrf');return {'user':user,'csrf':csrf,'mode':settings.mode,'business_name':settings.business_name}

@app.get('/api/dashboard')
def dashboard(request: Request):
    user_for(request)
    with transaction() as conn:
        counts={r['state']:r['n'] for r in conn.execute('SELECT state,count(*) n FROM messages GROUP BY state')}
        incoming={r['classification']:r['n'] for r in conn.execute('SELECT classification,count(*) n FROM inbound GROUP BY classification')}
        contacts=conn.execute('SELECT id,reply_hold FROM contacts').fetchall()
        oldest=conn.execute("SELECT min(created_at) FROM messages WHERE state='queued'").fetchone()[0]
        costs=conn.execute('SELECT coalesce(sum(estimated_cost),0),sum(actual_cost) FROM messages').fetchone()
        return {'counts':counts,'contacts':len(contacts),'eligible_contacts':sum(not r['reply_hold'] and not policy.eligibility(conn,r['id']) for r in contacts),'properties':conn.execute('SELECT count(*) FROM properties').fetchone()[0], 'inbound':incoming,'estimated_cost':costs[0],'actual_cost':costs[1],'queue_age_seconds':max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(oldest.replace('Z','+00:00'))).total_seconds()) if oldest else 0,'campaigns':[dict(r) for r in conn.execute('SELECT * FROM campaigns ORDER BY id DESC LIMIT 20')],'global_pause':get_setting(conn,'global_pause',False),'pause_reason':get_setting(conn,'pause_reason',''),'mode':settings.mode}

@app.get('/api/contacts')
def contacts(request: Request, search: str='', limit: int=500, offset:int=0):
    user_for(request)
    with transaction() as conn:
        rows=conn.execute('SELECT * FROM contacts WHERE phone LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR EXISTS(SELECT 1 FROM properties p WHERE p.contact_id=contacts.id AND (p.street_address LIKE ? OR p.city LIKE ? OR p.property_id LIKE ?)) ORDER BY id DESC LIMIT ? OFFSET ?',('%'+search[:200]+'%',)*6+(max(1,min(limit,1000)),max(0,offset))).fetchall()
        return [dict(r,eligibility=policy.eligibility(conn,r['id']),properties=[dict(p) for p in conn.execute('SELECT * FROM properties WHERE contact_id=?',(r['id'],))]) for r in rows]

@app.get('/api/properties')
def properties(request: Request):
    user_for(request)
    with transaction() as conn:
        return [dict(r) for r in conn.execute('SELECT p.*,c.phone,c.first_name,c.last_name FROM properties p JOIN contacts c ON c.id=p.contact_id ORDER BY p.id DESC LIMIT 1000')]

@app.get('/api/contacts/{contact_id}')
def contact_detail(request: Request, contact_id: int):
    user_for(request)
    with transaction() as conn:
        row=conn.execute('SELECT * FROM contacts WHERE id=?',(contact_id,)).fetchone()
        if not row: raise HTTPException(404,'Contact not found')
        suppression=conn.execute('SELECT * FROM suppressions WHERE phone=?',(row['phone'],)).fetchone()
        return {'contact':dict(row),'properties':[dict(r) for r in conn.execute('SELECT * FROM properties WHERE contact_id=?',(contact_id,))],'consents':[dict(r) for r in conn.execute('SELECT * FROM consents WHERE contact_id=? ORDER BY id DESC',(contact_id,))],'eligibility':policy.eligibility(conn,contact_id),'suppression':dict(suppression) if suppression else None,'suppression_history':[dict(r) for r in conn.execute('SELECT * FROM suppression_events WHERE phone=? ORDER BY id DESC',(row['phone'],))]}

class Consent(BaseModel):
    business:str=Field(min_length=1,max_length=200)
    channel:str='sms'
    purpose:str='seller_outreach'
    source:str=Field(min_length=1,max_length=1000)
    occurred_at:str
    disclosure_version:str=Field(min_length=1,max_length=200)
    evidence_ref:str=Field(min_length=1,max_length=2000)

@app.post('/api/contacts/{contact_id}/consents')
def create_consent(request: Request, contact_id:int, body:Consent):
    user=user_for(request,WRITERS)
    occurred=datetime.fromisoformat(body.occurred_at.replace('Z','+00:00'))
    if occurred.tzinfo is None or occurred>datetime.now(timezone.utc): raise ValueError('Consent timestamp needs an offset and cannot be in the future')
    if body.channel!='sms': raise ValueError('This workflow records SMS consent')
    with transaction() as conn:
        cur=conn.execute('INSERT INTO consents(contact_id,business,channel,purpose,source,occurred_at,disclosure_version,evidence_ref,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(contact_id,body.business,body.channel,body.purpose,body.source,occurred.isoformat(),body.disclosure_version,body.evidence_ref,'pending',now()))
        audit(conn,user['email'],'consent.submitted','consent',cur.lastrowid)
        return {'id':cur.lastrowid,'status':'pending'}

@app.post('/api/consents/{consent_id}/verify')
def verify_consent(request:Request,consent_id:int,body:ReviewNote|None=None):
    user=user_for(request,REVIEWERS)
    with transaction() as conn:
        result=engine.restore_consent(conn,consent_id,user['email'])
        audit(conn,user['email'],'consent.evidence_review','consent',consent_id,{'review_note':body.review_note if body else ''})
        return result or {'ok':True}

class TimezoneReview(BaseModel):
    timezone:str
    timezone_source:str=Field(min_length=5,max_length=1000)

@app.post('/api/contacts/{contact_id}/timezone')
def set_timezone(request:Request,contact_id:int,body:TimezoneReview):
    user=user_for(request,REVIEWERS)
    try:ZoneInfo(body.timezone)
    except Exception:raise ValueError('Use a valid IANA timezone') from None
    if any(s in body.timezone_source.lower() for s in ('area code','property address','inferred','guess')): raise ValueError('Use documented recipient timezone evidence')
    with transaction() as conn:
        if not conn.execute('SELECT id FROM contacts WHERE id=?',(contact_id,)).fetchone(): raise HTTPException(404,'Contact not found')
        row=conn.execute('SELECT custom_fields FROM contacts WHERE id=?',(contact_id,)).fetchone()
        fields=json.loads(row['custom_fields']);fields.update(timezone_evidence_ref=body.timezone_source,timezone_reviewed_by=user['email'],timezone_reviewed_at=now())
        conn.execute('UPDATE contacts SET timezone=?,timezone_source=?,custom_fields=?,updated_at=? WHERE id=?',(body.timezone,'manual_verified',json.dumps(fields),now(),contact_id))
        audit(conn,user['email'],'timezone.reviewed','contact',contact_id,{'timezone':body.timezone,'source':body.timezone_source})
        return {'ok':True}

class Reason(BaseModel):
    reason:str=Field(min_length=3,max_length=1000)

@app.post('/api/contacts/{contact_id}/suppress')
def suppress_contact(request:Request,contact_id:int,body:Reason):
    user=user_for(request,WRITERS)
    with transaction() as conn:
        contact=conn.execute('SELECT phone FROM contacts WHERE id=?',(contact_id,)).fetchone()
        if not contact: raise HTTPException(404,'Contact not found')
        engine.suppress(conn,contact['phone'],body.reason,user['email']);return {'ok':True}

class Template(BaseModel):
    name:str=Field(min_length=1,max_length=200)
    body:str=Field(min_length=1,max_length=2000)

@app.get('/api/templates')
def templates(request:Request):
    user_for(request)
    with transaction() as conn: return [dict(r) for r in conn.execute('SELECT * FROM templates ORDER BY name,version DESC')]

@app.post('/api/templates')
def create_template(request:Request,body:Template):
    user=user_for(request,WRITERS)
    with transaction() as conn:
        version=conn.execute('SELECT coalesce(max(version),0)+1 FROM templates WHERE name=?',(body.name,)).fetchone()[0]
        cur=conn.execute('INSERT INTO templates(name,version,body,status,created_at) VALUES(?,?,?,?,?)',(body.name,version,body.body,'draft',now()))
        audit(conn,user['email'],'template.version_created','template',cur.lastrowid,{'name':body.name,'version':version})
        return {'id':cur.lastrowid,'version':version,'status':'draft'}

@app.post('/api/templates/{template_id}/approve')
def approve_template(request:Request,template_id:int,body:ReviewNote|None=None):
    user=user_for(request,REVIEWERS)
    with transaction() as conn:
        row=conn.execute('SELECT * FROM templates WHERE id=?',(template_id,)).fetchone()
        if not row:raise HTTPException(404,'Template not found')
        # Validate syntax and final content with explicit synthetic merge values.
        example={k:'Example' for k in ('first_name','last_name','phone')}
        prop={k:'Example' for k in ('street_address','city','state','zip','property_id')}
        text=policy.render(row['body'],example,prop,settings.business_name)
        if settings.business_name.lower() not in text.lower() or 'reply stop to unsubscribe' not in text.lower(): raise ValueError('Identify the business and include Reply STOP to unsubscribe')
        conn.execute("UPDATE templates SET status='approved',approved_by=?,approved_at=? WHERE id=?",(user['email'],now(),template_id))
        audit(conn,user['email'],'template.approved','template',template_id,{'review_note':body.review_note if body else ''});return {'ok':True}

class Preview(BaseModel):
    template_id:int
    contact_id:int
    property_id:int|None=None

@app.post('/api/preview')
def preview(request:Request,body:Preview):
    user_for(request)
    with transaction() as conn:
        tpl=conn.execute('SELECT * FROM templates WHERE id=?',(body.template_id,)).fetchone();contact=conn.execute('SELECT * FROM contacts WHERE id=?',(body.contact_id,)).fetchone()
        if not tpl or not contact:raise HTTPException(404,'Template or contact not found')
        prop=conn.execute('SELECT * FROM properties WHERE id=? AND contact_id=?',(body.property_id,body.contact_id)).fetchone() if body.property_id else conn.execute('SELECT * FROM properties WHERE contact_id=? ORDER BY id LIMIT 1',(body.contact_id,)).fetchone()
        text=policy.render(tpl['body'],dict(contact),dict(prop) if prop else None,settings.business_name)
        return {'body':text,**policy.sms_metrics(text),'eligibility':policy.eligibility(conn,body.contact_id),'property_id':prop['id'] if prop else None,'template_version':tpl['version']}

class Campaign(BaseModel):
    name:str=Field(min_length=1,max_length=200)
    template_id:int
    variant_ids:list[int]=Field(default_factory=list,max_length=10)
    contact_ids:list[int]=Field(default_factory=list,max_length=50000)
    property_ids:dict[str,int]=Field(default_factory=dict)
    mode:str='simulation'
    service_sid:str|None=None
    sender:str|None=None

@app.get('/api/campaigns')
def campaigns(request:Request):
    user_for(request)
    with transaction() as conn:
        return [dict(r,contact_count=conn.execute('SELECT count(*) FROM campaign_contacts WHERE campaign_id=?',(r['id'],)).fetchone()[0], outcomes={x['state']:x['n'] for x in conn.execute('SELECT state,count(*) n FROM messages WHERE campaign_id=? GROUP BY state',(r['id'],))}) for r in conn.execute('SELECT * FROM campaigns ORDER BY id DESC')]

@app.post('/api/campaigns')
def create_campaign(request:Request,body:Campaign):
    user=user_for(request,WRITERS)
    if body.mode not in ('simulation','production'):raise ValueError('Choose simulation or production')
    with transaction() as conn:
        cur=conn.execute('INSERT INTO campaigns(name,template_id,variant_ids,status,mode,service_sid,sender,created_at) VALUES(?,?,?,?,?,?,?,?)',(body.name,body.template_id,json.dumps(body.variant_ids),'draft',body.mode,body.service_sid,body.sender,now()));cid=cur.lastrowid
        for contact_id in set(body.contact_ids):
            pid=body.property_ids.get(str(contact_id))
            prop=conn.execute('SELECT id FROM properties WHERE contact_id=?'+(' AND id=?' if pid else ' ORDER BY id LIMIT 1'),(contact_id,pid) if pid else (contact_id,)).fetchone()
            if pid and not prop:raise ValueError('Property does not belong to its selected contact')
            conn.execute('INSERT INTO campaign_contacts VALUES(?,?,?)',(cid,contact_id,prop['id'] if prop else None))
        audit(conn,user['email'],'campaign.created','campaign',cid);return {'id':cid}

def campaign_validation(conn,cid):
    campaign=conn.execute('SELECT * FROM campaigns WHERE id=?',(cid,)).fetchone()
    if not campaign:raise HTTPException(404,'Campaign not found')
    ids=[campaign['template_id']]+json.loads(campaign['variant_ids']);reasons=[];preview_rows=[]
    for tid in ids:
        tpl=conn.execute('SELECT * FROM templates WHERE id=?',(tid,)).fetchone()
        if not tpl or tpl['status']!='approved':reasons.append(f'Template {tid} needs approval')
    members=conn.execute('SELECT cc.*,c.* FROM campaign_contacts cc JOIN contacts c ON c.id=cc.contact_id WHERE campaign_id=?',(cid,)).fetchall()
    if not members:reasons.append('Select at least one contact')
    for member in members:
        cr=policy.eligibility(conn,member['contact_id'],allow_synthetic=campaign['mode']!='production')
        if member['reply_hold']:cr.append('Recipient replied; review the hold before future automated outreach')
        prop=conn.execute('SELECT * FROM properties WHERE id=?',(member['property_id'],)).fetchone()
        for tid in ids:
            tpl=conn.execute('SELECT * FROM templates WHERE id=?',(tid,)).fetchone()
            if not tpl:continue
            try:
                text=policy.render(tpl['body'],dict(member),dict(prop) if prop else None,settings.business_name)
                metric=policy.sms_metrics(text)
                if metric['segments']>get_setting(conn,'policy',{}).get('max_segments',3):cr.append('Rendered message exceeds segment limit')
                if settings.business_name.lower() not in text.lower() or 'reply stop to unsubscribe' not in text.lower():cr.append('Rendered content needs business identification and default opt-out instruction')
                if len(preview_rows)<20:preview_rows.append({'contact_id':member['contact_id'],'phone':member['phone'],'template_id':tid,'body':text,**metric})
            except ValueError as exc:cr.append(str(exc))
        if cr:reasons.append(f"{member['phone']}: {'; '.join(dict.fromkeys(cr))}")
    if campaign['mode']=='production':
        if settings.mode!='production' or not settings.allow_production:reasons.append('Production sending is disabled by server configuration')
        cred=conn.execute("SELECT * FROM credentials WHERE environment='production'").fetchone()
        if not cred or not cred['verified_at']:reasons.append('Verify production Twilio connection')
        else:
            ver=json.loads(cred['verification'])
            if campaign['service_sid']!=cred['service_sid'] or campaign['service_sid']!=ver.get('service_sid'):reasons.append('Choose the verified Messaging Service')
            if campaign['sender'] not in ver.get('authorized_senders',[]):reasons.append('Choose a verified authorized sender')
            if policy.timestamp(cred['verified_at'])<datetime.now(timezone.utc)-timedelta(seconds=settings.verification_ttl):reasons.append('Refresh stale Twilio connection verification')
            for key in ('account_ok','service_ok','campaign_ok','webhooks_ok'):
                if not ver.get(key):reasons.append('Twilio '+key.replace('_ok','')+' review is incomplete')
        ready=get_setting(conn,'readiness',{})
        if not all(ready.get(k) is True for k in ('advanced_optout_reviewed','sender_associations_reviewed','campaign_content_reviewed','smoke_test_reviewed')) or not ready.get('reviewed_by') or not ready.get('reviewed_at'):reasons.append('Complete documented production readiness review')
        for key in ('inbound_health_at','callback_health_at','suppression_health_at'):
            stamp=get_setting(conn,key)
            if not stamp or policy.timestamp(stamp)<datetime.now(timezone.utc)-timedelta(seconds=settings.health_ttl):reasons.append(key.replace('_at','').replace('_',' ')+' is stale or unavailable')
        if any(m['timezone_source']=='synthetic' for m in members):reasons.append('Synthetic timezone evidence cannot authorize production')
    return {'valid':not reasons,'reasons':reasons,'previews':preview_rows,'contacts':len(members),'dispatch_rechecks_required':True}

@app.post('/api/campaigns/{cid}/validate')
def validate_campaign(request:Request,cid:int):
    user_for(request,WRITERS)
    with transaction() as conn:return campaign_validation(conn,cid)

@app.post('/api/campaigns/{cid}/approve')
def approve_campaign(request:Request,cid:int,body:ReviewNote|None=None):
    user=user_for(request,REVIEWERS)
    with transaction() as conn:
        existing=conn.execute('SELECT status FROM campaigns WHERE id=?',(cid,)).fetchone()
        if existing and existing['status']=='paused':raise ValueError('Use incident review and resume for a paused campaign')
        result=campaign_validation(conn,cid)
        if not result['valid']:raise HTTPException(400,{'message':'Resolve campaign eligibility before approval',**result})
        conn.execute("UPDATE campaigns SET status='approved',approved_by=?,approved_at=? WHERE id=?",(user['email'],now(),cid));audit(conn,user['email'],'campaign.approved','campaign',cid,{'review_note':body.review_note if body else ''});return {'ok':True}

class Schedule(BaseModel):
    scheduled_at:str

@app.post('/api/campaigns/{cid}/schedule')
def schedule_campaign(request:Request,cid:int,body:Schedule):
    user=user_for(request,REVIEWERS)
    at=datetime.fromisoformat(body.scheduled_at.replace('Z','+00:00'))
    if at.tzinfo is None:raise ValueError('Schedule requires a UTC offset')
    if at < datetime.now(timezone.utc)-timedelta(minutes=1):raise ValueError('Schedule cannot be in the past')
    with transaction() as conn:
        campaign=conn.execute('SELECT * FROM campaigns WHERE id=?',(cid,)).fetchone()
        if not campaign or campaign['status']!='approved':raise ValueError('Approve the campaign before scheduling it')
        conn.execute("UPDATE campaigns SET status='scheduled',scheduled_at=? WHERE id=?",(at.astimezone(timezone.utc).isoformat(timespec='seconds'),cid))
        return engine.enqueue_campaign(conn,cid,user['email'])

@app.post('/api/campaigns/{cid}/pause')
def pause_campaign(request:Request,cid:int,body:Reason):
    user=user_for(request,WRITERS)
    with transaction() as conn:
        conn.execute("UPDATE campaigns SET status='paused',pause_reason=? WHERE id=?",(body.reason,cid));audit(conn,user['email'],'campaign.paused','campaign',cid,{'reason':body.reason});return {'ok':True}

@app.post('/api/campaigns/{cid}/clone')
def clone_campaign(request:Request,cid:int):
    user=user_for(request,WRITERS)
    with transaction() as conn:
        row=conn.execute('SELECT * FROM campaigns WHERE id=?',(cid,)).fetchone()
        if not row:raise HTTPException(404,'Campaign not found')
        cur=conn.execute('INSERT INTO campaigns(name,template_id,variant_ids,status,mode,service_sid,sender,created_at) VALUES(?,?,?,?,?,?,?,?)',(row['name']+' copy',row['template_id'],row['variant_ids'],'draft',row['mode'],row['service_sid'],row['sender'],now()))
        conn.execute('INSERT INTO campaign_contacts SELECT ?,contact_id,property_id FROM campaign_contacts WHERE campaign_id=?',(cur.lastrowid,cid));audit(conn,user['email'],'campaign.cloned','campaign',cur.lastrowid,{'source':cid});return {'id':cur.lastrowid}

@app.post('/api/imports/preview')
async def preview_import(request:Request,file:UploadFile=File(...),region:str=Form(''),mapping:str=Form('{}')):
    user=user_for(request,WRITERS)
    content=await file.read(8*1024*1024+1)
    preview=importer.preview_csv(content,file.filename or 'contacts.csv',region or None,json.loads(mapping) or None)
    with transaction() as conn:
        report={k:v for k,v in preview.items() if k not in ('headers','mapping','rows')}
        cur=conn.execute('INSERT INTO import_jobs(filename,headers,rows,mapping,report,state,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',((file.filename or 'contacts.csv')[:200],json.dumps(preview['headers']),json.dumps(preview['rows']),json.dumps(preview['mapping']),json.dumps(report),'preview',user['email'],now()))
        audit(conn,user['email'],'import.previewed','import',cur.lastrowid,{'accepted':preview['accepted'],'rejected':preview['rejected']})
        return {**preview,'id':cur.lastrowid,'rows':preview['rows'][:100],'preview_truncated':len(preview['rows'])>100}

@app.post('/api/imports/{iid}/commit')
def commit_import(request:Request,iid:int):
    user=user_for(request,WRITERS)
    with transaction() as conn:
        row=conn.execute('SELECT * FROM import_jobs WHERE id=?',(iid,)).fetchone()
        if not row or row['state']!='preview':raise ValueError('This import is missing or already committed')
        preview={**json.loads(row['report']),'headers':json.loads(row['headers']),'mapping':json.loads(row['mapping']),'rows':json.loads(row['rows'])}
        result=importer.commit_import(conn,preview,user['email']);conn.execute("UPDATE import_jobs SET state='committed' WHERE id=?",(iid,));return result

@app.get('/api/imports/{iid}/rejections')
def rejected_import(request:Request,iid:int):
    user_for(request)
    with transaction() as conn:
        row=conn.execute('SELECT report FROM import_jobs WHERE id=?',(iid,)).fetchone()
        if not row:raise HTTPException(404,'Import not found')
        data=json.loads(row['report'])['rejections']
        records=[{'row_number':r['row_number'],'reasons':'; '.join(r['reasons']),**r['values']} for r in data]
        return Response(importer.safe_csv(records),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="rejected-rows.csv"'})

@app.get('/api/inbox')
def inbox(request:Request):
    user_for(request)
    with transaction() as conn:return [dict(r) for r in conn.execute('SELECT i.*,c.first_name,c.last_name,c.reply_hold FROM inbound i LEFT JOIN contacts c ON c.id=i.contact_id ORDER BY i.id DESC LIMIT 1000')]

class InboxUpdate(BaseModel):
    owner:str|None=Field(default=None,max_length=200)
    lead_status:str=Field(default='new',max_length=100)

@app.patch('/api/inbox/{iid}')
def update_inbox(request:Request,iid:int,body:InboxUpdate):
    user=user_for(request,WRITERS)
    if body.lead_status not in ('new','review','suppressed','interested','qualified','not_interested','complaint','wrong_number','closed'):raise ValueError('Select a valid lead status')
    with transaction() as conn:
        row=conn.execute('SELECT * FROM inbound WHERE id=?',(iid,)).fetchone()
        if not row:raise HTTPException(404,'Conversation not found')
        conn.execute('UPDATE inbound SET owner=?,lead_status=? WHERE id=?',(body.owner,body.lead_status,iid))
        if body.lead_status in ('complaint','wrong_number'):engine.suppress(conn,row['from_phone'],body.lead_status,user['email'])
        audit(conn,user['email'],'inbox.updated','inbound',iid,body.model_dump());return {'ok':True}

class Reply(BaseModel):
    body:str=Field(min_length=1,max_length=2000)

@app.post('/api/inbox/{iid}/reply')
def reply(request:Request,iid:int,body:Reply):
    user=user_for(request,REVIEWERS)
    if '{{' in body.body:raise ValueError('Manual replies must contain final text, without merge fields')
    if settings.business_name.lower() not in body.body.lower() or 'reply stop to unsubscribe' not in body.body.lower():raise ValueError('Include business identification and Reply STOP to unsubscribe')
    with transaction() as conn:
        row=conn.execute('SELECT * FROM inbound WHERE id=?',(iid,)).fetchone()
        if not row or not row['contact_id']:raise ValueError('Import and verify consent for this recipient before replying')
        cred=conn.execute('SELECT service_sid FROM credentials WHERE environment=?',(settings.mode,)).fetchone()
        sid=cred['service_sid'] if cred else None
        tpl=conn.execute('INSERT INTO templates(name,version,body,status,approved_by,approved_at,created_at) VALUES(?,?,?,?,?,?,?)',(f'Reply {iid} {secrets.token_hex(4)}',1,body.body,'approved',user['email'],now(),now())).lastrowid
        cid=conn.execute('INSERT INTO campaigns(name,template_id,status,mode,service_sid,sender,scheduled_at,approved_by,approved_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(f'Reply to conversation {iid}',tpl,'scheduled',settings.mode,sid,row['to_phone'],now(),user['email'],now(),now())).lastrowid
        conn.execute('INSERT INTO campaign_contacts VALUES(?,?,NULL)',(cid,row['contact_id']))
        audit(conn,user['email'],'manual_reply.approved','campaign',cid)
        return engine.enqueue_campaign(conn,cid,user['email'],kind='manual')

@app.get('/api/messages')
def messages(request:Request):
    user_for(request)
    with transaction() as conn:return [dict(r,attempts=[dict(a) for a in conn.execute('SELECT * FROM attempts WHERE message_id=? ORDER BY id',(r['id'],))]) for r in conn.execute('SELECT m.*,c.phone FROM messages m JOIN contacts c ON c.id=m.contact_id ORDER BY m.id DESC LIMIT 1000')]

@app.get('/api/audit')
def audit_history(request:Request):
    user_for(request)
    with transaction() as conn:return [dict(r) for r in conn.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT 1000')]

@app.get('/api/evidence/export')
def export_evidence(request:Request):
    user=user_for(request,REVIEWERS)
    with transaction() as conn:
        data={table:[dict(r) for r in conn.execute('SELECT * FROM '+table)] for table in ('contacts','properties','consents','suppressions','suppression_events','campaigns','templates','messages','attempts','inbound','audit_log')}
        data['exported_at']=now();audit(conn,user['email'],'evidence.exported','business',settings.business_name)
        return JSONResponse(data,headers={'Content-Disposition':'attachment; filename="blastio-evidence.json"'})

@app.get('/api/settings')
def get_settings(request:Request):
    user_for(request)
    with transaction() as conn:
        creds=[]
        for env in ('simulation','production'):
            row=conn.execute('SELECT * FROM credentials WHERE environment=?',(env,)).fetchone()
            if row:
                creds.append({'environment':env,'account_sid':row['account_sid'],'api_key_sid':row['api_key_sid'],'service_sid':row['service_sid'],'api_secret':'********','auth_token':'********','verification':json.loads(row['verification']),'verified_at':row['verified_at']})
        return {'mode':settings.mode,'business_name':settings.business_name,'global_pause':get_setting(conn,'global_pause',False),'pause_reason':get_setting(conn,'pause_reason',''),'policy':get_setting(conn,'policy',getattr(policy,'DEFAULT_POLICY',{})),'readiness':get_setting(conn,'readiness',{}),'credentials':creds,'health':{k:get_setting(conn,k) for k in ('inbound_health_at','callback_health_at','suppression_health_at')},'production_enabled':settings.mode=='production' and settings.allow_production}

class SettingsUpdate(BaseModel):
    policy:dict=Field(default_factory=dict)
    readiness:dict=Field(default_factory=dict)
    review_note:str=Field(default='',max_length=2000)

@app.put('/api/settings')
def update_settings(request:Request,body:SettingsUpdate):
    user=user_for(request,ADMIN)
    allowed={'window_start':(0,23),'window_end':(1,24),'max_recipient_24h':(1,10),'max_recipient_7d':(1,30),'max_account_daily':(1,100000),'max_campaign_daily':(1,100000),'max_per_minute':(1,60000),'max_daily_cost':(.01,100000),'max_campaign_cost':(.01,100000),'max_segments':(1,10),'max_queue_age_seconds':(60,86400),'max_provider_validity_seconds':(6,300),'health_min_sample':(1,10000),'max_filter_rate':(.001,1),'max_optout_rate':(.001,1)}
    with transaction() as conn:
        current=get_setting(conn,'policy',getattr(policy,'DEFAULT_POLICY',{})).copy()
        for k,v in body.policy.items():
            if k not in allowed or isinstance(v,bool) or not isinstance(v,(int,float)) or not allowed[k][0]<=v<=allowed[k][1]:raise ValueError('Invalid policy setting: '+k)
            if k not in ('max_daily_cost','max_campaign_cost','max_filter_rate','max_optout_rate') and int(v)!=v:raise ValueError('Setting requires a whole number: '+k)
            current[k]=v
        if current.get('window_start',9)>=current.get('window_end',18):raise ValueError('Sending window must end after it starts on the same day')
        ready=get_setting(conn,'readiness',{}).copy()
        for k,v in body.readiness.items():
            if k not in ('advanced_optout_reviewed','sender_associations_reviewed','campaign_content_reviewed','smoke_test_reviewed') or not isinstance(v,bool):raise ValueError('Invalid readiness review')
            ready[k]=v
        if any(body.readiness.values()):
            if len(body.review_note.strip())<10:raise ValueError('Record the evidence and result of the readiness review')
            ready.update(reviewed_by=user['email'],reviewed_at=now(),review_note=body.review_note)
        set_setting(conn,'policy',current);set_setting(conn,'readiness',ready);audit(conn,user['email'],'settings.reviewed','business',settings.business_name,{'policy':current,'readiness':ready});return {'ok':True}

class Credentials(BaseModel):
    environment:str='production'
    account_sid:str
    api_key_sid:str=''
    api_secret:str=Field(default='',max_length=500)
    auth_token:str=Field(default='',max_length=500)
    service_sid:str

@app.post('/api/twilio')
def save_twilio(request:Request,body:Credentials):
    user=user_for(request,ADMIN)
    if body.environment!='production':raise ValueError('Simulation does not require Twilio credentials')
    with transaction() as conn:
        provider.save_credentials(conn,{**body.model_dump(),'api_key_secret':body.api_secret},user['email'],body.environment)
        set_setting(conn,'global_pause',True);set_setting(conn,'pause_reason','Credentials changed; verify connection and review before resuming')
        set_setting(conn,'readiness',{});audit(conn,user['email'],'credentials.replaced','twilio',body.environment);return {'ok':True}

@app.post('/api/twilio/{environment}/verify')
def verify_twilio(request:Request,environment:str):
    user=user_for(request,ADMIN)
    if environment!='production':raise ValueError('Use simulation without a connection')
    with transaction() as conn:
        original=conn.execute('SELECT * FROM credentials WHERE environment=?',(environment,)).fetchone()
        if not original:raise ValueError('Connect credentials before verifying')
        fingerprint=tuple(original[k] for k in ('account_sid','api_key_sid','service_sid','secret_encrypted','auth_token_encrypted'))
        client=provider.TwilioProvider(environment,credentials=provider.load_credentials(conn,environment))
    try:result=client.verify()
    finally:client.close()
    with transaction() as conn:
        current=conn.execute('SELECT * FROM credentials WHERE environment=?',(environment,)).fetchone()
        if not current or tuple(current[k] for k in ('account_sid','api_key_sid','service_sid','secret_encrypted','auth_token_encrypted'))!=fingerprint:raise HTTPException(409,'Credentials changed during verification. Verify the current connection again.')
        previous=json.loads(original['verification'])
        if previous and provider.verification_review_fingerprint(previous)!=provider.verification_review_fingerprint(result):
            set_setting(conn,'readiness',{});set_setting(conn,'global_pause',True);set_setting(conn,'pause_reason','Verified provider configuration changed; repeat the manual association and content review')
            conn.execute("UPDATE campaigns SET status='paused',pause_reason='Provider configuration changed; review required' WHERE mode='production' AND status IN('approved','scheduled')")
        conn.execute('UPDATE credentials SET verification=?,verified_at=? WHERE environment=?',(json.dumps(result),now(),environment));audit(conn,user['email'],'twilio.readonly_verified','twilio',environment,{'checks':result.get('checks',{})})
    return result

@app.post('/api/twilio/{environment}/revoke')
def revoke_twilio(request:Request,environment:str):
    user=user_for(request,ADMIN)
    with transaction() as conn:
        provider.revoke_credentials(conn,user['email'],environment)
        set_setting(conn,'global_pause',True);set_setting(conn,'pause_reason','Local credentials revoked. Revoke the API key in Twilio Console as well.');audit(conn,user['email'],'credentials.revoked_locally','twilio',environment);return {'ok':True,'provider_revocation_required':True}

@app.post('/api/twilio/{environment}/probe')
def probe_twilio(request:Request,environment:str):
    user=user_for(request,ADMIN)
    if environment!='production':raise ValueError('Only production webhook paths need external probes')
    from .health import probe_webhooks
    result=probe_webhooks()
    with transaction() as conn:audit(conn,user['email'],'webhooks.probed','twilio',environment,result)
    return result

@app.post('/api/operations/pause')
def global_pause(request:Request,body:Reason):
    user=user_for(request,WRITERS)
    with transaction() as conn:set_setting(conn,'global_pause',True);set_setting(conn,'pause_reason',body.reason);audit(conn,user['email'],'global.paused','business',settings.business_name,{'reason':body.reason})
    return {'ok':True}

class Resume(BaseModel):
    review_note:str=Field(min_length=10,max_length=2000)

@app.post('/api/operations/resume')
def resume(request:Request,body:Resume):
    user=user_for(request,ADMIN)
    with transaction() as conn:
        set_setting(conn,'global_pause',False);set_setting(conn,'pause_reason','');audit(conn,user['email'],'global.resumed_after_review','business',settings.business_name,{'note':body.review_note})
    return {'ok':True,'dispatch_gates_still_apply':True}

@app.post('/api/simulation/tick')
def simulation_tick(request:Request):
    user=user_for(request,WRITERS)
    if settings.mode!='simulation':raise ValueError('Simulation runner unavailable in production')
    return {'processed':engine.dispatch_one(provider.SimulationProvider())}

class SimInbound(BaseModel):
    phone:str
    body:str=Field(min_length=1,max_length=2000)

@app.post('/api/simulation/inbound')
def simulate_inbound(request:Request,body:SimInbound):
    user=user_for(request,WRITERS)
    if settings.mode!='simulation':raise ValueError('Synthetic inbound unavailable in production')
    import phonenumbers
    pn=phonenumbers.parse(body.phone,None)
    if not phonenumbers.is_valid_number(pn):raise ValueError('Enter an E.164 phone number')
    phone=phonenumbers.format_number(pn,phonenumbers.PhoneNumberFormat.E164)
    with transaction() as conn:
        result=engine.process_inbound(conn,{'MessageSid':'SIM'+secrets.token_hex(16),'AccountSid':'SIM','From':phone,'To':'+12025550199','Body':body.body})
        audit(conn,user['email'],'simulation.inbound','contact',phone);return result

async def webhook_params(request):
    if len(await request.body())>65536:raise HTTPException(413,'Webhook exceeds size limit')
    form=await request.form()
    # Twilio signs all form fields; preserve repeated values for helper validation.
    return {k:form.getlist(k) if len(form.getlist(k))>1 else form.get(k) for k in form.keys()}

def validate_webhook(request,params):
    with transaction() as conn:
        cred=conn.execute("SELECT * FROM credentials WHERE environment='production'").fetchone()
        if not cred:raise HTTPException(403,'Webhook credentials unavailable')
        token=provider.webhook_auth_token(conn)
        if params.get('AccountSid')!=cred['account_sid']:raise HTTPException(403,'Webhook account does not match')
    url=settings.public_url+request.url.path
    if request.url.query:url+='?'+request.url.query
    if not provider.verify_signature(url,params,request.headers.get('x-twilio-signature',''),token):raise HTTPException(403,'Invalid webhook signature')

@app.post('/webhooks/twilio/inbound')
async def inbound_hook(request:Request):
    params=await webhook_params(request);validate_webhook(request,params)
    try:
        with transaction() as conn:
            engine.process_inbound(conn,params);set_setting(conn,'inbound_health_at',now());set_setting(conn,'suppression_health_at',now())
    except Exception:
        with transaction() as conn:set_setting(conn,'global_pause',True);set_setting(conn,'pause_reason','Inbound processing failed; review before resuming')
        raise
    return Response('<Response/>',media_type='application/xml')

@app.post('/webhooks/twilio/status')
async def status_hook(request:Request,message_id:int|None=None):
    params=await webhook_params(request);validate_webhook(request,params)
    with transaction() as conn:
        result=engine.process_status(conn,params,message_id=message_id);set_setting(conn,'callback_health_at',now());set_setting(conn,'suppression_health_at',now())
    return {'ok':True}

@app.post('/webhooks/twilio/probe/{kind}')
async def probe_hook(request:Request,kind:str):
    if kind not in ('inbound','status'):raise HTTPException(404,'Unknown probe')
    params=await webhook_params(request);validate_webhook(request,params)
    with transaction() as conn:
        nonce=get_setting(conn,'probe_nonce')
        if not nonce or params.get('ProbeNonce')!=nonce:raise HTTPException(403,'Probe challenge is missing or expired')
        # Exercise transaction writes and the real processing functions under a
        # savepoint, then roll back synthetic records. No outbound calls.
        conn.execute('SAVEPOINT probe')
        if kind=='inbound':
            engine.process_inbound(conn,{'MessageSid':'PROBE'+nonce,'From':'+12025550198','To':'+12025550199','Body':'STOP'})
            stored=conn.execute('SELECT active FROM suppressions WHERE phone=?',('+12025550198',)).fetchone()
            if not stored or not stored['active']:raise RuntimeError('Suppression probe did not persist')
        else:
            engine.process_status(conn,{'MessageSid':'PROBE'+nonce,'MessageStatus':'delivered'})
        conn.execute('ROLLBACK TO probe');conn.execute('RELEASE probe')
        set_setting(conn,'inbound_health_at' if kind=='inbound' else 'callback_health_at',now());set_setting(conn,'suppression_health_at',now())
    return {'ok':True,'probe':kind}

static=Path(__file__).parent/'static'
app.mount('/static',StaticFiles(directory=static),name='static')
@app.get('/')
def index():return FileResponse(static/'index.html')
@app.get('/healthz')
def healthz():
    with transaction() as conn:conn.execute('SELECT 1 FROM suppressions LIMIT 1')
    return {'ok':True,'mode':settings.mode}

@app.get('/api/contact-options')
def contact_options(request:Request):
    user_for(request)
    with transaction() as conn:
        rows=[dict(r,properties=[]) for r in conn.execute('SELECT id,phone,first_name,last_name,timezone,reply_hold FROM contacts ORDER BY id LIMIT 50000')]
        by_id={r['id']:r for r in rows}
        for p in conn.execute('SELECT id,contact_id,street_address,city,state,zip,property_id FROM properties ORDER BY id'):
            if p['contact_id'] in by_id:by_id[p['contact_id']]['properties'].append(dict(p))
        for r in rows:r['property_count']=len(r['properties'])
        return rows

@app.post('/api/campaigns/{cid}/resume')
def resume_campaign(request:Request,cid:int,body:Resume):
    user=user_for(request,REVIEWERS)
    with transaction() as conn:
        row=conn.execute('SELECT * FROM campaigns WHERE id=?',(cid,)).fetchone()
        if not row or row['status']!='paused':raise ValueError('Only paused campaigns require incident review')
        result=campaign_validation(conn,cid)
        if not result['valid']:raise HTTPException(400,{'message':'Resolve current eligibility before resuming',**result})
        conn.execute("UPDATE campaigns SET status=?,pause_reason=NULL,approved_by=?,approved_at=? WHERE id=?",('scheduled' if row['scheduled_at'] else 'approved',user['email'],now(),cid))
        retried=0
        for m in conn.execute("SELECT * FROM messages WHERE campaign_id=? AND state='blocked' AND expires_at>? AND NOT EXISTS(SELECT 1 FROM attempts a WHERE a.message_id=messages.id)",(cid,now())).fetchall():
            if not policy.gate(conn,dict(m,state='queued')):
                conn.execute("UPDATE messages SET state='queued',reason=NULL,updated_at=? WHERE id=?",(now(),m['id']));retried+=1
        audit(conn,user['email'],'campaign.resumed_after_review','campaign',cid,{'review_note':body.review_note,'unsubmitted_records_reconsidered':retried})
        return {'ok':True,'requeued':retried,'unknown_attempts_unchanged':True}

@app.post('/api/contacts/{cid}/resume-automation')
def resume_future_automation(request:Request,cid:int,body:Resume):
    user=user_for(request,REVIEWERS)
    with transaction() as conn:
        reasons=policy.eligibility(conn,cid)
        if reasons:raise HTTPException(400,{'message':'Review current consent and recipient evidence first','reasons':reasons})
        conn.execute('UPDATE contacts SET reply_hold=0,updated_at=? WHERE id=?',(now(),cid))
        audit(conn,user['email'],'future_automation.reviewed','contact',cid,{'review_note':body.review_note,'canceled_messages_not_revived':True})
        return {'ok':True,'future_campaigns_only':True}

class Reconciliation(BaseModel):
    provider_sid:str|None=None
    review_note:str=Field(min_length=10,max_length=2000)

@app.post('/api/messages/{mid}/reconcile')
def reconcile_message(request:Request,mid:int,body:Reconciliation):
    user=user_for(request,REVIEWERS)
    with transaction() as conn:
        row=conn.execute('SELECT m.*,c.phone FROM messages m JOIN contacts c ON c.id=m.contact_id WHERE m.id=?',(mid,)).fetchone()
        if not row or row['state'] not in ('unknown','accepted','sent','delivered','failed','filtered'):raise ValueError('This record has no provider outcome to reconcile')
        row=dict(row)
    if row['mode']!='production':raise ValueError('Use synthetic outcomes in the simulation lab')
    sid=row['provider_sid'] or body.provider_sid
    if not sid:return {'reconciled':False,'reason':'No provider SID is known. Review Twilio logs and provide evidence; do not resend.'}
    client=provider.TwilioProvider()
    try:remote=client.fetch_message(sid)
    finally:client.close()
    if remote.get('to')!=row['phone'] or remote.get('body')!=row['body'] or remote.get('messaging_service_sid')!=row['service_sid'] or remote.get('from')!=row['sender']:raise ValueError('Provider record does not match this recipient, exact body, service, and sender')
    if not row['provider_sid']:
        from email.utils import parsedate_to_datetime
        created=remote.get('date_created')
        try:
            dt=parsedate_to_datetime(created) if created and ',' in created else policy.timestamp(created)
            if abs((dt-policy.timestamp(row['claimed_at'])).total_seconds())>300:raise ValueError()
        except (ValueError,TypeError):raise ValueError('Provider timestamp cannot be bound to this attempt') from None
    with transaction() as conn:
        current=conn.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if current['provider_sid'] and current['provider_sid']!=sid:raise HTTPException(409,'A different provider SID is already attached')
        result=engine.process_status(conn,{'MessageSid':sid,'MessageStatus':remote.get('status'),'ErrorCode':remote.get('error_code') or '', 'Price':remote.get('price')},message_id=mid,update_health=False)
        audit(conn,user['email'],'provider_reconciled_readonly','message',mid,{'review_note':body.review_note,'sid':sid,'resubmitted':False})
        return {'reconciled':result.get('applied',False),**result,'resubmitted':False}

class SimStatus(BaseModel):
    message_id:int
    status:str
    error_code:str=''

@app.post('/api/simulation/status')
def simulation_status(request:Request,body:SimStatus):
    user=user_for(request,WRITERS)
    if settings.mode!='simulation':raise ValueError('Synthetic status events are unavailable in production')
    with transaction() as conn:
        row=conn.execute("SELECT * FROM messages WHERE id=? AND mode='simulation'",(body.message_id,)).fetchone()
        if not row or not row['provider_sid']:raise ValueError('Dispatch a simulation message before applying its outcome')
        result=engine.process_status(conn,{'MessageSid':row['provider_sid'],'MessageStatus':body.status,'ErrorCode':body.error_code},message_id=row['id'])
        audit(conn,user['email'],'simulation.status','message',row['id'],{'status':body.status});return result

@app.get('/api/performance')
def performance(request:Request):
    user_for(request)
    with transaction() as conn:
        result={}
        groups=[('by_campaign','m.campaign_id','JOIN campaigns c ON c.id=m.campaign_id','m.campaign_id id,c.name'),('by_template','m.template_id','JOIN templates t ON t.id=m.template_id','m.template_id id,t.name,t.version'),('by_sender','m.sender','','m.sender sender')]
        for name,column,join,fields in groups:
            cols=','.join("sum(m.state='"+state+"') "+state for state in ('queued','sent','delivered','failed','filtered','blocked','unknown','accepted','canceled','expired'))
            rows=[dict(r) for r in conn.execute('SELECT '+fields+',m.mode,count(*) total,'+cols+',sum(m.estimated_cost) estimated_cost,sum(m.actual_cost) actual_cost FROM messages m '+join+' GROUP BY '+column+',m.mode')]
            for r in rows:
                key=r.get('id',r.get('sender'))
                for classification in ('interested','opt_out','negative','complaint','wrong_number','qualified'):r[classification]=0
                matched=conn.execute('SELECT i.classification,i.lead_status FROM inbound i JOIN messages m ON m.id=(SELECT x.id FROM messages x JOIN contacts ct ON ct.id=x.contact_id WHERE ct.phone=i.from_phone AND x.sender=i.to_phone AND x.provider_sid IS NOT NULL AND x.created_at<=i.created_at ORDER BY x.created_at DESC,x.id DESC LIMIT 1) WHERE '+column+' IS ? AND m.mode=?',(key,r['mode'])).fetchall()
                for reply in matched:
                    if reply['classification'] in r:r[reply['classification']]+=1
                    if reply['lead_status']=='qualified' and reply['classification']=='interested':r['qualified']+=1
            result[name]=rows
        result['reply_attribution']='Latest accepted outbound to the recipient on the inbound sender before the reply; unmatched replies omitted.'
        return result
