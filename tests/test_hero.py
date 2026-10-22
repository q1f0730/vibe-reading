"""关键场景动画（hero scenes）测试：全部用合成文本 + 模拟 LLM，不含任何版权作品、不发真实请求。
python3 -m unittest tests.test_hero -v
- 静态检查：外链 / 网络 API / 存储 API / 体积上限 / 必须订阅滚动进度
- 浏览器校验（有 Chromium 时）：合格模块通过；报错 + 溢出 + 减弱动效下自己动 + 不随滚动变化的模块被拒
- 编排：生成 → 校验 → 带错误修一次 → 仍不合格回退预制组件；内容过滤拒答直接回退；选场景被拒时去掉短摘重试
- 渲染：合格模块进沙箱 iframe（sandbox="allow-scripts"、CSP 无网络）、回退组件插在同一位置；原文逐段不受影响
"""
import io, json, os, pathlib, re, subprocess, tempfile, unittest, urllib.error
from unittest import mock

from vibereading import segment, planner, render, hero
from tests.test_web import HAVE_VERIFY

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
GOOD = (FIX / "hero_good.html").read_text(encoding="utf-8")
BAD = (FIX / "hero_bad.html").read_text(encoding="utf-8")
CHARS = [{"id": "aqing", "name": "阿青", "color": "#2b4a6f"}, {"id": "laozhou", "name": "老周", "color": "#a3253c"}]


def synth_text(n=40):
    out = ["2001年1月"]
    acts = ["推开门走进来", "站在窗边没说话", "把杯子放回桌上", "低头看着自己的手", "笑了一下又停住", "走到楼梯口回头看"]
    for k in range(n):
        a, b = ("阿青", "老周") if k % 2 else ("老周", "阿青")
        out.append(f"{a}{acts[k % len(acts)]}，{b}看着{a}，过了一会儿说：今天的风很大。" if k % 3 else f"屋里很安静，{a}和{b}都没有开口，第{k}次。")
    return "\n".join(out) + "\n"


def synth_plan(seg):
    n = seg["count"]
    scenes = [{"start": 0, "end": 13, "name": "进门", "time": "傍晚", "place": "厨房", "tone": "day"},
              {"start": 14, "end": 27, "name": "窗边", "time": "傍晚", "place": "客厅", "tone": "dim"},
              {"start": 28, "end": n - 1, "name": "离开", "time": "夜里", "place": "门口", "tone": "night"}]
    paras = [{"sp": "", "pr": ["aqing", "laozhou"], "in": round(0.2 + 0.6 * i / n, 2), "te": 0.3, "bl": 0, "bw": ""} for i in range(n)]
    p = {"meta": {"title": "合成", "subtitle": "阿青 · 老周", "hero_motif": "door", "hero_segments": [0]},
         "characters": CHARS, "focal_pair": ["aqing", "laozhou"], "scenes": scenes, "paras": paras,
         "visuals": [{"at": 10, "pos": "after", "type": "interior", "params": {"room": "kitchen", "caption": "厨房"}}], "quotes": []}
    plan, _ = planner.validate_plan(p, seg)
    return plan


SELECT_JSON = {"heroes": [
    {"scene": 0, "at": 10, "title": "推门", "caption": "门被推开", "summary": "两人在厨房相遇", "setting": "傍晚的厨房",
     "objects": ["门", "桌子", "杯子"], "characters": [{"id": "aqing", "role": "门口"}, {"id": "laozhou", "role": "桌边"}],
     "curve": [{"t": 0, "beat": "门关着"}, {"t": .5, "beat": "门开了"}, {"t": 1, "beat": "对视"}], "motif": "门缝里的光",
     "seconds": 12, "tone": "day", "fallback": {"type": "door", "params": {"action": "open", "caption": "门开了"}}},
    {"scene": 2, "at": 33, "title": "离开", "summary": "老周离开", "objects": ["门", "楼梯"],
     "curve": [{"t": 0, "beat": "起身"}, {"t": .5, "beat": "走到门口"}, {"t": 1, "beat": "门关上"}], "motif": "距离拉远",
     "seconds": 30, "fallback": {"type": "silence", "params": {"length": "long", "bogus": 1}}},
]}


def http_filtered():
    return urllib.error.HTTPError("https://api.inceptionlabs.ai/v1/chat/completions", 400, "Bad Request", {},
                                  io.BytesIO(b'{"error":{"code":"content_filter","message":"blocked"}}'))


class FakeLLM:
    """按调用类型（选场景 / 镜头脚本 / 写模块 / 修复）返回预设内容；记录收到的消息。"""
    def __init__(self, modules, select=SELECT_JSON, select_filtered=0, gen_filtered=False):
        self.modules = list(modules); self.select = select; self.select_filtered = select_filtered
        self.gen_filtered = gen_filtered; self.calls = []

    def __call__(self, messages, model, max_tokens, stats, provider="qwen", json_mode=True, extra_override=None, timeout=None):
        sysmsg = messages[0]["content"]; self.calls.append((sysmsg[:12], messages))
        stats.calls += 1
        if sysmsg.startswith("你是图文长篇"):
            if self.select_filtered > 0:
                self.select_filtered -= 1; raise http_filtered()
            return json.dumps(self.select, ensure_ascii=False), "stop"
        if sysmsg.startswith("你是报纸图像部的分镜师"):
            return json.dumps({"elements": [{"id": "door", "x": 1, "y": 1, "w": 10, "h": 10}], "beats": []}), "stop"
        if self.gen_filtered:
            raise http_filtered()
        return "好的：\n```html\n" + self.modules.pop(0) + "\n```", "stop"


class StaticChecks(unittest.TestCase):
    def test_good_fixture_passes(self):
        self.assertEqual(hero.static_check(GOOD), [])

    def test_forbidden(self):
        cases = {
            '<script src="https://cdn.x/y.js"></script><div></div><script>VRH.onProgress(function(p){})</script>': "外部",
            '<div></div><script>fetch("/a");VRH.onProgress(function(p){})</script>': "网络",
            '<div></div><script>localStorage.x=1;VRH.onProgress(function(p){})</script>': "存储",
            '<style>@import "a.css";</style><div></div><script>VRH.onProgress(function(p){})</script>': "@import",
            '<div style="background:url(//evil.example/a.png)"></div><script>VRH.onProgress(function(p){})</script>': "外部地址",
            '<div></div><script>var x=1;</script>': "VRH.onProgress",
            '<div></div><script>parent.document.body.remove();VRH.onProgress(function(p){})</script>': "父页面",
            '<div></div><script>VRH.onProgress(function(p){})': "未闭合",
        }
        for mod, why in cases.items():
            errs = hero.static_check(mod)
            self.assertTrue(any(why in e for e in errs), (mod, errs))

    def test_size_cap(self):
        big = GOOD + "<!--" + "x" * (hero.MAX_BYTES + 10) + "-->"
        self.assertTrue(any("体积" in e for e in hero.static_check(big)))

    def test_extract_module(self):
        raw = ('说明文字\n```html\n<!DOCTYPE html><html><head><title>t</title><style>a{}</style></head><body>'
               '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg><script>VRH.onProgress(function(p){})</script></body></html>\n```')
        m = hero.extract_module(raw)
        self.assertNotIn("<html", m); self.assertNotIn("<body", m); self.assertNotIn("<title", m)
        self.assertIn("<style>a{}</style>", m); self.assertNotIn("xmlns", m)
        self.assertEqual(hero.static_check(m), [])

    def test_wrap_has_csp_and_runtime(self):
        doc = hero.wrap(GOOD, render.fix_colors([dict(c) for c in CHARS]))
        self.assertIn("default-src 'none'", doc); self.assertIn("connect-src 'none'", doc)
        self.assertIn("window.VRH", doc); self.assertIn("--c-aqing", doc)
        self.assertIn(":root[data-theme=night]", doc)

    def test_sanitize_drops_unsafe_module(self):
        hs = hero.sanitize_heroes([{"id": "x", "at": 3, "module": "<div></div><script>fetch('x');VRH.onProgress(function(p){})</script>",
                                    "fallback": {"type": "door", "params": {"action": "open", "zzz": 1}}},
                                   {"id": "y", "at": 999, "module": GOOD}], 10)
        self.assertEqual(len(hs), 1)
        self.assertIsNone(hs[0]["module"]); self.assertEqual(hs[0]["status"], "fallback")
        self.assertEqual(hs[0]["fallback"], {"type": "door", "params": {"action": "open"}})


@unittest.skipUnless(HAVE_VERIFY, "需要 Chromium + puppeteer-core")
class BrowserChecks(unittest.TestCase):
    def test_good_and_bad(self):
        chars = render.fix_colors([dict(c) for c in CHARS])
        static = (FIX / "hero_static.html").read_text(encoding="utf-8")
        r = hero.browser_check({"good": hero.wrap(GOOD, chars), "bad": hero.wrap(BAD, chars), "static": hero.wrap(static, chars)})
        self.assertTrue(r["good"]["ok"], r["good"]["errors"])
        e = " ".join(r["bad"]["errors"])
        for why in ("JS 报错", "横向溢出", "减弱动效", "空白"):
            self.assertIn(why, e)
        self.assertFalse(r["static"]["ok"])
        self.assertTrue(any("随滚动变化太小" in x for x in r["static"]["errors"]), r["static"]["errors"])
        self.assertIn("undefinedThing", e)
        self.assertIn("perf", r["good"]["metrics"])


class Orchestration(unittest.TestCase):
    def setUp(self):
        self.seg = segment.split(synth_text())
        self.plan = synth_plan(self.seg)

    def _check_stub(self, mods, chars, hs):
        # 不开浏览器：静态检查 + 用模块里的标记模拟浏览器结论
        return {k: (hero.static_check(m) or (["JS 报错：boom"] if "BROKEN" in m else [])) for k, m in mods.items()}

    def test_pass_fix_fallback(self):
        broken = GOOD.replace("<script>", "<script>/*BROKEN*/", 1)
        fake = FakeLLM([GOOD, broken, broken])   # h1-1 首次通过；h1-2 两次都坏 → 回退
        with mock.patch.object(planner, "chat", fake), mock.patch.object(hero, "check", self._check_stub), \
             mock.patch.dict(os.environ, {"VR_LLM_CONCURRENCY": "1"}):
            hs = hero.run(self.seg, self.plan, per_chapter=3, mode="code")
        H = {h["id"]: h for h in self.plan["heroes"]}
        self.assertEqual(hs.selected, 2)
        self.assertEqual(H["h1-1"]["status"], "ok"); self.assertEqual(H["h1-1"]["attempts"], 1)
        self.assertEqual(H["h1-2"]["status"], "fallback"); self.assertEqual(H["h1-2"]["attempts"], 2)
        self.assertEqual(H["h1-2"]["seconds"], 20)                      # 夹紧到 8–20 秒
        self.assertEqual(H["h1-2"]["fallback"], {"type": "silence", "params": {"length": "long"}})   # 非法参数被去掉
        self.assertEqual((hs.passed_first, hs.passed_fixed, hs.fallback), (1, 0, 1))
        # 修复请求里带上了错误信息
        fix_msgs = [m for tag, m in fake.calls if tag.startswith("你是 The Pud") and len(m) > 2]
        self.assertTrue(fix_msgs and "boom" in fix_msgs[0][-1]["content"])
        # 写模块的请求里没有原文段落
        gen_msgs = [m for tag, m in fake.calls if tag.startswith("你是 The Pud")]
        for msgs in gen_msgs:
            self.assertNotIn("今天的风很大", msgs[1]["content"])

    def test_fixed_on_second_try(self):
        broken = GOOD.replace("<script>", "<script>/*BROKEN*/", 1)
        sel = {"heroes": SELECT_JSON["heroes"][:1]}
        with mock.patch.object(planner, "chat", FakeLLM([broken, GOOD], select=sel)), mock.patch.object(hero, "check", self._check_stub):
            hs = hero.run(self.seg, self.plan, per_chapter=1, mode="code")
        self.assertEqual(self.plan["heroes"][0]["status"], "fixed"); self.assertEqual(hs.passed_fixed, 1)

    def test_content_filter_generation_falls_back(self):
        with mock.patch.object(planner, "chat", FakeLLM([], gen_filtered=True)), mock.patch.object(hero, "check", self._check_stub):
            hs = hero.run(self.seg, self.plan, per_chapter=2, mode="code")
        self.assertTrue(all(h["status"] == "fallback" and h["module"] is None for h in self.plan["heroes"]))
        self.assertEqual(hs.fallback, 2); self.assertGreaterEqual(hs.filtered, 2)

    def test_content_filter_selection_retries_without_snippets(self):
        fake = FakeLLM([GOOD, GOOD], select_filtered=1)
        with mock.patch.object(planner, "chat", fake), mock.patch.object(hero, "check", self._check_stub):
            hs = hero.run(self.seg, self.plan, per_chapter=2, mode="code")
        sels = [m for tag, m in fake.calls if tag.startswith("你是图文长篇")]
        self.assertEqual(len(sels), 2)
        self.assertIn("今天的风很大"[:4], sels[0][1]["content"])          # 第一次带截断短摘
        self.assertNotIn("今天的风", sels[1][1]["content"])                # 被拒后只给元数据
        self.assertEqual(hs.selected, 2)
        # 两次都被拒 → 本章不做 hero，流程照常
        plan2 = synth_plan(self.seg)
        with mock.patch.object(planner, "chat", FakeLLM([], select_filtered=2)), mock.patch.object(hero, "check", self._check_stub):
            hs2 = hero.run(self.seg, plan2, per_chapter=2, mode="code")
        self.assertEqual(plan2["heroes"], []); self.assertEqual(hs2.filtered, 2)

    def test_render_iframe_and_fallback(self):
        self.plan["heroes"] = [
            {"id": "h1-1", "at": 10, "pos": "after", "title": "推门", "caption": "门被推开", "seconds": 12, "module": GOOD, "status": "ok"},
            {"id": "h1-2", "at": 33, "pos": "after", "title": "离开", "module": None, "status": "fallback",
             "fallback": {"type": "door", "params": {"action": "close", "caption": "门关上"}}}]
        html, warns = render.render(self.seg, self.plan)
        self.assertEqual(html.count('class="hs"'), 1)
        self.assertIn('class="hs-src"', html)
        self.assertNotIn("</script><script>VRH", html)                  # 模块源码被转义在 JSON 里
        self.assertIn("sandbox', 'allow-scripts'", html)                 # 运行时以沙箱方式创建 iframe
        self.assertIn('data-a="close"', html)                            # 回退组件
        self.assertTrue(any("关键场景动画" in w for w in warns))         # 相邻的预制 interior 被让位
        # 原文段落与不带 hero 时完全一致
        plain, _ = render.render(self.seg, dict(self.plan, heroes=[]))
        para = lambda h: re.findall(r'<(?:p|h2)[^>]*data-src="\d+"[^>]*>.*?</(?:p|h2)>', h, flags=re.S)
        self.assertEqual(para(html), para(plain))
        src = re.search(r'<script type="application/json" class="hs-src">(.*?)</script>', html, flags=re.S).group(1)
        self.assertNotIn("<", src)
        doc = json.loads(src)
        self.assertIn("default-src 'none'", doc); self.assertIn("VRH.onProgress", doc)

    @unittest.skipUnless(HAVE_VERIFY, "需要 Chromium + puppeteer-core")
    def test_page_with_hero_still_verifies(self):
        self.plan["heroes"] = [{"id": "h1-1", "at": 10, "pos": "after", "title": "推门", "seconds": 10, "module": GOOD, "status": "ok"}]
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "p.html"; segp = pathlib.Path(d) / "p.segments.json"
            html, _ = render.render(self.seg, self.plan)
            out.write_text(html, encoding="utf-8"); segp.write_text(json.dumps(self.seg, ensure_ascii=False), encoding="utf-8")
            r = subprocess.run(["node", str(ROOT / "tools/verify.js"), str(out), str(segp)], capture_output=True, text=True, timeout=300)
            rep = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(rep["ok"], rep)
        self.assertTrue(rep["text_identical"]); self.assertEqual(rep["external_requests"], [])

    def test_pipeline_flag(self):
        """--hero / --no-hero / 默认保留：用外部分镜 + 模拟 LLM 跑 pipeline.build。"""
        from vibereading import pipeline
        text = synth_text()
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "b.html"
            sel = {"heroes": SELECT_JSON["heroes"][:1]}
            with mock.patch.object(planner, "chat", FakeLLM([GOOD], select=sel)), mock.patch.object(hero, "check", self._check_stub), \
                 mock.patch.dict(os.environ, {"VIBEREADING_GATEWAY": "1"}):
                rep = pipeline.build(text, out, plan_in=synth_plan(self.seg), hero=True, hero_max=1, hero_mode="code")
            self.assertEqual(rep["hero"]["selected"], 1); self.assertEqual(rep["hero"]["passed_first"], 1)
            saved = json.loads((pathlib.Path(d) / "b.plan.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved["heroes"]), 1)
            self.assertIn('class="hs"', out.read_text(encoding="utf-8"))
            # 复用分镜重新渲染（hero=None）保留动画；--no-hero 去掉
            pipeline.build(text, out, reuse_plan=True)
            self.assertIn('class="hs"', out.read_text(encoding="utf-8"))
            pipeline.build(text, out, reuse_plan=True, hero=False)
            self.assertNotIn('class="hs"', out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
