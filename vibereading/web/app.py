"""VibeReading Web：账号 + SQLite 书架 + 持久化任务。标准库 http.server，无框架依赖。
python3 server.py --host 0.0.0.0 --port 3000     （数据目录 VR_DATA_DIR，默认 <仓库>/data）"""
import argparse, json, mimetypes, os, pathlib, re, secrets, sys, time, traceback
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs, quote

from . import db, auth, jobs, views
from .. import planner, segment

ROOT = db.ROOT
STATIC = pathlib.Path(__file__).resolve().parent / "static"
SAMPLE_DIR = ROOT / "examples" / "kongyiji"
MAX_BODY = int(os.environ.get("VR_MAX_BODY", 24_000_000))   # 整本书 txt（≥1 MB）+ JSON 转义余量
MAX_CHARS = int(os.environ.get("VR_MAX_CHARS", 1_500_000))
LABELS = {"mercury": ("快速 · Mercury mercury-3", "短篇约 20–40 秒；整本书按章并发，约 5–15 分钟"),
          "qwen": ("质量 · 千问 qwen3.8-max", "每批 30–50 秒，分镜更稳")}

CSP_APP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
           "connect-src 'self'; font-src 'self'; frame-ancestors 'self'; form-action 'self'; base-uri 'none'; object-src 'none'")
# 阅读页是生成的单文件 HTML（内联脚本/样式）。禁止它发起任何网络请求，cookie 本身是 HttpOnly。
CSP_READER = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
              "font-src data:; connect-src 'none'; form-action 'none'; frame-ancestors 'self'; base-uri 'none'")


def load_sample():
    txt = (SAMPLE_DIR / "kongyiji.txt").read_text(encoding="utf-8")
    plan = json.loads((SAMPLE_DIR / "kongyiji.plan.json").read_text(encoding="utf-8"))
    seg = segment.split(txt)
    first_para = seg["segments"][0]["text"]
    cut = first_para.find("：") + 1 or 18
    return {"text": txt, "plan": plan, "chars": len(re.sub(r"\s", "", txt)), "segments": seg["count"],
            "scenes": len(plan.get("scenes", [])), "visuals": len(plan.get("visuals", [])),
            "kb": round((SAMPLE_DIR / "kongyiji.html").stat().st_size / 1024),
            "plan_kb": max(1, round((SAMPLE_DIR / "kongyiji.plan.json").stat().st_size / 1024)),
            "first": first_para[:cut], "rest": first_para[cut:cut + 120]}


SAMPLE = None


class HTTPError(Exception):
    def __init__(self, code, msg, headers=None):
        super().__init__(msg); self.code = code; self.msg = msg; self.headers = headers or {}


class H(BaseHTTPRequestHandler):
    server_version = "VibeReading"
    sys_version = ""

    # ---------------- plumbing ----------------
    def _ip(self):
        xff = self.headers.get("X-Forwarded-For")
        if xff and os.environ.get("VR_TRUST_PROXY", "1") == "1":
            return xff.split(",")[-1].strip()[:64]          # 最右一跳 = 反代看到的真实来源
        return self.client_address[0]

    def _https(self):
        return (self.headers.get("X-Forwarded-Proto", "").lower() == "https" or os.environ.get("VR_COOKIE_SECURE") == "1")

    def _token(self):
        c = SimpleCookie()
        try: c.load(self.headers.get("Cookie", ""))
        except Exception: return None
        m = c.get(auth.COOKIE)
        return m.value if m else None

    def _user(self):
        if not hasattr(self, "_u"):
            self._u = auth.session_user(self._token())
        return self._u

    def _cookie(self, token, max_age):
        parts = [f"{auth.COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if self._https():
            parts.append("Secure")
        return "; ".join(parts)

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None, csp=CSP_APP):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN"); self.send_header("Content-Security-Policy", csp)
        if self._https():
            self.send_header("Strict-Transport-Security", "max-age=15552000")
        if "Cache-Control" not in (headers or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            for vv in (v if isinstance(v, list) else [v]):
                self.send_header(k, vv)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def _html(self, code, html, headers=None):
        self._send(code, html, "text/html; charset=utf-8", headers)

    def _redirect(self, to, headers=None):
        h = {"Location": to}; h.update(headers or {})
        self._send(302, b"", "text/plain", h)

    def _json_body(self):
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise HTTPError(403, "跨站请求被拒绝")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise HTTPError(415, "需要 application/json")      # 同时挡掉跨站表单 CSRF
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise HTTPError(413, f"请求太大（> {MAX_BODY // 1_000_000} MB）")
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            assert isinstance(body, dict)
            return body
        except Exception:
            raise HTTPError(400, "JSON 格式不对")

    def _need_user(self):
        u = self._user()
        if not u:
            raise HTTPError(401, "请先登录")
        return u

    def _book_for(self, book_id, write=False):
        """读：本人或 unlisted；写：仅本人。别人的私密书 → 403。"""
        b = db.conn().execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not b:
            raise HTTPError(404, "没有这本书")
        u = self._user()
        own = bool(u and u["id"] == b["user_id"])
        if write and not own:
            raise HTTPError(403, "这不是你的书")
        if not own and b["visibility"] != "unlisted":
            raise HTTPError(403, "这本书是私密的")
        return b

    def log_message(self, fmt, *a):
        if os.environ.get("VR_ACCESS_LOG") == "1":
            sys.stderr.write("%s %s\n" % (self._ip(), fmt % a))

    # ---------------- dispatch ----------------
    def do_HEAD(self): self._dispatch("GET")
    def do_GET(self): self._dispatch("GET")
    def do_POST(self): self._dispatch("POST")
    def do_DELETE(self): self._dispatch("DELETE")

    def _dispatch(self, method):
        url = urlsplit(self.path); path = url.path; self.q = parse_qs(url.query)
        is_api = path.startswith("/api/")
        try:
            for m, rx, fn in ROUTES:
                if m == method:
                    mm = rx.fullmatch(path)
                    if mm:
                        return fn(self, *mm.groups())
            raise HTTPError(404, "页面不存在")
        except HTTPError as e:
            if is_api:
                return self._send(e.code, {"error": e.msg}, headers=e.headers)
            if e.code == 401:
                return self._redirect("/login?next=" + quote(path, safe="/"))
            big = {403: "403", 404: "404"}.get(e.code, str(e.code))
            return self._html(e.code, views.message(e.msg, big, "如果这是你的书，请先登录对应账号；分享链接需要书的主人开启“链接可见”。" if e.code == 403
                                                    else "地址可能写错了，或者这本书已经被删除。", self._user(), code=f"ERR {e.code}"), e.headers)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._send(500, {"error": "服务器内部错误"}) if is_api else self._html(500, views.message("服务器内部错误", "500", "稍后再试。", code="ERR 500"))
            except Exception:
                pass

    # ---------------- pages ----------------
    def p_home(self):
        self._html(200, views.layout("VibeReading · 把小说排成会呼吸的长文", views.home(self._user(), SAMPLE, jobs.llm_status(), LABELS), self._user()))

    def p_auth(self, mode):
        nxt = (self.q.get("next") or ["/shelf"])[0]
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/shelf"
        if self._user():
            return self._redirect(nxt)
        title = "登录" if mode == "login" else "注册"
        self._html(200, views.layout(f"{title} · VibeReading", views.auth(mode, nxt=nxt), None, current=mode))

    def p_shelf(self):
        u = self._need_user(); c = db.conn()
        books = c.execute("SELECT * FROM books WHERE user_id=? ORDER BY created_at DESC", (u["id"],)).fetchall()
        jb = {r["book_id"]: r["id"] for r in c.execute(
            "SELECT book_id, id FROM jobs WHERE user_id=? ORDER BY created_at ASC", (u["id"],)).fetchall()}
        self._html(200, views.layout("我的书架 · VibeReading", views.shelf(u, books, jb, any(jobs.llm_status().values())), u, current="shelf"))

    def p_job(self, jid):
        u = self._need_user()
        j = db.conn().execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not j:
            raise HTTPError(404, "没有这个任务")
        if j["user_id"] != u["id"]:
            raise HTTPError(403, "这不是你的任务")
        b = db.conn().execute("SELECT * FROM books WHERE id=?", (j["book_id"],)).fetchone()
        self._html(200, views.layout(f"显影中 ·《{b['title']}》", views.job_page(u, j, b), u, dark=True))

    def p_read(self, book_id):
        b = self._book_for(book_id)
        f = (db.data_dir() / (b["html_path"] or "")).resolve()
        if not b["html_path"] or not f.is_file() or db.data_dir() not in f.parents:
            raise HTTPError(404, "这本书还没有生成好")
        self._send(200, f.read_bytes(), "text/html; charset=utf-8", csp=CSP_READER)

    def p_sample(self):
        self._send(200, (SAMPLE_DIR / "kongyiji.html").read_bytes(), "text/html; charset=utf-8",
                   headers={"Cache-Control": "public, max-age=600"}, csp=CSP_READER)

    def p_static(self, name):
        f = (STATIC / name).resolve()
        if STATIC not in f.parents or not f.is_file():
            raise HTTPError(404, "not found")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype, headers={"Cache-Control": "public, max-age=86400"})

    def p_health(self):
        db.conn().execute("SELECT 1")
        self._send(200, {"ok": True})

    # ---------------- auth api ----------------
    def a_register(self):
        b = self._json_body()
        try:
            uid = auth.register(b.get("username"), b.get("password"), b.get("email"), ip=self._ip())
        except auth.AuthError as e:
            raise HTTPError(e.code, str(e), {"Retry-After": str(e.retry_after)} if e.retry_after else None)
        tok = auth.create_session(uid, self._ip(), self.headers.get("User-Agent", ""))
        self._send(201, {"ok": True, "username": b.get("username").strip()}, headers={"Set-Cookie": self._cookie(tok, auth.SESSION_DAYS * 86400)})

    def a_login(self):
        b = self._json_body()
        try:
            uid = auth.authenticate(b.get("account") or b.get("username"), b.get("password"), ip=self._ip())
        except auth.AuthError as e:
            raise HTTPError(e.code, str(e), {"Retry-After": str(e.retry_after)} if e.retry_after else None)
        old = self._token()
        if old:
            auth.destroy_session(old)
        tok = auth.create_session(uid, self._ip(), self.headers.get("User-Agent", ""))
        self._send(200, {"ok": True}, headers={"Set-Cookie": self._cookie(tok, auth.SESSION_DAYS * 86400)})

    def a_logout(self):
        self._json_body()
        auth.destroy_session(self._token())
        self._send(200, {"ok": True}, headers={"Set-Cookie": self._cookie("", 0)})

    def a_me(self):
        u = self._user()
        self._send(200, {"user": {"id": u["id"], "username": u["username"], "email": u["email"]} if u else None})

    def a_delete_me(self):
        u = self._need_user(); b = self._json_body()
        if jobs.active_job_for_user(u["id"]):
            raise HTTPError(409, "还有任务在运行，等它结束再注销")
        ids = [r["id"] for r in db.conn().execute("SELECT id FROM books WHERE user_id=?", (u["id"],))]
        try:
            auth.delete_user(u["id"], b.get("password"))
        except auth.AuthError as e:
            raise HTTPError(e.code, str(e))
        for i in ids:
            jobs.remove_book_files(i)
        self._send(200, {"ok": True, "deleted_books": len(ids)}, headers={"Set-Cookie": self._cookie("", 0)})

    # ---------------- books api ----------------
    def a_config(self):
        self._send(200, {"llm": jobs.llm_status(), "labels": {k: v[0] for k, v in LABELS.items()}, "max_chars": MAX_CHARS})

    def a_sample(self, name):
        self._send(200, {"text": SAMPLE["text"], "plan": SAMPLE["plan"], "title": "孔乙己", "author": "鲁迅"})

    @staticmethod
    def _book_json(b, job=None):
        v = json.loads(b["verify_json"]) if b["verify_json"] else None
        return {"id": b["id"], "title": b["title"], "author": b["author"], "chars": b["char_count"], "segments": b["segments"],
                "status": b["status"], "visibility": b["visibility"], "model": b["model"], "seconds": b["seconds"],
                "verify": v, "error": b["error"], "created_at": b["created_at"], "updated_at": b["updated_at"],
                "url": f"/b/{b['id']}" if b["html_path"] else None, "job": job}

    def a_books(self):
        u = self._need_user(); c = db.conn()
        rows = c.execute("SELECT * FROM books WHERE user_id=? ORDER BY created_at DESC", (u["id"],)).fetchall()
        self._send(200, {"books": [self._book_json(r) for r in rows]})

    def a_create_book(self):
        u = self._need_user(); b = self._json_body()
        text = b.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPError(400, "原文是空的")
        text = text.replace("\r\n", "\n").lstrip("\ufeff")
        if len(text) > MAX_CHARS:
            raise HTTPError(413, f"原文太长：{len(text):,} 字，上限 {MAX_CHARS:,}")
        plan = b.get("plan")
        if plan is not None and not isinstance(plan, dict):
            raise HTTPError(400, "plan.json 必须是 JSON 对象")
        model = b.get("model") or None
        if not plan:
            try:
                planner.resolve_model(model)
            except ValueError as e:
                raise HTTPError(400, str(e))
            if not planner.llm_available(model):
                raise HTTPError(400, "服务器没有配置模型 key（INCEPTION_API_KEY / DASHSCOPE_API_KEY），请改用 plan.json 模式")
        if jobs.active_job_for_user(u["id"]):
            raise HTTPError(429, "你已有一个任务在运行，等它结束再提交")
        name = str(b.get("name") or "")[:120]
        title = (str(b.get("title") or "").strip() or re.sub(r"\.[^.]+$", "", name) or "未命名")[:80]
        author = str(b.get("author") or "").strip()[:60] or None
        theme = "night" if b.get("theme") == "night" else "paper"
        bid = secrets.token_urlsafe(12); now = time.time()
        db.conn().execute("INSERT INTO books(id, user_id, title, author, source_name, text, char_count, model, theme, status, visibility, created_at, updated_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?, 'queued','private',?,?)",
                          (bid, u["id"], title, author, name, text, len(re.sub(r"\s", "", text)), "plan.json" if plan else (model or planner.DEFAULT_PROVIDER), theme, now, now))
        jid = jobs.start(bid, u["id"], "build", plan=plan, model=model, verify=b.get("verify", True) is not False)
        self._send(201, {"book": bid, "job": jid, "progress": f"/jobs/{jid}"})

    def a_get_book(self, bid):
        b = self._book_for(bid)
        j = db.conn().execute("SELECT id FROM jobs WHERE book_id=? ORDER BY created_at DESC LIMIT 1", (bid,)).fetchone()
        u = self._user()
        self._send(200, self._book_json(b, j["id"] if j and u and u["id"] == b["user_id"] else None))

    def a_rebuild(self, bid):
        u = self._need_user(); b = self._book_for(bid, write=True); body = self._json_body()
        mode = body.get("mode", "rerender")
        if b["status"] in ("queued", "running"):
            raise HTTPError(409, "这本书正在生成")
        if jobs.active_job_for_user(u["id"]):
            raise HTTPError(429, "你已有一个任务在运行")
        if mode == "rerender":
            if not b["plan_json"]:
                raise HTTPError(400, "还没有分镜可复用：首次生成失败的书请重新上传，或改用“重新规划”")
            jid = jobs.start(bid, u["id"], "rerender", verify=True)
        elif mode == "replan":
            model = body.get("model") or (b["model"] if b["model"] and b["model"].split(":")[0] in planner.PROVIDERS else None)
            model = model.split(":")[0] if model else None
            if not planner.llm_available(model):
                raise HTTPError(400, "服务器没有配置模型 key，无法重新规划；可以“沿用分镜重新渲染”")
            jid = jobs.start(bid, u["id"], "replan", model=model, verify=True)
        else:
            raise HTTPError(400, "mode 只能是 rerender / replan")
        self._send(201, {"job": jid, "progress": f"/jobs/{jid}"})

    def a_visibility(self, bid):
        self._need_user(); b = self._book_for(bid, write=True); body = self._json_body()
        vis = body.get("visibility")
        if vis not in ("private", "unlisted"):
            raise HTTPError(400, "visibility 只能是 private / unlisted")
        db.conn().execute("UPDATE books SET visibility=?, updated_at=? WHERE id=?", (vis, time.time(), bid))
        self._send(200, {"ok": True, "visibility": vis, "url": f"/b/{bid}"})

    def a_delete_book(self, bid):
        self._need_user(); b = self._book_for(bid, write=True)
        if b["status"] in ("queued", "running"):
            raise HTTPError(409, "正在生成，结束后才能删除")
        db.conn().execute("DELETE FROM books WHERE id=?", (bid,))
        jobs.remove_book_files(bid)
        self._send(200, {"ok": True})

    def a_job(self, jid):
        u = self._need_user()
        j = db.conn().execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not j:
            raise HTTPError(404, "没有这个任务")
        if j["user_id"] != u["id"]:
            raise HTTPError(403, "这不是你的任务")
        b = db.conn().execute("SELECT model, seconds, verify_json FROM books WHERE id=?", (j["book_id"],)).fetchone()
        self._send(200, {"id": j["id"], "book": j["book_id"], "kind": j["kind"], "status": j["status"], "stage": j["stage"],
                         "pct": j["pct"], "log": json.loads(j["log"]), "error": j["error"], "created_at": j["created_at"],
                         "finished_at": j["finished_at"], "model": b["model"] if b else None, "seconds": b["seconds"] if b else None,
                         "verify": json.loads(b["verify_json"]) if b and b["verify_json"] and j["status"] == "done" else None})


ID = r"([A-Za-z0-9_\-]{6,40})"
ROUTES = [(m, re.compile(p), f) for m, p, f in [
    ("GET", r"/(?:index\.html)?", H.p_home),
    ("GET", r"/(login|register)", H.p_auth),
    ("GET", r"/shelf", H.p_shelf),
    ("GET", r"/jobs/" + ID, H.p_job),
    ("GET", r"/b/" + ID, H.p_read),
    ("GET", r"/sample/kongyiji", H.p_sample),
    ("GET", r"/static/([a-z0-9_\-]+\.(?:css|js))", H.p_static),
    ("GET", r"/healthz", H.p_health),
    ("POST", r"/api/register", H.a_register),
    ("POST", r"/api/login", H.a_login),
    ("POST", r"/api/logout", H.a_logout),
    ("GET", r"/api/me", H.a_me),
    ("DELETE", r"/api/me", H.a_delete_me),
    ("GET", r"/api/config", H.a_config),
    ("GET", r"/api/sample/(kongyiji)", H.a_sample),
    ("GET", r"/api/books", H.a_books),
    ("POST", r"/api/books", H.a_create_book),
    ("GET", r"/api/books/" + ID, H.a_get_book),
    ("POST", r"/api/books/" + ID + r"/rebuild", H.a_rebuild),
    ("POST", r"/api/books/" + ID + r"/visibility", H.a_visibility),
    ("DELETE", r"/api/books/" + ID, H.a_delete_book),
    ("GET", r"/api/jobs/" + ID, H.a_job),
]]


def main(argv=None):
    global SAMPLE
    ap = argparse.ArgumentParser(description="VibeReading web")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    v = db.migrate(db.connect())
    n = db.recover_interrupted()
    SAMPLE = load_sample()
    keys = [k for k, ok in jobs.llm_status().items() if ok]
    print(f"VibeReading http://{a.host}:{a.port} · db {db.db_path()} (schema v{v}) · 中断任务标记失败 {n} 个 · "
          f"LLM: {', '.join(keys) if keys else '无（plan.json 模式）'}", flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), H)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
