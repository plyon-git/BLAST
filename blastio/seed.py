"""Synthetic local demo only. Evidence cannot authorize real production outreach."""
import json
from datetime import datetime, timezone, timedelta
from .config import settings
from .db import now, audit

def seed_demo(conn):
    if settings.mode!='simulation':raise ValueError('Synthetic data is available only in simulation')
    if conn.execute('SELECT 1 FROM contacts LIMIT 1').fetchone():raise ValueError('Demo requires an empty contacts database')
    stamp=now();past=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(timespec='seconds')
    people=[('Jordan','Reed','+12025550101','1502 Willow Lane','Austin','TX','78704'),('Avery','Chen','+12025550102','2247 Palm Avenue','Phoenix','AZ','85004'),('Morgan','Davis','+12025550103','801 Maple Street','Raleigh','NC','27601'),('Taylor','Brooks','+12025550104','960 Bay Street','Tampa','FL','33602'),('Riley','James','+12025550105','72 Oak Court','Chicago','IL','60601')]
    for i,(first,last,phone,street,city,state,zip_code) in enumerate(people):
        cid=conn.execute('INSERT INTO contacts(phone,first_name,last_name,timezone,timezone_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(phone,first,last,'America/Denver','synthetic',stamp,stamp)).lastrowid
        conn.execute('INSERT INTO properties(contact_id,property_key,street_address,city,state,zip,property_id,created_at) VALUES(?,?,?,?,?,?,?,?)',(cid,'demo:'+str(i),street,city,state,zip_code,'DEMO-'+str(i+1),stamp))
        if i!=3:
            conn.execute('INSERT INTO consents(contact_id,business,channel,purpose,source,occurred_at,disclosure_version,evidence_ref,status,verified_by,verified_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(cid,settings.business_name,'sms',settings.purpose,'Synthetic demo',past,'demo-v1','simulation://consent/'+str(cid),'verified','demo seed',stamp,stamp))
        if i==4:
            from .engine import suppress
            suppress(conn,phone,'Synthetic opt-out','demo seed')
    tpl=conn.execute('INSERT INTO templates(name,version,body,status,approved_by,approved_at,created_at) VALUES(?,?,?,?,?,?,?)',('Seller inquiry',1,'Hi {{first_name}}, this is {{business_name}}. Would you like to discuss {{street_address}}? Reply STOP to unsubscribe','approved','demo seed',stamp,stamp)).lastrowid
    conn.execute('INSERT INTO templates(name,version,body,status,created_at) VALUES(?,?,?,?,?)',('Seller inquiry',2,'Hi {{first_name}}, {{business_name}} here. Would you like to discuss your property in {{city}}? Reply STOP to unsubscribe','draft',stamp))
    cid=conn.execute('INSERT INTO campaigns(name,template_id,status,mode,sender,created_at) VALUES(?,?,?,?,?,?)',('Austin seller conversations',tpl,'draft','simulation','+12025550199',stamp)).lastrowid
    for r in conn.execute('SELECT c.id,p.id pid FROM contacts c JOIN properties p ON p.contact_id=c.id WHERE c.id<=3'):
        conn.execute('INSERT INTO campaign_contacts VALUES(?,?,?)',(cid,r['id'],r['pid']))
    from .engine import process_inbound
    process_inbound(conn,{'MessageSid':'SIM-DEMO-INBOUND','From':people[1][2],'To':'+12025550199','Body':'Yes, I am interested. Can you share the next steps?'})
    audit(conn,'demo seed','synthetic_demo.created','business',settings.business_name,{'no_real_consent':True})
    return {'contacts':5,'templates':2,'campaigns':1}
