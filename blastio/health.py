"""Signed public route probes; exercise our ingress/store, not carrier delivery."""
import base64
import hashlib
import hmac
import secrets
import httpx
from .config import settings
from .db import transaction, get_setting, set_setting, now
from .provider import webhook_auth_token, ProviderRejected

def probe_webhooks(client=None):
    if not settings.public_url.startswith('https://'):
        raise ProviderRejected('External webhook probes require the configured HTTPS origin')
    nonce=secrets.token_hex(24)
    with transaction() as conn:
        row=conn.execute("SELECT account_sid FROM credentials WHERE environment='production'").fetchone()
        if not row:raise ProviderRejected('Connect the account before probing webhooks')
        token=webhook_auth_token(conn);account=row['account_sid']
        set_setting(conn,'probe_nonce',nonce)
    result={}
    own=client is None
    client=client or httpx.Client(timeout=8,follow_redirects=False)
    try:
        for kind in ('inbound','status'):
            url=settings.public_url+'/webhooks/twilio/probe/'+kind
            params={'AccountSid':account,'ProbeNonce':nonce}
            data=url+''.join(k+params[k] for k in sorted(params))
            signature=base64.b64encode(hmac.new(token.encode(),data.encode(),hashlib.sha1).digest()).decode()
            try:
                response=client.post(url,data=params,headers={'X-Twilio-Signature':signature})
                result[kind]=response.status_code==200 and response.json().get('ok') is True
            except (httpx.HTTPError,ValueError):result[kind]=False
        with transaction() as conn:
            if not all(result.values()):
                set_setting(conn,'global_pause',True);set_setting(conn,'pause_reason','Signed webhook/store probe failed; investigate before resuming')
            set_setting(conn,'probe_last_result',{'at':now(),**result})
            set_setting(conn,'probe_nonce',None)
    finally:
        if own:client.close()
    return {'ok':all(result.values()),'checks':result,'carrier_delivery_tested':False}
