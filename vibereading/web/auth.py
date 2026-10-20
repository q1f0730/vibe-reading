"""账号：scrypt 加盐哈希、会话 token（库里只存 sha256）、登录/注册限速。"""
import base64, hashlib, hmac, os, re, secrets, time
from . import db

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1          # ≈32 MiB 内存，单次 ~50–100ms
SESSION_DAYS = 30
COOKIE = "vr_session"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{2,24}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")

# 限速（窗口内失败次数），可用环境变量调
WINDOW = 15 * 60
MAX_FAIL_ACCOUNT = int(os.environ.get("VR_MAX_FAIL_ACCOUNT", 5))
MAX_FAIL_IP = int(os.environ.get("VR_MAX_FAIL_IP", 20))
MAX_REG_IP_HOUR = int(os.environ.get("VR_MAX_REG_IP_HOUR", 10))


def _b64(b): return base64.b64encode(b).decode()


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(h)}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        alg, n, r, p, salt, h = stored.split("$")
        assert alg == "scrypt"
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=len(base64.b64decode(h)))
        return hmac.compare_digest(got, base64.b64decode(h))
    except Exception:
        return False


_DUMMY = None
def _dummy_hash():
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_hex(8))
    return _DUMMY


def _th(token): return hashlib.sha256(token.encode()).hexdigest()


# ---------- 限速 ----------
def _count(key, window):
    return db.conn().execute("SELECT COUNT(*) FROM login_attempts WHERE key=? AND ts>?", (key, time.time() - window)).fetchone()[0]


def _hit(key):
    db.conn().execute("INSERT INTO login_attempts(key, ts) VALUES(?,?)", (key, time.time()))


def login_blocked(ip, account):
    """返回需要等待的秒数（0 = 放行）。"""
    acc = (account or "").strip().lower()
    for key, lim in ((f"user:{acc}", MAX_FAIL_ACCOUNT), (f"ip:{ip}", MAX_FAIL_IP)):
        if _count(key, WINDOW) >= lim:
            oldest = db.conn().execute("SELECT MIN(ts) FROM (SELECT ts FROM login_attempts WHERE key=? AND ts>? ORDER BY ts DESC LIMIT ?)",
                                       (key, time.time() - WINDOW, lim)).fetchone()[0] or time.time()
            return max(1, int(oldest + WINDOW - time.time()))
    return 0


def reg_blocked(ip):
    return _count(f"reg:{ip}", 3600) >= MAX_REG_IP_HOUR


# ---------- 用户 ----------
class AuthError(Exception):
    def __init__(self, msg, code=400, retry_after=None):
        super().__init__(msg); self.code = code; self.retry_after = retry_after


def register(username, password, email=None, ip=""):
    username = (username or "").strip(); email = (email or "").strip() or None
    if not USERNAME_RE.match(username):
        raise AuthError("用户名 2–24 位：字母、数字、下划线、连字符或汉字")
    if email and not EMAIL_RE.match(email):
        raise AuthError("邮箱格式不对")
    if not isinstance(password, str) or len(password) < 8 or len(password) > 256:
        raise AuthError("密码至少 8 位")
    if password.lower() in {username.lower(), (email or "").lower()}:
        raise AuthError("密码不能和用户名/邮箱相同")
    if reg_blocked(ip):
        raise AuthError("注册太频繁，请一小时后再试", 429, 3600)
    c = db.conn()
    if c.execute("SELECT 1 FROM users WHERE username=? OR (email IS NOT NULL AND email=?) OR username=? OR email=?",
                 (username, username, email or "\0", email or "\0")).fetchone():
        raise AuthError("用户名或邮箱已被占用", 409)
    _hit(f"reg:{ip}")
    cur = c.execute("INSERT INTO users(username, email, pw_hash, created_at) VALUES(?,?,?,?)",
                    (username, email, hash_password(password), time.time()))
    return cur.lastrowid


def authenticate(account, password, ip=""):
    account = (account or "").strip()
    wait = login_blocked(ip, account)
    if wait:
        raise AuthError(f"尝试次数过多，请 {max(1, wait // 60)} 分钟后再试", 429, wait)
    row = db.conn().execute("SELECT * FROM users WHERE username=? OR email=?", (account, account)).fetchone() if account else None
    ok = verify_password(password or "", row["pw_hash"] if row else _dummy_hash())
    if not (row and ok):
        _hit(f"user:{account.lower()}"); _hit(f"ip:{ip}")
        raise AuthError("账号或密码不对", 401)
    db.conn().execute("DELETE FROM login_attempts WHERE key=?", (f"user:{account.lower()}",))
    return row["id"]


def create_session(user_id, ip="", ua=""):
    token = secrets.token_urlsafe(32); now = time.time()
    db.conn().execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, ip, user_agent) VALUES(?,?,?,?,?,?)",
                      (_th(token), user_id, now, now + SESSION_DAYS * 86400, ip[:64], (ua or "")[:200]))
    return token


def session_user(token):
    if not token or len(token) > 100:
        return None
    return db.conn().execute("SELECT u.id, u.username, u.email, u.created_at FROM sessions s JOIN users u ON u.id=s.user_id "
                             "WHERE s.token_hash=? AND s.expires_at>?", (_th(token), time.time())).fetchone()


def destroy_session(token):
    if token:
        db.conn().execute("DELETE FROM sessions WHERE token_hash=?", (_th(token),))


def delete_user(user_id, password):
    row = db.conn().execute("SELECT pw_hash FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not verify_password(password or "", row["pw_hash"]):
        raise AuthError("密码不对", 401)
    db.conn().execute("DELETE FROM users WHERE id=?", (user_id,))
