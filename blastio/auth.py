import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Request
from .db import transaction, now
from .config import settings

def hash_password(password):
    if len(password) < 12:
        raise ValueError('Use a password of at least 12 characters')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return 'scrypt$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()

DUMMY_PASSWORD_HASH = hash_password('dummy-account-timing-only')

def check_password(password, stored):
    try:
        _, salt, expected = stored.split('$')
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def check_origin(request):
    origin = request.headers.get('origin')
    if origin and origin != settings.public_url:
        raise HTTPException(403, 'Request origin does not match configured application URL')

def user_for(request, roles=None):
    token = request.cookies.get('blastio_session', '')
    with transaction() as conn:
        user = conn.execute('SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?', (token_hash(token), now())).fetchone()
    if not user:
        raise HTTPException(401, 'Sign in required')
    user = dict(user)
    user.pop('password_hash', None)
    if request.method not in ('GET','HEAD','OPTIONS'):
        check_origin(request)
        if not hmac.compare_digest(request.headers.get('x-csrf-token',''), user['csrf']):
            raise HTTPException(403, 'Session security token is missing or expired')
    if roles and user['role'] not in roles:
        raise HTTPException(403, 'Your role cannot perform this action')
    return user

def make_session(conn, user_id):
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc)+timedelta(hours=8)).isoformat(timespec='seconds')
    conn.execute('DELETE FROM sessions WHERE expires_at<=?', (now(),))
    conn.execute('INSERT INTO sessions VALUES(?,?,?,?)', (token_hash(token),user_id,csrf,expires))
    return token,csrf
