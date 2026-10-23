"""ui-rewamp 界面层测试：服务端标记、CSP（无内联脚本）、主题、书架筛选、动效降级、阅读页外壳不变式。
python3 -m unittest tests.test_ui_rewamp -v
交互部分用 Python Playwright + 系统 Chromium（没有就跳过）。"""
import json, os, pathlib, re, shutil, subprocess, tempfile, unittest

from vibereading.web import views
from tests.test_web import Server, SAMPLE_TXT, SAMPLE_PLAN, HAVE_VERIFY

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "vibereading/web/static"
TPL = (ROOT / "vibereading/template.html").read_text(encoding="utf-8")
try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = os.path.exists(os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"))
except Exception:
    HAVE_PW = False


def book(**kw):
    b = dict(id="b1", verify_json=None, status="done", seconds=1.2, model="plan.json", visibility="private", html_path="x.html", error=None,
             created_at=1_790_000_000, title="祝福", author="鲁迅", char_count=9182, segments=110, plan_json="{}", quality_json=None)
    b.update(kw); return b


USER = {"id": 1, "username": "Q1f730"}


class Client:
    def __init__(self, base): self.base = base; self.cookie = ""
    def req(self, method, path, body=None):
        import urllib.request, urllib.error
        from tests.test_web import DIRECT
        r = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json", "Cookie": self.cookie})
        try: resp = DIRECT.open(r, timeout=60); code = resp.status
        except urllib.error.HTTPError as e: resp = e; code = e.code
        sc = resp.headers.get("Set-Cookie")
        if sc: self.cookie = sc.split(";")[0]
        try: return code, json.loads(resp.read() or b"null")
        except Exception: return code, None
    def wait(self, jid, timeout=180):
        import time
        t = time.time()
        while time.time() - t < timeout:
            _, j = self.req("GET", f"/api/jobs/{jid}")
            if j and j.get("status") in ("done", "failed"): return j
            time.sleep(0.5)
        raise TimeoutError(jid)


class Markup(unittest.TestCase):
    def test_layout_theme_and_no_inline_script(self):
        html = views.layout("t", "<p>x</p>", USER, current="shelf")
        self.assertIn('<script src="/static/theme.js', html)
        self.assertLess(html.index("/static/theme.js"), html.index("/static/app.css"))      # 先定主题再画，避免闪
        self.assertIn('data-theme-toggle', html); self.assertIn('role="switch"', html)
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])               # CSP script-src 'self'：不能有内联脚本
        self.assertIn('aria-current="page"><span>书架</span><svg class="squig"', html)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        dark = views.layout("t", "", USER, dark=True)
        self.assertIn('content="dark" data-fixed', dark)                                       # 暗房页固定暗色，不跟随主题

    def test_auth_tabs_and_split(self):
        h = views.auth("login")
        self.assertIn('data-morph', h); self.assertIn('class="tab-ink"', h); self.assertIn('data-tab="register"', h)
        self.assertIn('<blockquote data-split>', h)
        self.assertIn('id="authform"', h)                                                       # 表单本身不变

    def test_home_hooks(self):
        s = {"first": "a", "rest": "b", "segments": 14, "kb": 58, "plan_kb": 8, "chars": 2600, "scenes": 4, "visuals": 6}
        h = views.home(USER, s, {"mercury": False}, {"mercury": ("Mercury", "x")})
        self.assertIn('<h1 data-split>', h); self.assertIn('data-words', h)
        self.assertIn('id="go"', h); self.assertIn('go-film', h)
        self.assertIn('class="edge"', h)

    def test_shelf_tools_folder_and_skeleton(self):
        bs = [book(id="a"), book(id="b", status="running", html_path=None), book(id="c", status="failed", error="boom", html_path=None)]
        h = views.shelf(USER, bs, {"b": "j1"}, False)
        self.assertIn('id="shelfq"', h); self.assertEqual(h.count('data-filter='), 4)
        self.assertIn('data-filter="failed" aria-selected="false">失败<small>1</small>', h)
        self.assertIn('data-status="running"', h); self.assertEqual(h.count('class="tab"'), 3)
        self.assertEqual(h.count('class="skel"'), 1)                                            # 只有显影中的书有骨架
        self.assertIn('data-slide', h); self.assertIn('id="delacct"', h); self.assertIn('id="delpw"', h)
        self.assertNotIn('id="shelfq"', views.shelf(USER, [], {}, False))                      # 空书架不出工具条

    def test_settings_segmented_keeps_select(self):
        h = views.settings(USER, None, True, {"mercury": True})
        self.assertIn('id="m_kind" name="kind"', h)                                             # 表单值仍来自原 <select>
        self.assertIn('data-for="m_kind"', h); self.assertEqual(h.count('role="radio"'), 3)
        self.assertIn('data-val="openai" aria-checked="true"', h)
        v = {"config": {"kind": "gemini"}, "profile": None, "status": "probing", "tier": None, "error": None, "key": "…x", "has_key": True}
        h2 = views.settings(USER, v, True, {})
        self.assertIn('data-val="gemini" aria-checked="true"', h2); self.assertIn('skel-t', h2)

    def test_quality_bars(self):
        q = {"overall": 88.7, "tier": "A", "floor_ok": True, "scores": {"verbatim": 100.0, "diversity": 64.1, "schema": None},
             "floor": {"verbatim_100": True}, "hero": {"count": 2, "status": {"ok": 1, "rule": 1}}, "seconds": 39.2}
        h = views.quality_page(USER, book(quality_json=json.dumps(q)), q)
        self.assertIn('class="qbar" style="--v:1.000"', h); self.assertIn('--v:0.641', h)
        self.assertIn('class="qhead"', h); self.assertIn('ok 1 / rule 1', h); self.assertNotIn('{"ok"', h)
        self.assertEqual(views._bar(0.5), '<span class="qbar" style="--v:0.500"><b>0.5</b><i aria-hidden="true"></i></span>')
        self.assertEqual(views._bar(None), "—")


class Static(unittest.TestCase):
    def test_js_parses(self):
        if not shutil.which("node"): self.skipTest("node")
        for f in ("app.js", "theme.js"):
            r = subprocess.run(["node", "--check", str(STATIC / f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_external_and_no_copied_framework(self):
        for f in ("app.js", "theme.js", "app.css"):
            t = (STATIC / f).read_text(encoding="utf-8")
            self.assertNotRegex(t, r"https?://(?!www\.w3\.org|rewampui\.com)", f)
            self.assertNotIn("framer-motion", t); self.assertNotIn("import ", t.split("\n", 3)[0])

    def test_reduced_motion_covered(self):
        css = (STATIC / "app.css").read_text(encoding="utf-8"); js = (STATIC / "app.js").read_text(encoding="utf-8")
        tail = css[css.index("ui-rewamp"):]
        self.assertIn("prefers-reduced-motion:reduce", tail)
        self.assertIn(".js-split .ch,.js-split .w{opacity:1", tail)                                # 万一被拆了字，也必须可见
        self.assertIn("if (!reduce) $$('[data-split]')", js)
        self.assertIn(":root[data-theme=dark]", css)


class ReaderShell(unittest.TestCase):
    def test_ids_and_hooks_kept(self):
        for i in ('id="bTheme"', 'id="mTheme"', 'id="bToc"', 'id="mToc"', 'id="toc"', 'id="progress"', 'id="tocPct"'):
            self.assertIn(i, TPL)
        self.assertIn("{{BODY}}", TPL); self.assertIn("{{TOC}}", TPL)
        self.assertIn('<button id="bTheme" aria-pressed="false" aria-label="夜间主题">夜间<span class="sw" aria-hidden="true"></span></button>', TPL)
        self.assertIn("li.cur:not(.chap) a em::before", TPL)                                       # 目录“镜头”
        # 打开目录时只滚目录自己，绝不能 scrollIntoView（会把正文阅读位置也挪走）
        seg = TPL[TPL.index("function openToc"):TPL.index("$('#bToc').onclick")]
        self.assertIn("ol.scrollTo", seg); self.assertNotIn("scrollIntoView", seg)
        self.assertIn("prefers-reduced-motion:reduce){.toc li", TPL)


@unittest.skipUnless(HAVE_PW and HAVE_VERIFY, "需要 Playwright + Chromium")
class Interaction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vr-ui-t-"); cls.srv = Server(cls.tmp).start(); cls.base = f"http://127.0.0.1:{cls.srv.port}"
        c = Client(cls.base); assert c.req("POST", "/api/register", {"username": "uitest", "password": "demo-pass-2026"})[0] == 201
        _, a = c.req("POST", "/api/books", {"name": "k.txt", "text": SAMPLE_TXT, "title": "孔乙己", "author": "鲁迅", "plan": SAMPLE_PLAN, "verify": False}); c.wait(a["job"])
        _, b = c.req("POST", "/api/books", {"name": "d.txt", "text": "第一章\n\n草稿。", "title": "写坏的分镜", "plan": {"scenes": "x"}, "verify": False}); c.wait(b["job"])
        cls.cookie = c.cookie.split("=", 1)

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _ctx(self, pw, br, **kw):
        ctx = br.new_context(viewport={"width": 1280, "height": 860}, **kw)
        ctx.add_cookies([{"name": self.cookie[0], "value": self.cookie[1], "url": self.base}]); return ctx

    def test_shelf_filter_search_theme(self):
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"), args=["--no-sandbox"])
            ctx = self._ctx(pw, br, color_scheme="light"); p = ctx.new_page(); errs = []
            p.on("pageerror", lambda e: errs.append(str(e)))
            p.goto(self.base + "/shelf", wait_until="networkidle")
            vis = lambda: p.eval_on_selector_all(".book", "els => els.filter(e => !e.hidden).length")
            self.assertEqual(vis(), 2)
            p.click("[data-filter=failed]"); self.assertEqual(vis(), 1)
            self.assertEqual(p.get_attribute("[data-filter=failed]", "aria-selected"), "true")
            p.click("[data-filter=all]"); p.fill("#shelfq", "孔乙"); self.assertEqual(vis(), 1)
            p.fill("#shelfq", "不存在的书"); self.assertEqual(vis(), 0); self.assertTrue(p.is_visible("#nores"))
            self.assertEqual(p.get_attribute("html", "data-theme"), "light")
            p.click("[data-theme-toggle]"); self.assertEqual(p.get_attribute("html", "data-theme"), "dark")
            self.assertEqual(p.get_attribute("[data-theme-toggle]", "aria-checked"), "true")
            p.reload(wait_until="networkidle"); self.assertEqual(p.get_attribute("html", "data-theme"), "dark")   # 记住选择
            self.assertEqual(p.evaluate("document.documentElement.scrollWidth - innerWidth"), 0)
            self.assertEqual(errs, []); ctx.close(); br.close()

    def test_reduced_motion_and_os_dark(self):
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"), args=["--no-sandbox"])
            ctx = br.new_context(reduced_motion="reduce", color_scheme="dark"); p = ctx.new_page()
            p.goto(self.base + "/", wait_until="networkidle")
            self.assertEqual(p.locator("h1 .ch").count(), 0)                                         # 不拆字、不做动画
            self.assertEqual(p.get_attribute("html", "data-theme"), "dark")                          # 没有选择时跟随系统
            p.goto(self.base + "/login", wait_until="networkidle")
            self.assertEqual(p.evaluate("getComputedStyle(document.querySelector('.tab-ink')).transitionDuration"), "0s")
            ctx.close()
            ctx = br.new_context(color_scheme="light"); p = ctx.new_page(); p.goto(self.base + "/", wait_until="networkidle")
            self.assertGreater(p.locator("h1 .ch").count(), 10)
            self.assertEqual(p.get_attribute("h1", "aria-label").replace(" ", "")[:6], "把一篇小说，")    # 读屏仍读整句
            ctx.close(); br.close()


if __name__ == "__main__":
    unittest.main()
