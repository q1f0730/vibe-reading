"""SQLite 存储：单文件数据库 + 顺序迁移（PRAGMA user_version 记录版本）。
数据目录：环境变量 VR_DATA_DIR，默认 <仓库>/data（已在 .gitignore 中排除）。"""
import os, pathlib, sqlite3, threading, time

ROOT = pathlib.Path(__file__).resolve().parents[2]


def data_dir():
    d = pathlib.Path(os.environ.get("VR_DATA_DIR") or (ROOT / "data")).resolve()
    if not d.exists():
        d.mkdir(parents=True, mode=0o700)       # 原文与会话都在这里：只给服务用户自己读
    (d / "books").mkdir(parents=True, exist_ok=True)
    return d


def db_path():
    return data_dir() / "vibereading.db"


# 每一项是一次迁移；只追加，不修改已发布的迁移。
MIGRATIONS = [
    # v1：初始结构
    """
    CREATE TABLE users (
        id          INTEGER PRIMARY KEY,
        username    TEXT NOT NULL UNIQUE COLLATE NOCASE,
        email       TEXT UNIQUE COLLATE NOCASE,
        pw_hash     TEXT NOT NULL,
        created_at  REAL NOT NULL
    );
    CREATE TABLE sessions (
        token_hash  TEXT PRIMARY KEY,            -- sha256(cookie token)；明文 token 只在 cookie 里
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  REAL NOT NULL,
        expires_at  REAL NOT NULL,
        ip          TEXT, user_agent TEXT
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE books (
        id          TEXT PRIMARY KEY,            -- 随机不可猜 id，也是分享链接的一部分
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title       TEXT NOT NULL,
        author      TEXT,
        source_name TEXT,
        text        TEXT NOT NULL,               -- 原文
        char_count  INTEGER NOT NULL,
        segments    INTEGER,
        plan_json   TEXT,                        -- 分镜 JSON（生成后写入）
        html_path   TEXT,                        -- 相对数据目录：books/<id>/book.html
        model       TEXT,                        -- mercury / qwen / plan.json
        theme       TEXT NOT NULL DEFAULT 'paper',
        status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
        visibility  TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','unlisted')),
        verify_json TEXT,
        seconds     REAL,
        error       TEXT,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE INDEX books_user ON books(user_id, created_at DESC);
    CREATE TABLE jobs (
        id          TEXT PRIMARY KEY,
        book_id     TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind        TEXT NOT NULL DEFAULT 'build',   -- build / replan / rerender
        status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
        stage       TEXT NOT NULL DEFAULT 'queued',
        pct         REAL NOT NULL DEFAULT 0,
        log         TEXT NOT NULL DEFAULT '[]',      -- JSON 数组
        error       TEXT,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        finished_at REAL
    );
    CREATE INDEX jobs_book ON jobs(book_id, created_at DESC);
    CREATE INDEX jobs_user ON jobs(user_id, status);
    CREATE TABLE login_attempts (
        id   INTEGER PRIMARY KEY,
        key  TEXT NOT NULL,                      -- ip:<addr> / user:<name> / reg:<addr>
        ts   REAL NOT NULL
    );
    CREATE INDEX login_attempts_key ON login_attempts(key, ts);
    """,
    # v2：用户自己的模型（key 只存 AES-GCM 密文）+ 质量报告
    """
    CREATE TABLE model_configs (
        user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        config_json  TEXT NOT NULL,              -- kind/base_url/model/选项（不含 key）
        key_enc      TEXT,                       -- v1:<AES-GCM 密文>，主密钥来自环境变量 VR_SECRET_KEY
        key_hint     TEXT,                       -- 掩码（…abcd），只用于界面提示
        profile_json TEXT,                       -- 能力档案（探针结果）
        tier         TEXT,                       -- A / B / C
        status       TEXT NOT NULL DEFAULT 'probing' CHECK (status IN ('probing','ready','failed')),
        error        TEXT,
        updated_at   REAL NOT NULL
    );
    ALTER TABLE books ADD COLUMN quality_json TEXT;
    ALTER TABLE books ADD COLUMN tier TEXT;
    """,
]

_local = threading.local()


def connect(path=None):
    path = str(path or db_path())
    c = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=15000")
    return c


def conn():
    """每线程一个连接（ThreadingHTTPServer 每请求一个线程，任务也在线程里跑）。"""
    c = getattr(_local, "c", None)
    if c is None or getattr(_local, "path", None) != str(db_path()):
        c = connect(); _local.c = c; _local.path = str(db_path())
    return c


def migrate(c=None):
    c = c or connect()
    v = c.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[v:], start=v + 1):
        c.executescript("BEGIN;" + sql + f";PRAGMA user_version={i};COMMIT;")
    return c.execute("PRAGMA user_version").fetchone()[0]


def recover_interrupted(c=None):
    """服务启动时：上次没跑完的任务一律标记失败（线程已随进程消失）。返回处理条数。"""
    c = c or connect(); now = time.time()
    n = c.execute("UPDATE jobs SET status='failed', stage='failed', error='服务重启，任务中断，请重新生成', "
                  "updated_at=?, finished_at=? WHERE status IN ('queued','running')", (now, now)).rowcount
    c.execute("UPDATE books SET status='failed', error='服务重启，任务中断，请重新生成', updated_at=? "
              "WHERE status IN ('queued','running')", (now,))
    c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    c.execute("DELETE FROM login_attempts WHERE ts < ?", (now - 86400,))
    return n
