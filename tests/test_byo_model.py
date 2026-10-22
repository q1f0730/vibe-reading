"""Web 端到端：用户自己的模型（本地假模型服务器）——测试按钮、保存（加密）、后台探针分档、用它生成、质量报告只有作者可见、key 不外泄。"""
import json, os, sqlite3, sys, tempfile, time, unittest, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
from tests.test_web import Server, Client, SAMPLE_TXT  # noqa: E402
from tests.mockllm import MockLLM  # noqa: E402

KEY = "sk-byo-PLAINTEXT-SECRET-9876543210"


class EnvServer(Server):
    def __init__(self, data_dir, extra):
        super().__init__(data_dir); self.extra = extra

    def start(self):
        old = {k: os.environ.get(k) for k in self.extra}
        os.environ.update(self.extra)
        try:
            return super().start()
        finally:
            for k, v in old.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v


class BYOModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = MockLLM()
        cls.tmp = tempfile.mkdtemp(prefix="vr-byo-")
        cls.srv = EnvServer(cls.tmp, {"VR_SECRET_KEY": "k" * 48, "VR_ALLOW_PRIVATE_LLM": "1", "VR_ALLOW_HTTP_LLM": "1",
                                      "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost", "VR_LLM_RETRIES": "1"}).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop(); cls.m.close()

    def cfg(self, **kw):
        d = {"kind": "openai", "base_url": self.m.url(), "model": "mock-strong", "api_key": KEY, "label": "假模型"}
        d.update(kw); return d

    def wait_ready(self, c, timeout=60):
        t = time.time()
        while time.time() - t < timeout:
            code, j, *_ = c.req("GET", "/api/model")
            if j["model"] and j["model"]["status"] != "probing":
                return j["model"]
            time.sleep(0.3)
        self.fail("probe did not finish")

    def test_flow(self):
        c = Client(self.srv); self.assertEqual(c.register("byo1")[0], 201)
        # 未登录不能动模型配置
        anon = Client(self.srv)
        self.assertEqual(anon.req("GET", "/api/model")[0], 401)
        # 测试按钮：不保存
        code, j, *_ = c.req("POST", "/api/model/test", self.cfg())
        self.assertEqual(code, 200); self.assertTrue(j["ok"], j)
        self.assertIsNone(c.req("GET", "/api/model")[1]["model"])
        # 拒绝内网地址（未开 VR_ALLOW_PRIVATE_LLM 时）在单元层测；这里测非法类型
        self.assertEqual(c.req("POST", "/api/model", self.cfg(kind="bogus"))[0], 400)
        # 保存 → 后台探针 → A 档
        code, j, *_ = c.req("POST", "/api/model", self.cfg())
        self.assertEqual(code, 200, j); self.assertEqual(j["model"]["status"], "probing")
        mv = self.wait_ready(c)
        self.assertEqual(mv["status"], "ready", mv); self.assertEqual(mv["tier"], "A")
        raw_api = json.dumps(mv, ensure_ascii=False)
        self.assertNotIn(KEY, raw_api); self.assertEqual(mv["key"], "…3210")
        # 库里只有密文
        db = sqlite3.connect(os.path.join(self.tmp, "vibereading.db"))
        enc = db.execute("SELECT key_enc FROM model_configs").fetchone()[0]
        self.assertTrue(enc.startswith("v1:")); self.assertNotIn("PLAINTEXT", enc)
        dump = "\n".join(db.iterdump())
        self.assertNotIn(KEY, dump); self.assertNotIn("PLAINTEXT-SECRET", dump)
        # 设置页
        code, _, html, _ = c.req("GET", "/settings")
        self.assertEqual(code, 200); self.assertNotIn(KEY.encode(), html); self.assertIn("A 档".encode(), html)
        # 用自己的模型生成（不传 model：默认用自己的）
        code, j, *_ = c.req("POST", "/api/books", {"name": "k.txt", "text": SAMPLE_TXT, "title": "孔乙己", "verify": False})
        self.assertEqual(code, 201, j)
        job = c.wait_job(j["job"])
        self.assertEqual(job["status"], "done", job)
        self.assertTrue(any("使用你的模型" in l for l in job["log"]))
        self.assertTrue(any("|quality|" in l for l in job["log"]))
        self.assertTrue(self.m.requests and all(r["headers"].get("Authorization") == "Bearer " + KEY for r in self.m.requests))
        book = c.req("GET", f"/api/books/{j['book']}")[1]
        self.assertTrue(book["model"].startswith("user:")); self.assertIn("quality", book)
        self.assertTrue(book["quality"]["floor_ok"]); self.assertEqual(book["quality"]["tier"], "A")
        # 作者视图：阅读页有质量徽标，报告页可看
        code, _, page, _ = c.req("GET", f"/b/{j['book']}")
        self.assertIn("作者视图".encode(), page)
        self.assertEqual(c.req("GET", f"/b/{j['book']}/quality")[0], 200)
        # 读者（分享链接）看不到徽标，也打不开报告
        c.req("POST", f"/api/books/{j['book']}/visibility", {"visibility": "unlisted"})
        r = Client(self.srv); r.register("reader1")
        code, _, page2, _ = r.req("GET", f"/b/{j['book']}")
        self.assertEqual(code, 200); self.assertNotIn("作者视图".encode(), page2)
        self.assertEqual(r.req("GET", f"/b/{j['book']}/quality")[0], 403)
        self.assertNotIn("quality", r.req("GET", f"/api/books/{j['book']}")[1])
        # 书架有质量行
        self.assertIn("质量".encode(), c.req("GET", "/shelf")[2])
        # 服务端日志里没有明文 key
        log = open(os.path.join(self.tmp, "server.log"), encoding="utf-8", errors="replace").read()
        self.assertNotIn(KEY, log)
        # 删除 → 回到平台默认（无平台 key → 不能用模型生成）
        self.assertEqual(c.req("DELETE", "/api/model", {})[0], 200)
        self.assertIsNone(c.req("GET", "/api/model")[1]["model"])
        self.assertEqual(c.req("POST", "/api/books", {"name": "k.txt", "text": SAMPLE_TXT, "model": "user"})[0], 400)

    def test_errors_never_leak_key(self):
        c = Client(self.srv); c.register("byo2")
        self.m.state["echo_key_on_500"] = True       # 提供方把 key 回显在错误里
        code, j, raw, _ = c.req("POST", "/api/model/test", self.cfg())
        self.assertEqual(code, 200); self.assertFalse(j["ok"])
        self.assertNotIn(KEY, raw.decode()); self.assertNotIn("PLAINTEXT-SECRET", raw.decode())

    def test_weak_model_tier_c(self):
        c = Client(self.srv); c.register("byo3")
        self.m.state.update(weak=True, max_output=900)
        try:
            c.req("POST", "/api/model", self.cfg(model="mock-weak"))
            mv = self.wait_ready(c)
            self.assertEqual(mv["tier"], "C", mv)
        finally:
            self.m.state.clear()


class UrlGuardTest(unittest.TestCase):
    def test_private_urls_rejected(self):
        from vibereading.web import models
        old = os.environ.pop("VR_ALLOW_PRIVATE_LLM", None)
        try:
            for u in ("https://127.0.0.1/v1", "https://10.0.0.5/v1", "https://169.254.169.254/latest", "http://api.example.com/v1"):
                with self.assertRaises(models.ConfigError, msg=u):
                    models.check_public_url(u)
        finally:
            if old is not None: os.environ["VR_ALLOW_PRIVATE_LLM"] = old


if __name__ == "__main__":
    unittest.main()
