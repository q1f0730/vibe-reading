"""长文本（整本书）测试：全部用合成文本，不含任何版权作品。
python3 -m unittest tests.test_longtext -v
- 切分：章名识别（空行后的独立短行 / 第X章）、不把"每行一段"误判成硬换行、无损
- 规划：整本书模式按章并发；模拟 429 限流、内容过滤拒答、单章异常 → 整本仍完成，降级段落用规则分镜而不是复制上一段
- 渲染：分章模式（只有第一章进 DOM，其余在 <template>），目录含章名
- Web：≥1 MB 的 txt 能上传并用 plan.json 生成（有 Chromium 时再做逐段原文校验）
"""
import io, json, os, random, re, tempfile, unittest, urllib.error

from vibereading import segment, planner, render
from tests.test_web import Server, Client, HAVE_VERIFY

NAMES = ["阿青", "老周", "林雪", "小满"]
CN_NUM = "一二三四五六七八九十"


def synth_book(chapters=12, paras=60, seed=7, heading="date", filtered_chapter=None):
    """合成小说：每行一段，章与章之间空一行；章名独占一行。"""
    rnd = random.Random(seed)
    verbs = ["推开门", "望着窗外", "把杯子放下", "沉默了一会儿", "笑了笑", "低下头", "走到楼梯口", "把信折好"]
    places = ["厨房里", "站台上", "雨里", "车里", "院子里", "楼道里"]
    out = []
    for c in range(chapters):
        if heading == "date":
            title = f"{rnd.choice(['三周后', '两个月后', '六周后', '一年后'])}（{2001 + c // 4}年{c % 12 + 1}月）" if c else "2001年1月"
        else:
            title = f"第{CN_NUM[c % 10]}{'' if c < 10 else '十'}章 远行"
        lines = [title]
        for k in range(paras):
            a, b = rnd.sample(NAMES, 2)
            r = rnd.random()
            if r < 0.35:
                lines.append(f"“{rnd.choice(['你来了', '我不知道', '明天见', '别走'])}。”{a}说。")
            elif r < 0.45:
                lines.append(f"{a}的脸红了，{b}假装没看见。")
            else:
                lines.append(f"{a}在{rnd.choice(places)}{rnd.choice(verbs)}，{b}没有说话。" * rnd.randint(1, 4))
        if filtered_chapter == c:
            lines[5] = "【过滤触发】" + lines[5]
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


class FakeLLM:
    """按请求内容回包：cast / 章 outline / detail。可注入 429、内容过滤、异常。"""
    def __init__(self, n_429=2, crash_chapter_outline=None):
        self.n_429 = n_429; self.crash = crash_chapter_outline; self.calls = 0; self.filtered = 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        body = json.loads(req.data)
        sysm, user = body["messages"][0]["content"], body["messages"][-1]["content"]
        if self.n_429 > 0:
            self.n_429 -= 1
            raise urllib.error.HTTPError(req.full_url, 429, "rate", {"Retry-After": "0"}, io.BytesIO(b'{"error":{"type":"rate_limit_error"}}'))
        if "【过滤触发】" in user and "逐字子串" in sysm:   # detail 批次里有"敏感"段 → 拒答
            self.filtered += 1
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b'{"error":{"type":"content_filter_error"}}'))
        if "全书通用" in sysm:
            out = {"meta": {"title": "合成", "subtitle": "测试", "hero_motif": "door", "hero_segments": []},
                   "characters": [{"id": f"c{k}", "name": n, "color": "#2b4a6f"} for k, n in enumerate(NAMES)],
                   "focal_pair": ["c0", "c1"]}
        elif "只为这一章划分场景" in sysm:
            a, b = map(int, re.search(r"编号 (\d+)–(\d+)", user).groups())
            if self.crash is not None and a == self.crash:
                raise ValueError("boom")
            sc, s = [], a
            while s <= b:
                e = min(b, s + 9); sc.append({"start": s, "end": e, "name": "场景", "time": "", "place": "", "tone": "day"}); s = e + 1
            out = {"scenes": sc}
        else:
            idx = [int(x) for x in re.findall(r"^\[(\d+)\]", user, re.M)]
            out = {"paras": [[i, "c0", ["c0", "c1"], 0.5, 0.3, 0.0, ""] for i in idx], "utterances": [], "visuals": [], "quotes": []}
        d = {"choices": [{"message": {"content": json.dumps(out, ensure_ascii=False)}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
        return FakeResp(json.dumps(d).encode())


class SegmentTest(unittest.TestCase):
    def test_date_headings_become_chapters(self):
        t = synth_book(chapters=18)
        seg = segment.split(t)
        self.assertEqual(seg["mode"], "line")
        self.assertTrue(segment.verify_lossless(t, seg))
        self.assertEqual(len(seg["chapters"]), 18)
        self.assertEqual(seg["chapters"][0]["title"], "2001年1月")
        self.assertRegex(seg["chapters"][1]["title"], r"（2001年2月）$")
        self.assertEqual(seg["count"], 18 * 61)

    def test_numbered_headings(self):
        seg = segment.split(synth_book(chapters=5, heading="num"))
        self.assertEqual([c["title"][:3] for c in seg["chapters"]], ["第一章", "第二章", "第三章", "第四章", "第五章"])

    def test_hard_wrapped_text_still_joined(self):
        para = "他走进门来，看见屋里没有人，便在桌边坐下，等了很久也不见有人出来，心里不免有些着急起来，于是站起身四处张望。" * 2
        t = "\n\n".join("\n".join(para[i:i + 20] for i in range(0, len(para), 20)) for _ in range(5))
        seg = segment.split(t)
        self.assertEqual(seg["mode"], "block"); self.assertEqual(seg["count"], 5)
        self.assertTrue(segment.verify_lossless(t, seg))

    def test_short_text_unchanged(self):
        t = "2011年1月\n她开门。\n他进来了。\n"
        seg = segment.split(t)
        self.assertEqual(seg["segments"][0]["role"], "dateline"); self.assertEqual(len(seg["chapters"]), 1)


class BookPlannerTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k) for k in ("DASHSCOPE_API_KEY", "VIBEREADING_GATEWAY", "INCEPTION_API_KEY") if k in os.environ}
        os.environ["INCEPTION_API_KEY"] = "test-not-a-real-key"
        self._urlopen, self._sleep = planner.urllib.request.urlopen, planner._sleep_for
        planner._sleep_for = lambda *a, **k: 0

    def tearDown(self):
        planner.urllib.request.urlopen, planner._sleep_for = self._urlopen, self._sleep
        os.environ.pop("INCEPTION_API_KEY", None); os.environ.update(self._env)

    def test_book_mode_degrades_per_batch_and_chapter(self):
        t = synth_book(chapters=10, paras=70, filtered_chapter=3)
        seg = segment.split(t)
        self.assertTrue(planner.is_book(seg))
        crash_at = seg["chapters"][6]["start"]
        fake = FakeLLM(n_429=3, crash_chapter_outline=crash_at)
        planner.urllib.request.urlopen = fake
        events = []
        plan, st = planner.plan(seg, progress=lambda s, m: events.append((s, m)), model_spec="mercury")
        st = st.as_dict()
        n = seg["count"]
        self.assertEqual(len(plan["paras"]), n)
        # 场景完整覆盖、不跨章
        sc = plan["scenes"]; self.assertEqual(sc[0]["start"], 0); self.assertEqual(sc[-1]["end"], n - 1)
        for a, b in zip(sc, sc[1:]): self.assertEqual(b["start"], a["end"] + 1)
        self.assertEqual(len(plan["chapters"]), 10)
        # 429 被退避重试吸收
        self.assertEqual(st["transient"], 3)
        # 内容过滤：拆批后只有含"敏感"段的那一小块降级
        self.assertGreaterEqual(fake.filtered, 1); self.assertGreaterEqual(st["content_filtered"], 1)
        bad = next(i for i, s in enumerate(seg["segments"]) if s["text"].startswith("【过滤触发】"))
        ch3 = seg["chapters"][3]
        rule3 = [i for i in range(ch3["start"], ch3["end"] + 1) if plan["paras"][i]["in"] == 0.3 and plan["paras"][i]["te"] == 0.2]
        self.assertIn(bad, rule3)
        self.assertLess(len(rule3), 12, "内容过滤应只降级一小块而不是整章")
        # 单章 outline 异常 → 只有这一章用规则场景（场景名来自章名）
        ch6 = seg["chapters"][6]
        names6 = {s["name"] for s in sc if ch6["start"] <= s["start"] <= ch6["end"]}
        self.assertTrue(all(x.startswith(ch6["title"][:6]) for x in names6), names6)
        # 降级段不是"复制上一段"：规则分镜的说话人只来自本段文字
        for i in rule3:
            p = plan["paras"][i]; txt = seg["segments"][i]["text"]
            if p["sp"]:
                self.assertIn(NAMES[int(p["sp"][1:])], txt)
        # 进度：每章一条 chapter+，最后一条是 N/N
        ch_ev = [m for s, m in events if s == "chapter+"]
        self.assertEqual(len(ch_ev), 10); self.assertTrue(any("（10/10）" in m for m in ch_ev))

    def test_render_chapter_mode(self):
        t = synth_book(chapters=8, paras=150)
        self.assertGreater(len(t), render.CHAPTER_MODE_CHARS)
        seg = segment.split(t)
        planner.urllib.request.urlopen = FakeLLM(n_429=0)
        plan, _ = planner.plan(seg, model_spec="mercury")
        html, warns = render.render(seg, plan, title="合成")
        self.assertEqual(html.count('class="chapter"'), 8)
        self.assertEqual(html.count("<template>"), 7)                     # 第一章直接渲染，其余惰性
        self.assertEqual(html.count('<li class="chap"'), 8)               # 目录里有每一章
        self.assertIn("（2001年2月）", html)
        state = json.loads(re.search(r"const STATE = (.*?);\n", html).group(1))
        self.assertEqual(len(state["chapters"]), 8)
        self.assertNotIn("http://", re.sub(r"http://www\.w3\.org[^\"' ]*", "", html))
        self.assertNotIn("https://", html)


class WebBigUploadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vrlong-")
        cls.srv = Server(cls.tmp).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_upload_over_1mb_with_plan(self):
        """≥1 MB 的 txt 能上传、生成、打开（这么大的合成书在 Chromium 里逐段校验要几分钟，校验放到下一个用例）。"""
        t = synth_book(chapters=64, paras=200, seed=3)
        self.assertGreater(len(t.encode("utf-8")), 1_000_000)
        seg = segment.split(t)
        plan = {"meta": {"title": "合成长篇"}, "characters": [{"id": "a", "name": "阿青"}],
                "scenes": [{"start": 0, "end": seg["count"] - 1, "name": "全书"}]}     # 一个大场景：渲染时按章切开
        a = Client(self.srv); a.register("bigbook")
        c, j, *_ = a.req("POST", "/api/books", {"name": "big.txt", "text": t, "title": "合成长篇", "plan": plan, "verify": False})
        self.assertEqual(c, 201, j)
        job = a.wait_job(j["job"], timeout=300)
        self.assertEqual(job["status"], "done", job)
        c, _, raw, _ = a.req("GET", f"/b/{j['book']}")
        self.assertEqual(c, 200)
        self.assertEqual(raw.count(b'class="chapter"'), 64)
        self.assertEqual(raw.count(b"<template>"), 63)

    @unittest.skipUnless(HAVE_VERIFY, "需要 node + Chromium")
    def test_chapter_mode_verify(self):
        """分章模式的阅读页：全部章节挂载后逐段 100% 一致、无外链、无 JS 报错、无横向溢出。"""
        t = synth_book(chapters=10, paras=160, seed=5)
        seg = segment.split(t)
        plan = {"meta": {"title": "合成"}, "characters": [{"id": "a", "name": "阿青"}, {"id": "b", "name": "老周"}],
                "scenes": [{"start": c["start"], "end": c["end"], "name": "场景"} for c in seg["chapters"]]}
        a = Client(self.srv); a.register("midbook")
        c, j, *_ = a.req("POST", "/api/books", {"name": "mid.txt", "text": t, "title": "合成", "plan": plan, "verify": True})
        self.assertEqual(c, 201, j)
        job = a.wait_job(j["job"], timeout=600)
        self.assertEqual(job["status"], "done", job)
        v = job["verify"]
        self.assertTrue(v["chapter_mode"]); self.assertTrue(v["ok"], v)
        self.assertEqual(v["matched"], seg["count"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
