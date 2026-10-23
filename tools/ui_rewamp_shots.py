"""ui-rewamp 截图：在临时数据目录起一个干净服务 + 本地假模型，造数据后用 Playwright 截桌面 1440×900 与手机 390×844 两套。
python3 tools/ui_rewamp_shots.py OUTDIR [--zhufu-plan evals/results/mercury/page.plan.json]

数据：注册测试账号 → 保存“自带模型”（tests/mockllm 假模型，跑完探针得到能力档案）→
  《祝福》（鲁迅，公有领域；evals/texts/zhufu.txt + 一份含关键场景动画的分镜）、《孔乙己》（examples/，链接分享）、一份写坏的分镜（失败）；
  进度页在“校验”阶段截“进行中”。不使用任何版权内容，不碰线上库。
每张图同时检查：横向溢出、外部请求、页面 JS 报错。最后输出 JSON 报告（同时写 OUTDIR/report.json）。
"""
import json, os, pathlib, sys, tempfile, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_web import Server, DIRECT  # noqa: E402
from tests.mockllm import MockLLM  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "out/ui-rewamp"); OUT.mkdir(parents=True, exist_ok=True)
ZPLAN = pathlib.Path(sys.argv[sys.argv.index("--zhufu-plan") + 1]) if "--zhufu-plan" in sys.argv else ROOT / "evals/results/mercury/page.plan.json"
CHROME = os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium")
VIEWS = {"desktop": dict(viewport={"width": 1440, "height": 900}, device_scale_factor=1),
         "mobile": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)}


class Client:
    def __init__(self, base): self.base = base; self.cookie = ""
    def req(self, method, path, body=None):
        r = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json", "Cookie": self.cookie, "Origin": self.base})
        try:
            resp = DIRECT.open(r, timeout=60)
        except urllib.error.HTTPError as e:
            resp = e
        sc = resp.headers.get("Set-Cookie")
        if sc: self.cookie = sc.split(";")[0]
        raw = resp.read()
        try: return resp.status if hasattr(resp, "status") else resp.code, json.loads(raw or b"null")
        except Exception: return resp.code, None
    def wait(self, jid, timeout=240):
        t = time.time()
        while time.time() - t < timeout:
            _, j = self.req("GET", f"/api/jobs/{jid}")
            if j and j.get("status") in ("done", "failed"): return j
            time.sleep(0.6)
        raise TimeoutError(jid)


def main():
    mock = MockLLM()
    tmp = tempfile.mkdtemp(prefix="vr-ui-")
    env_keep = {k: os.environ.get(k) for k in ("VR_SECRET_KEY", "VR_ALLOW_PRIVATE_LLM", "VR_ALLOW_HTTP_LLM", "NO_PROXY", "no_proxy")}
    os.environ.update(VR_SECRET_KEY="k" * 48, VR_ALLOW_PRIVATE_LLM="1", VR_ALLOW_HTTP_LLM="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    srv = Server(tmp).start(); base = f"http://127.0.0.1:{srv.port}"
    rep = {"base": base, "data_dir": tmp, "errors": [], "shots": []}
    try:
        c = Client(base)
        st, _ = c.req("POST", "/api/register", {"username": "Q1f730", "password": "demo-pass-2026"}); assert st == 201, st
        st, _ = c.req("POST", "/api/model", {"kind": "openai", "base_url": mock.url(), "model": "mock-strong", "api_key": "sk-test-" + "x" * 24, "label": "本地假模型"})
        assert st == 200, st
        for _ in range(120):
            _, j = c.req("GET", "/api/model")
            if j and j.get("model") and j["model"]["status"] != "probing": break
            time.sleep(0.5)
        ztxt = (ROOT / "evals/texts/zhufu.txt").read_text(encoding="utf-8"); zplan = json.loads(ZPLAN.read_text(encoding="utf-8"))
        ktxt = (ROOT / "examples/kongyiji/kongyiji.txt").read_text(encoding="utf-8"); kplan = json.loads((ROOT / "examples/kongyiji/kongyiji.plan.json").read_text(encoding="utf-8"))
        _, k = c.req("POST", "/api/books", {"name": "kongyiji.txt", "text": ktxt, "title": "孔乙己", "author": "鲁迅", "plan": kplan, "verify": False}); c.wait(k["job"])
        c.req("POST", f"/api/books/{k['book']}/visibility", {"visibility": "unlisted"})
        _, f = c.req("POST", "/api/books", {"name": "draft.txt", "text": "第一章\n\n这是一份分镜写坏了的草稿。", "title": "一份写坏的分镜", "plan": {"scenes": "oops"}, "verify": False}); c.wait(f["job"])
        _, z = c.req("POST", "/api/books", {"name": "zhufu.txt", "text": ztxt, "title": "祝福", "author": "鲁迅", "plan": zplan, "verify": True}); zj = c.wait(z["job"])
        rep["zhufu_job"] = {"status": zj["status"], "verify": zj.get("verify"), "error": zj.get("error")}
        name, val = c.cookie.split("=", 1)

        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

            def page(vn, auth, theme=None, reduced=False):
                ctx = br.new_context(**VIEWS[vn], reduced_motion="reduce" if reduced else "no-preference", color_scheme="light")
                if auth: ctx.add_cookies([{"name": name, "value": val, "url": base}])
                if theme: ctx.add_init_script(f"try{{localStorage.setItem('vr:theme','{theme}')}}catch(e){{}}")
                p = ctx.new_page()
                p.on("pageerror", lambda e, vn=vn: rep["errors"].append(f"{vn}: pageerror {e}"))
                p.on("console", lambda m, vn=vn: m.type == "error" and rep["errors"].append(f"{vn}: console {m.text}"))
                p.on("request", lambda r, vn=vn: (not r.url.startswith(base) and not r.url.startswith(("data:", "blob:", "about:"))) and rep["errors"].append(f"{vn}: external {r.url}"))
                return ctx, p

            def shot(label, url, auth=True, full=True, before=None, theme=None, views=("desktop", "mobile"), wait=2600):
                for vn in views:
                    ctx, p = page(vn, auth, theme)
                    p.goto(base + url, wait_until="networkidle"); p.wait_for_timeout(wait)
                    if before: before(p, vn)
                    ov = p.evaluate("document.documentElement.scrollWidth - innerWidth")
                    if ov > 0: rep["errors"].append(f"{label}/{vn}: horizontal overflow {ov}px")
                    out = OUT / f"{label}-{vn}.png"; p.screenshot(path=str(out), full_page=full); rep["shots"].append(str(out)); ctx.close()

            shot("01-home", "/", auth=False)
            shot("02-login", "/login", auth=False, full=False)
            shot("03-register-dark", "/register", auth=False, full=False, theme="dark")
            shot("04-upload", "/#upload", full=False, before=lambda p, vn: p.evaluate("document.getElementById('upload').scrollIntoView()") or p.wait_for_timeout(400))
            shot("05-shelf", "/shelf")
            shot("06-shelf-dark", "/shelf", theme="dark", full=False)
            # 进度页：进行中（校验阶段）
            for vn in VIEWS:
                _, d = c.req("POST", "/api/books", {"name": "zhufu.txt", "text": ztxt, "title": "祝福（第二版）", "author": "鲁迅", "plan": zplan, "verify": True})
                ctx, p = page(vn, True); p.goto(base + "/jobs/" + d["job"], wait_until="domcontentloaded")
                try: p.wait_for_selector("[data-step=verify].active", timeout=60000)
                except Exception: rep["errors"].append(f"progress/{vn}: never saw verify active")
                p.wait_for_timeout(1500); out = OUT / f"07-progress-running-{vn}.png"; p.screenshot(path=str(out), full_page=True); rep["shots"].append(str(out)); ctx.close()
                # 同一时刻的书架：显影中的书卡（骨架扫光）
                ctx, p = page(vn, True); p.goto(base + "/shelf", wait_until="domcontentloaded"); p.wait_for_timeout(700)
                out = OUT / f"07b-shelf-running-{vn}.png"; p.screenshot(path=str(out), full_page=False); rep["shots"].append(str(out)); ctx.close()
                c.wait(d["job"])
            shot("08-progress-done", "/jobs/" + z["job"], wait=2600)

            def to_hero(p, vn):
                p.wait_for_timeout(600)
                # 像读者一样滚到第一个关键场景动画的中段，让懒加载的 iframe 起跑
                p.evaluate("""() => { const h = document.querySelector('.hs, .hero-scene, [data-hero]'); if (!h) return;
                    const r = h.getBoundingClientRect(); scrollTo(0, scrollY + r.top + Math.min(h.offsetHeight * 0.45, h.offsetHeight - innerHeight)); }""")
                for _ in range(20):
                    p.mouse.wheel(0, 30); p.wait_for_timeout(60)
                p.wait_for_timeout(2200)
            shot("09-reader-top", f"/b/{z['book']}", full=False)
            shot("10-reader-hero", f"/b/{z['book']}", full=False, before=to_hero)

            def toc_open(p, vn):
                p.evaluate("scrollTo(0, document.body.scrollHeight * 0.35)"); p.wait_for_timeout(900)
                p.click("#bToc" if vn == "desktop" else "#mToc"); p.wait_for_timeout(900)
            shot("11-reader-toc", f"/b/{z['book']}", full=False, before=toc_open)
            shot("12-settings", "/settings")
            shot("13-quality", f"/b/{z['book']}/quality")
            br.close()
    finally:
        srv.stop(); mock.close()
        for kk, vv in env_keep.items():
            if vv is None: os.environ.pop(kk, None)
            else: os.environ[kk] = vv
    (OUT / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False))


if __name__ == "__main__":
    main()
