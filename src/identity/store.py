"""Separate encrypted identity store; access tokens never appear in logs."""
import hashlib
import json
import os
import secrets
import hmac
import base64
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from cryptography.fernet import Fernet


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def future(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None or d <= datetime.now(timezone.utc):
        raise ValueError('authorization_expired_or_timezone_missing')
    return value


def secure_directory(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        actor = subprocess.check_output(['whoami'], text=True).strip()
        result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', actor + ':(OI)(CI)F'], capture_output=True)
        if result.returncode:
            raise RuntimeError('identity_directory_acl_failed')
    else:
        path.chmod(0o700)
    return path


class IdentityStore:
    def __init__(self, directory):
        self.directory = secure_directory(directory)
        key = self.directory / 'encryption.key'
        if not key.exists():
            with key.open('xb') as f:
                f.write(Fernet.generate_key())
            if os.name != 'nt': key.chmod(0o600)
        self.cipher = Fernet(key.read_bytes())
        self.path = self.directory / 'identity.db'
        with self.connection() as c:
            c.executescript(Path(__file__).with_name('migration.sql').read_text())
            c.execute('INSERT OR IGNORE INTO identity_metadata VALUES (?,?)', ('namespace', uid()))
            self.namespace = c.execute("SELECT value FROM identity_metadata WHERE key='namespace'").fetchone()[0]

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA secure_delete=ON')
        try:
            with c: yield c
        finally: c.close()

    def seal(self, value):
        return self.cipher.encrypt(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode())

    def unseal(self, value, raw=False):
        result = self.cipher.decrypt(value)
        return result if raw else json.loads(result)

    def bootstrap(self, actor, role='admin'):
        if role not in {'admin','reviewer','viewer'} or not actor.strip(): raise ValueError('invalid_account')
        token = secrets.token_urlsafe(40)
        with self.connection() as c:
            c.execute('INSERT INTO identity_accounts VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(),actor,role))
        return token

    @staticmethod
    def password_hash(password, *, allow_local_default=False):
        local_default = allow_local_default and password == '123456'
        if not isinstance(password, str) or (len(password) < 10 and not local_default) or len(password) > 256:
            raise ValueError('password_must_be_10_to_256_characters')
        iterations = 600000
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
        return 'pbkdf2_sha256${}${}${}'.format(iterations, base64.urlsafe_b64encode(salt).decode(), base64.urlsafe_b64encode(digest).decode())

    @staticmethod
    def password_matches(password, encoded):
        try:
            scheme, iterations, salt, expected = encoded.split('$', 3)
            if scheme != 'pbkdf2_sha256': return False
            actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), base64.urlsafe_b64decode(salt), int(iterations))
            return hmac.compare_digest(actual, base64.urlsafe_b64decode(expected))
        except (AttributeError, ValueError, TypeError):
            return False

    def create_user(self, username, password, role='teacher', actor='system', must_change=False, *, local_default=False):
        username = username.strip()
        if not username or len(username) > 120 or role not in {'admin', 'teacher', 'reviewer'}:
            raise ValueError('invalid_user')
        if local_default and (username != 'admin' or role != 'admin'):
            raise ValueError('local_default_requires_admin_account')
        user_actor = uid()
        timestamp = now()
        with self.connection() as c:
            try:
                c.execute('INSERT INTO teacher_users VALUES (?,?,?,?,?,?,?,?)', (user_actor, username, self.password_hash(password, allow_local_default=local_default), role, 'active', timestamp, timestamp, int(must_change)))
            except sqlite3.IntegrityError as error:
                raise ValueError('username_already_exists') from error
            self.audit(c, actor, 'user_created', user_actor, {'username': username, 'role': role})
        return {'actor': user_actor, 'username': username, 'role': role, 'status': 'active', 'must_change_password': bool(must_change)}

    def authenticate_password(self, username, password, throttle_key=None):
        username = username.strip()
        key = throttle_key or username.casefold()
        timestamp = now()
        with self.connection() as c:
            from datetime import timedelta
            attempt = c.execute('SELECT * FROM login_attempts WHERE throttle_key=?', (key,)).fetchone()
            clock = datetime.now(timezone.utc)
            if attempt and attempt['locked_until'] and datetime.fromisoformat(attempt['locked_until']) > clock:
                raise PermissionError('login_temporarily_locked')
            fresh = bool(attempt and datetime.fromisoformat(attempt['window_started']) > clock-timedelta(minutes=15))
            row = c.execute('SELECT * FROM teacher_users WHERE username=? COLLATE NOCASE', (username,)).fetchone()
            if row and row['status'] == 'active' and self.password_matches(password, row['password_hash']):
                c.execute('DELETE FROM login_attempts WHERE throttle_key=?', (key,))
                return dict(row)
            if not row: self.password_matches(password, self.password_hash('invalid-user-placeholder'))
            count = int(attempt['fail_count']) + 1 if fresh else 1
            locked = (clock+timedelta(minutes=15)).isoformat() if count >= 8 else None
            started = attempt['window_started'] if fresh else timestamp
            c.execute('INSERT INTO login_attempts VALUES (?,?,?,?) ON CONFLICT(throttle_key) DO UPDATE SET fail_count=excluded.fail_count,window_started=excluded.window_started,locked_until=excluded.locked_until', (key, count, started, locked))
        raise PermissionError('invalid_username_or_password')

    def create_session(self, actor, ttl_hours=8):
        token = secrets.token_urlsafe(48)
        timestamp = now()
        from datetime import timedelta
        expiry = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        with self.connection() as c:
            if not c.execute("SELECT 1 FROM teacher_users WHERE actor=? AND status='active'", (actor,)).fetchone(): raise PermissionError('user_disabled')
            c.execute('INSERT INTO teacher_sessions VALUES (?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), actor, timestamp, expiry, timestamp))
        return token

    def session_account(self, token):
        with self.connection() as c:
            row = c.execute("SELECT u.actor,u.username,u.role,u.must_change_password,s.expires_at FROM teacher_sessions s JOIN teacher_users u ON u.actor=s.actor WHERE s.session_hash=? AND u.status='active'", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if not row: raise PermissionError('login_required')
            if datetime.fromisoformat(row['expires_at']) <= datetime.now(timezone.utc):
                c.execute('DELETE FROM teacher_sessions WHERE session_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)); raise PermissionError('session_expired')
            c.execute('UPDATE teacher_sessions SET last_seen=? WHERE session_hash=?', (now(), hashlib.sha256(token.encode()).hexdigest()))
            result=dict(row);result['must_change_password']=bool(result['must_change_password']);return result

    def revoke_session(self, token):
        with self.connection() as c: c.execute('DELETE FROM teacher_sessions WHERE session_hash=?', (hashlib.sha256((token or '').encode()).hexdigest(),))

    def account(self, token):
        with self.connection() as c:
            row = c.execute('SELECT actor,role FROM identity_accounts WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row: raise PermissionError('identity_login_required')
        return dict(row)

    def audit(self, c, actor, operation, object_id, payload):
        c.execute('INSERT INTO identity_review_history VALUES (?,?,?,?,?,?)', (uid(),actor,operation,object_id,self.seal(payload),now()))

    def subject(self, sid, c=None, active=True):
        if c is None:
            with self.connection() as cx: return self.subject(sid,cx,active)
        row = c.execute('SELECT * FROM registered_subjects WHERE subject_id=?',(sid,)).fetchone()
        if not row: raise ValueError('subject_not_found')
        d = dict(row)
        if active:
            if d['status'] != 'active': raise ValueError('subject_authorization_revoked')
            future(d['expires_at'])
        d['consent_record'] = self.unseal(d['consent_record'])
        d['display_name'] = self.unseal(d['display_name']) if d['display_name'] else ''
        return d

    def purge(self, sid, actor, reference_id=None):
        """Purge ciphertext/features; conservatively invalidate dependent biometric findings."""
        with self.connection() as c:
            self.subject(sid,c,False)
            if reference_id:
                row=c.execute('SELECT subject_id FROM reference_images WHERE reference_image_id=?',(reference_id,)).fetchone()
                if not row or row[0]!=sid: raise ValueError('reference_not_found')
                c.execute("UPDATE reference_images SET image=NULL,feature=NULL,status='deleted' WHERE reference_image_id=?",(reference_id,))
                c.execute("UPDATE identity_bindings SET status='revoked' WHERE subject_id=? AND status='human_confirmed' AND source_kind='authorized_reference_comparison'",(sid,))
            else:
                c.execute("UPDATE registered_subjects SET status='revoked',display_name=NULL,consent_record=? WHERE subject_id=?",(self.seal({'withdrawn':True}),sid))
                c.execute("UPDATE reference_images SET image=NULL,feature=NULL,status='deleted' WHERE subject_id=?",(sid,))
                c.execute("UPDATE identity_bindings SET status='revoked' WHERE subject_id=? AND status='human_confirmed'",(sid,))
            # Scope may contain other authorized volunteers: purge whole affected scope, not an assumed identity.
            c.execute("UPDATE face_observations SET image=NULL,feature=NULL,status='purged' WHERE authorization_id IN (SELECT authorization_id FROM video_authorizations WHERE subject_id=?)",(sid,))
            c.execute("UPDATE video_authorizations SET status='revoked' WHERE subject_id=?",(sid,))
            c.execute("UPDATE identity_candidates SET status='unknown',raw_similarity=NULL,rejection_reason='reference_or_consent_withdrawn' WHERE subject_id=?",(sid,))
            c.execute("UPDATE identity_bindings SET status='revoked' WHERE subject_id=? AND source_kind='authorized_reference_comparison' AND status='human_confirmed'",(sid,))
            self.audit(c,actor,'reference_deleted' if reference_id else 'enrollment_revoked',reference_id or sid,{'subject_id':sid,'biometrics_purged':True})
        with self.connection() as c: c.execute('VACUUM')
