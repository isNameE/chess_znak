"""Personal sessions for the plant UI; explicit loopback-only demo access."""
import base64
from datetime import datetime,timedelta,timezone
import hashlib
import hmac
import secrets
import repository
from config import settings


def hash_password(password):
    if len(password)<10:
        raise ValueError('Пароль должен содержать не менее 10 символов.')
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000)
    return 'pbkdf2$600000$'+base64.b64encode(salt).decode()+'$'+base64.b64encode(digest).decode()


def check_password(password,encoded):
    try:
        _,rounds,salt,digest=encoded.split('$')
        actual=hashlib.pbkdf2_hmac('sha256',password.encode(),base64.b64decode(salt),int(rounds))
        return hmac.compare_digest(actual,base64.b64decode(digest))
    except (ValueError,TypeError,AttributeError):return False


def user_for(request):
    token=request.cookies.get('arm_session','')
    if token:
        if len(token)>=256:return None
        with repository.connect() as con:
            row=con.execute('SELECT o.id,o.display_name,o.role FROM user_sessions s JOIN operators o ON o.id=s.operator_id '
                            'WHERE s.token_hash=%s AND s.expires_at>now() AND o.active',
                            (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row:return dict(row,mode='personal')
        return None
    if settings().arm_demo_mode and request.client and request.client.host in ('127.0.0.1','::1','testclient'):
        return {'id':'demo-operator','display_name':'Демо · мастер смены','role':'admin','mode':'demo'}
    return None


def login(username,password):
    with repository.connect() as con:
        row=con.execute('SELECT * FROM operators WHERE id=%s AND active',(username,)).fetchone()
        # Always perform a comparable password derivation, including for unknown users.
        encoded=row['password_hash'] if row and row['password_hash'] else 'pbkdf2$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
        valid=check_password(password,encoded)
        if not row or not row['password_hash'] or not valid:return None
        token=secrets.token_urlsafe(32)
        con.execute('DELETE FROM user_sessions WHERE expires_at<now()')
        con.execute('INSERT INTO user_sessions(token_hash,operator_id,expires_at) VALUES(%s,%s,%s)',
                    (hashlib.sha256(token.encode()).hexdigest(),row['id'],datetime.now(timezone.utc)+timedelta(hours=12)))
        return token
