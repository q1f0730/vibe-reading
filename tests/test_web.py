"""VibeReading Web 端到端测试：真实启动 server.py（子进程，临时数据目录，无模型 key）。
python3 -m unittest tests.test_web -v      （plan.json 生成 + 校验需要 node 与 Chromium，见 README）"""
import json, os, pathlib, socket, sqlite3, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLE_TXT = (ROOT / "examples/kongyiji/kongyiji.txt").read_text(encoding="utf-8")
SAMPLE_PLAN = json.loads((ROOT / "examples/kongyiji/kongyiji.plan.json").read_text(encoding="utf-8"))
HAVE_VERIFY = (ROOT / "tools/node_modules/puppeteer-core").exists() and os.path.exists(os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"))


DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 不走环境里的 http_proxy


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Server:
    def __init__(self, data_dir):
        self.data_dir = data_dir; self.port = free_port(); self.proc = None

    def start(self):
        env = {k: v for k, v in os.environ.items() if k not in ("INCEPTION_API_KEY", "DASHSCOPE_API_KEY", "VIBEREADING_GATEWAY")}
        env.update(VR_DATA_DIR=self.data_dir, VR_MAX_REG_IP_HOUR="1000", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(self.port)], env=env,
                                     stdout=open(os.path.join(self.data_dir, "server.log"), "a"), stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                DIRECT.open(f"http://127.0.0.1:{self.port}/healthz", timeout=1); return self
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("server did not start, see " + os.path.join(self.data_dir, "server.log"))

    def stop(self):
        if self.proc:
            self.proc.terminate(); self.proc.wait(10); self.proc = None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None


class Client:
    def __init__(self, srv): self.srv = srv; self.cookie = None

    def req(self, method, path, body=None, ctype="application/json", headers=None):
        data = None; h = dict(headers or {})
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode(); h["Content-Type"] = ctype
        if self.cookie:
            h["Cookie"] = self.cookie
        r = urllib.request.Request(f"http://127.0.0.1:{self.srv.port}{path}", data=data, method=method, headers=h)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        try:
            resp = opener.open(r, timeout=60); code = resp.status
        except urllib.error.HTTPError as e:
            resp = e; code = e.code
        raw = resp.read(); sc = resp.headers.get("Set-Cookie")
        if sc:
            v = sc.split(";")[0]; self.cookie = None if v.endswith("=") else v
        try: j = json.loads(raw)
        except Exception: j = None
        return code, j, raw, resp.headers

    def register(self, name, pw="correct horse 42", email=None):
        return self.req("POST", "/api/register", {"username": name, "password": pw, "email": email})

    def login(self, acc, pw="correct horse 42"):
        return self.req("POST", "/api/login", {"account": acc, "password": pw})

    def build_sample(self, **kw):
        body = {"name": "kongyiji.txt", "text": SAMPLE_TXT, "title": "孔乙己", "author": "鲁迅", "plan": SAMPLE_PLAN, "verify": HAVE_VERIFY}
        body.update(kw)
        return self.req("POST", "/api/books", body)

    def wait_job(self, jid, timeout=180):
        t = time.time()
        while time.time() - t < timeout:
            c, j, *_ = self.req("GET", f"/api/jobs/{jid}")
            if j and j["status"] in ("done", "failed"):
                return j
            time.sleep(0.5)
        raise TimeoutError(jid)


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vrtest-")
        cls.srv = Server(cls.tmp).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    # ---------- 账号 ----------
    def test_01_register_login_logout(self):
        a = Client(self.srv)
        c, j, _, h = a.register("alice", email="alice@example.com")
        self.assertEqual(c, 201, j)
        sc = h["Set-Cookie"]
        self.assertIn("HttpOnly", sc); self.assertIn("SameSite=Lax", sc); self.assertNotIn("Secure", sc)  # http 下不加 Secure
        self.assertEqual(a.req("GET", "/api/me")[1]["user"]["username"], "alice")
        self.assertEqual(Client(self.srv).register("ALICE")[0], 409)              # 用户名大小写不敏感去重
        self.assertEqual(Client(self.srv).register("bob2", pw="short")[0], 400)   # 弱密码
        self.assertEqual(a.req("POST", "/api/logout", {})[0], 200)
        self.assertIsNone(a.req("GET", "/api/me")[1]["user"])
        b = Client(self.srv)
        self.assertEqual(b.login("alice", "wrong-password")[0], 401)
        self.assertEqual(b.login("alice@example.com")[0], 200)                     # 邮箱也能登录
        self.assertEqual(b.req("GET", "/api/me")[1]["user"]["username"], "alice")

    def test_02_password_hashed(self):
        Client(self.srv).register("hashcheck", pw="plaintext-secret-9")
        row = sqlite3.connect(os.path.join(self.tmp, "vibereading.db")).execute("SELECT pw_hash FROM users WHERE username='hashcheck'").fetchone()
        self.assertTrue(row[0].startswith("scrypt$"))
        self.assertNotIn("plaintext-secret-9", row[0])
        h2 = sqlite3.connect(os.path.join(self.tmp, "vibereading.db")).execute("SELECT COUNT(DISTINCT substr(pw_hash, 1, 60)) FROM users").fetchone()[0]
        self.assertGreaterEqual(h2, 1)

    def test_03_login_rate_limit(self):
        Client(self.srv).register("victim")
        x = Client(self.srv)
        codes = [x.login("victim", f"guess{i}")[0] for i in range(6)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)
        c, j, _, h = x.login("victim")                       # 正确密码也被挡住，直到窗口过去
        self.assertEqual(c, 429); self.assertTrue(h.get("Retry-After"))

    def test_04_csrf_form_post_rejected(self):
        a = Client(self.srv); a.register("csrf")
        c, *_ = a.req("POST", "/api/books", b"text=x", ctype="application/x-www-form-urlencoded")
        self.assertEqual(c, 415)
        c, *_ = a.req("POST", "/api/books", {"text": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(c, 403)

    # ---------- 生成 ----------
    def test_05_anonymous_cannot_generate(self):
        anon = Client(self.srv)
        self.assertEqual(anon.build_sample()[0], 401)
        self.assertEqual(anon.req("GET", "/api/books")[0], 401)
        c, _, _, h = anon.req("GET", "/shelf")
        self.assertEqual(c, 302); self.assertTrue(h["Location"].startswith("/login"))
        self.assertEqual(anon.req("GET", "/sample/kongyiji")[0], 200)              # 公开示例可读
        self.assertEqual(anon.req("GET", "/")[0], 200)

    def test_06_no_key_requires_plan(self):
        a = Client(self.srv); a.register("nokey")
        c, j, *_ = a.req("POST", "/api/books", {"text": SAMPLE_TXT, "model": "mercury"})
        self.assertEqual(c, 400); self.assertIn("plan.json", j["error"])
        self.assertFalse(any(a.req("GET", "/api/config")[1]["llm"].values()))

    def test_07_plan_mode_build_and_verify(self):
        a = Client(self.srv); a.register("writer")
        c, j, *_ = a.build_sample()
        self.assertEqual(c, 201, j)
        job = a.wait_job(j["job"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual([l.split("|")[1] for l in job["log"] if "|" in l][:4], ["start", "segment", "plan", "render"])
        if HAVE_VERIFY:
            v = job["verify"]
            self.assertTrue(v["ok"], v); self.assertTrue(v["text_identical"])
            self.assertEqual(v["matched"], v["segments"]); self.assertEqual(v["segments"], 14)   # 100%
        c, _, raw, h = a.req("GET", f"/b/{j['book']}")
        self.assertEqual(c, 200); self.assertIn("connect-src 'none'", h["Content-Security-Policy"])
        self.assertIn("鲁镇的酒店的格局".encode(), raw)
        self.assertEqual(a.req("GET", f"/jobs/{job['id']}")[0], 200)
        c, s, raw, _ = a.req("GET", "/shelf")
        self.assertIn("孔乙己".encode(), raw)
        # 沿用分镜重新渲染
        c, r, *_ = a.req("POST", f"/api/books/{j['book']}/rebuild", {"mode": "rerender"})
        self.assertEqual(c, 201, r)
        self.assertEqual(a.wait_job(r["job"])["status"], "done")
        type(self).writer_cookie = a.cookie; type(self).book = j["book"]; type(self).job = job["id"]

    def test_08_cross_user_access_forbidden(self):
        if not getattr(self, "book", None):
            self.skipTest("needs test_07")
        m = Client(self.srv); m.register("mallory")
        bid = self.book
        self.assertEqual(m.req("GET", f"/b/{bid}")[0], 403)
        self.assertEqual(m.req("GET", f"/api/books/{bid}")[0], 403)
        self.assertEqual(m.req("DELETE", f"/api/books/{bid}")[0], 403)
        self.assertEqual(m.req("POST", f"/api/books/{bid}/rebuild", {"mode": "rerender"})[0], 403)
        self.assertEqual(m.req("POST", f"/api/books/{bid}/visibility", {"visibility": "unlisted"})[0], 403)
        self.assertEqual(m.req("GET", f"/api/jobs/{self.job}")[0], 403)
        self.assertEqual(m.req("GET", f"/jobs/{self.job}")[0], 403)
        self.assertNotIn(bid, json.dumps(m.req("GET", "/api/books")[1]))
        self.assertEqual(Client(self.srv).req("GET", f"/b/{bid}")[0], 403)       # 匿名
        # 主人开启链接分享 → 别人可读但仍不能改
        w = Client(self.srv); w.cookie = self.writer_cookie
        self.assertEqual(w.req("POST", f"/api/books/{bid}/visibility", {"visibility": "unlisted"})[0], 200)
        self.assertEqual(m.req("GET", f"/b/{bid}")[0], 200)
        self.assertEqual(Client(self.srv).req("GET", f"/b/{bid}")[0], 200)
        self.assertEqual(m.req("DELETE", f"/api/books/{bid}")[0], 403)
        self.assertEqual(w.req("POST", f"/api/books/{bid}/visibility", {"visibility": "private"})[0], 200)
        self.assertEqual(m.req("GET", f"/b/{bid}")[0], 403)

    def test_09_restart_keeps_shelf_and_fails_interrupted(self):
        if not getattr(self, "book", None):
            self.skipTest("needs test_07")
        self.srv.stop()
        # 模拟“生成到一半服务挂了”：插入一个 running 任务
        con = sqlite3.connect(os.path.join(self.tmp, "vibereading.db"))
        uid = con.execute("SELECT user_id FROM books WHERE id=?", (self.book,)).fetchone()[0]
        now = time.time()
        con.execute("INSERT INTO books(id,user_id,title,text,char_count,status,created_at,updated_at) VALUES('halfdone01',?,'半成品','x',1,'running',?,?)", (uid, now, now))
        con.execute("INSERT INTO jobs(id,book_id,user_id,status,stage,pct,log,created_at,updated_at) VALUES('halfjob001','halfdone01',?,'running','detail',40,'[]',?,?)", (uid, now, now))
        con.commit(); con.close()
        self.srv.start()
        w = Client(self.srv); w.cookie = self.writer_cookie                       # 会话也持久化在库里
        c, j, *_ = w.req("GET", "/api/books")
        self.assertEqual(c, 200)
        ids = {b["id"]: b for b in j["books"]}
        self.assertEqual(ids[self.book]["status"], "done")
        self.assertEqual(w.req("GET", f"/b/{self.book}")[0], 200)
        self.assertEqual(ids["halfdone01"]["status"], "failed")
        jj = w.req("GET", "/api/jobs/halfjob001")[1]
        self.assertEqual(jj["status"], "failed"); self.assertIn("重启", jj["error"])
        # 重新登录同样能看到
        w2 = Client(self.srv); self.assertEqual(w2.login("writer")[0], 200)
        self.assertIn(self.book, json.dumps(w2.req("GET", "/api/books")[1]))

    def test_10_delete_book_and_account(self):
        a = Client(self.srv); a.register("leaver")
        c, j, *_ = a.build_sample(verify=False)
        self.assertEqual(a.wait_job(j["job"])["status"], "done")
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "books", j["book"])))
        self.assertEqual(a.req("DELETE", "/api/me", {"password": "wrong"})[0], 401)
        c, r, *_ = a.req("DELETE", "/api/me", {"password": "correct horse 42"})
        self.assertEqual(c, 200); self.assertEqual(r["deleted_books"], 1)
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "books", j["book"])))
        self.assertIsNone(a.req("GET", "/api/me")[1]["user"])
        self.assertEqual(Client(self.srv).login("leaver")[0], 401)

    def test_11_pages_render(self):
        a = Client(self.srv)
        for p in ("/", "/login", "/register", "/static/app.css", "/static/app.js"):
            c, _, raw, h = a.req("GET", p)
            self.assertEqual(c, 200, p)
            self.assertNotIn(b"http://", raw.replace(b"http://www.w3.org", b"")) if p.endswith(".css") else None
        self.assertEqual(a.req("GET", "/static/../server.py")[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
