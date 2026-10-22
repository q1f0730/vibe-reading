"""hero 阶段的规模与鲁棒性：校验池（每个 hero 独立超时、复用浏览器、卡死重启）、时间预算、逐个进度、
专有名词白名单 / 常见词、意象相关性放宽、空标题兜底。合成文本 + 模拟 LLM，不含任何版权作品、不发真实请求。
python3 -m unittest tests.test_hero_budget -v
"""
import json, os, sys, tempfile, textwrap, threading, time, unittest
from unittest import mock

from vibereading import segment, planner, hero, hero_prims as HP, quality, render
from vibereading import hero_checker
from vibereading.web import jobs as wjobs
from tests.test_web import HAVE_VERIFY
from tests.test_hero import synth_text, synth_plan, CHARS, SELECT_JSON
from tests.test_hero_prims import comp, GOOD, IDS

SECOND = comp(("ink", 0, .55, {"word": "余温"}), ("press", .5, 1, {"word": "风声"}))


class FakeCompose:
    """编排请求：本章第一个 → GOOD，之后 → SECOND（遵守本章原语次数上限）。线程安全。"""
    def __init__(self, delay=0.0):
        self.n = 0; self.lock = threading.Lock(); self.delay = delay

    def __call__(self, messages, model, max_tokens, stats, provider="qwen", json_mode=True, extra_override=None, timeout=None):
        with self.lock:
            self.n += 1; stats.calls += 1
        if self.delay:
            time.sleep(self.delay)
        user = messages[1]["content"]
        return json.dumps(SECOND if "本章前面的关键场景已经用过" in user else GOOD, ensure_ascii=False), "stop"


class FakePool:
    """代替 CheckerPool：bad = {id: 结果}，其余通过；delay 模拟每次实测耗时；记录调用。"""
    def __init__(self, bad=None, delay=0.0):
        self.bad = bad or {}; self.delay = delay; self.calls = []; self.lock = threading.Lock()

    def check(self, docs, deadline=None, timeout=None):
        with self.lock:
            self.calls += list(docs)
        if self.delay:
            time.sleep(self.delay)
        out = {}
        for k in docs:
            if deadline is not None and time.time() >= deadline:
                out[k] = {"ok": False, "timeout": True, "budget": True, "errors": ["预算"], "metrics": {}}
            else:
                out[k] = self.bad.get(k) or {"ok": True, "errors": [], "metrics": {}}
        return out

    def close(self):
        pass


def make_jobs(seg, n_chapters=31, per=2):
    jobs = []
    for c in range(n_chapters):
        for j in range(per):
            h = dict(SELECT_JSON["heroes"][j], at=SELECT_JSON["heroes"][j]["at"])
            jobs.append((f"h{c + 1}-{j + 1}", h))
    return jobs


def stats(pool, budget=420):
    hs = hero.HeroStats(None); hs.pool = pool; hs.budget_s = budget; hs.deadline = time.time() + budget
    return hs


class Batch(unittest.TestCase):
    def setUp(self):
        self.seg = segment.split(synth_text()); self.plan = synth_plan(self.seg)
        self.chars = render.fix_colors([dict(c) for c in CHARS])

    def run_prims(self, hs, jobs, chat):
        msgs = []
        with mock.patch.object(planner, "chat", chat):
            out = hero.run_prims(self.seg, self.plan, jobs, self.chars, hs, lambda st, m: msgs.append((st, m)))
        return out, msgs

    def test_62_heroes_all_pass_with_linear_progress(self):
        jobs = make_jobs(self.seg); pool = FakePool()
        out, msgs = self.run_prims(stats(pool), jobs, FakeCompose())
        self.assertEqual(len(out), 62)
        self.assertEqual({h["status"] for h in out}, {"ok"})
        self.assertTrue(all(h["comp"]["shots"] and h["title"] for h in out))
        self.assertEqual(len(pool.calls), 62)                      # 每个 hero 恰好实测一次
        prog = [m for st, m in msgs if st == "hero+"]
        self.assertEqual(len(prog), 62)
        self.assertEqual([int(m.rsplit("（", 1)[1].split("/")[0]) for m in prog], list(range(1, 63)))
        self.assertTrue(all(m.endswith("/62）") for m in prog))
        # 进度条：64 → 88 线性、单调
        st = {"pct": 64, "batches": 3}; pcts = []
        for m in prog:
            wjobs.advance(st, "hero+", m); pcts.append(st["pct"])
        self.assertEqual(pcts, sorted(pcts)); self.assertAlmostEqual(pcts[-1], 88); self.assertLess(pcts[0], 67)
        wjobs.advance(st, "render", ""); self.assertGreater(st["pct"], 88)

    def test_single_timeout_only_affects_that_hero(self):
        jobs = make_jobs(self.seg, 6)
        tmo = {"ok": False, "timeout": True, "errors": ["校验超时（单个模块 40s）"], "metrics": {}}
        pool = FakePool(bad={"h3-1": tmo})
        hs = stats(pool)
        out, _ = self.run_prims(hs, jobs, FakeCompose())
        H = {h["id"]: h for h in out}
        self.assertEqual(H["h3-1"]["status"], "simple")             # 模型编排×2、规则编排都超时 → 简化版，不回退预制组件
        self.assertTrue(H["h3-1"]["comp"]["shots"])
        self.assertTrue(any("校验超时" in x for es in H["h3-1"]["errors"] for x in es))
        self.assertEqual({k: v["status"] for k, v in H.items() if k != "h3-1"}, {k: "ok" for k in H if k != "h3-1"})
        self.assertEqual(hs.check_timeouts, 3); self.assertEqual(hs.fallback, 0); self.assertEqual(hs.simple, 1)

    def test_rule_reject_falls_to_simple_not_prefab(self):
        jobs = make_jobs(self.seg, 1, 1)
        bad = {"ok": False, "errors": ["画面随滚动变化太小"], "metrics": {}}
        out, _ = self.run_prims(stats(FakePool(bad={"h1-1": bad})), jobs, FakeCompose())
        self.assertEqual(out[0]["status"], "simple")
        self.assertEqual([s["prim"] for s in out[0]["comp"]["shots"]], ["typeset", "window"])

    def test_budget_exhausted_rest_use_rule(self):
        jobs = make_jobs(self.seg, 1, 2) + [(f"h{c}-1", dict(SELECT_JSON["heroes"][0])) for c in range(2, 9)]
        chat = FakeCompose(delay=0.15)
        hs = stats(FakePool(delay=0.05), budget=0.5)
        with mock.patch.dict(os.environ, {"VR_LLM_CONCURRENCY": "1"}):
            out, msgs = self.run_prims(hs, jobs, chat)
        st = [h["status"] for h in out]
        self.assertEqual(len(out), 9); self.assertNotIn("fallback", st)
        self.assertIn("rule", st); self.assertIn("ok", st)
        late = [h for h in out if h["status"] == "rule"]
        self.assertTrue(all(not h["checked"] and h["attempts"] == 0 for h in late[1:]))   # 预算外：不调模型、不实测
        self.assertLess(chat.n, 9)
        self.assertEqual(len([m for s, m in msgs if s == "hero+"]), 9)

    def test_run_budget_zero_skips_select(self):
        calls = []
        def chat(messages, *a, **k):
            calls.append(1); raise AssertionError("不应调用模型")
        with mock.patch.object(planner, "chat", chat):
            hs = hero.run(self.seg, self.plan, per_chapter=2, budget=0)
        self.assertEqual(self.plan["heroes"], []); self.assertEqual(calls, [])


class Whitelist(unittest.TestCase):
    SRC = "弗朗西丝坐在床边。她说我只是个普通人。医生提到生育能力的问题。今天的风很大。博比在都柏林。"

    def test_names_and_common_words(self):
        allow = ["弗朗西丝", "博比", "都柏林"]
        self.assertIsNone(HP.leaks("弗朗西丝", self.SRC, allow=allow))
        self.assertIsNone(HP.leaks("博比的门", self.SRC, allow=allow))
        self.assertIsNone(HP.leaks("生育能力", self.SRC, allow=allow))         # 单独的常见实词
        self.assertEqual(HP.leaks("今天的风", self.SRC, allow=allow), "今天的风")  # 带虚字的短语
        self.assertTrue(HP.leaks("我只是个普通人", self.SRC, allow=allow))
        self.assertTrue(HP.leaks("医生提到生育", self.SRC, allow=allow))        # ≥5 字连续重合
        self.assertTrue(HP.leaks("弗朗西丝坐在", self.SRC))                     # 不给白名单：人名 + 原文连成 ≥5 字
        self.assertIsNone(HP.leaks("弗朗西丝坐在", self.SRC, allow=allow))

    def test_proper_nouns_from_plan(self):
        plan = {"characters": [{"id": "f", "name": "弗朗西丝", "aliases": ["弗朗西丝", "弗"]}, {"id": "w", "name": "我", "aliases": []}],
                "scenes": [{"place": "都柏林·公寓"}, {"place": ""}], "meta": {"title": "聊天记录"}}
        pn = HP.proper_nouns(plan)
        self.assertIn("弗朗西丝", pn); self.assertIn("都柏林", pn); self.assertIn("聊天记录", pn)
        self.assertNotIn("我", pn); self.assertNotIn("弗", pn)

    def test_validate_accepts_name_label(self):
        c = comp(("focus", 0, .55, {"far": "弗朗西丝坐在", "near": "床沿"}), ("press", .5, 1, {"word": "博比"}))
        _, errs = HP.validate(c, IDS, self.SRC, allow=["弗朗西丝", "博比"])
        self.assertEqual(errs, [])
        _, errs = HP.validate(c, IDS, self.SRC)
        self.assertTrue(any("原文重合" in e for e in errs))


class Imagery(unittest.TestCase):
    def test_generic_imagery_allowed(self):
        scene = "他们在厨房里说话，桌上放着两个杯子。"
        for ws in (["相遇", "晨光", "笑意", "靠近"], ["逼近", "爆发", "沉默碎裂", "冷光"]):
            c = {"shots": [{"params": {"words": ws}}]}
            self.assertEqual(quality.hero_word_issues(c, scene), [], ws)

    def test_obviously_unrelated_rejected(self):
        c = {"shots": [{"params": {"words": ["量子", "税务"]}}]}
        self.assertTrue(quality.hero_word_issues(c, "他们在厨房里说话。"))


class Title(unittest.TestCase):
    def test_empty_title_from_summary(self):
        e = {"desc": {"summary": "凌晨三点，梅丽莎带弗朗西丝和博比去空房留宿", "motif": ""}, "comp": None, "_fig": 3}
        t = hero.short_title(e, ["弗朗西丝", "梅丽莎", "博比"], "", lambda x: not x)
        self.assertEqual(t, "凌晨三点")
        e = {"desc": {"summary": "梅丽莎带弗朗西丝去空房", "motif": ""}, "comp": None, "_fig": 3}
        t = hero.short_title(e, ["弗朗西丝", "梅丽莎"], "", lambda x: not x)
        self.assertTrue(t and "梅丽莎" not in t and len(t) <= 8, t)


FAKE_SERVE = textwrap.dedent("""
    import sys, json, time, threading
    print(json.dumps({"event": "ready"}), flush=True)
    def work(o):
        print(json.dumps({"event": "start", "id": o["id"]}), flush=True)
        if o["id"].startswith("hang"):
            time.sleep(3600)
        print(json.dumps({"id": o["id"], "ok": True, "errors": [], "metrics": {}}), flush=True)
    for line in sys.stdin:
        o = json.loads(line)
        if o["id"].startswith("hang"):
            work(o)            # 整个进程卡死（不再处理后续）
        else:
            threading.Thread(target=work, args=(o,), daemon=True).start()
""")


class Pool(unittest.TestCase):
    def test_stuck_process_restarted_only_stuck_item_times_out(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(FAKE_SERVE); path = f.name
        try:
            with mock.patch.object(hero_checker, "GRACE", 0.5):
                with hero_checker.CheckerPool(concurrency=2, timeout=1, cmd=[sys.executable, path]) as p:
                    t = time.time()
                    r = p.check({"a": "x", "hang1": "x", "b": "x", "c": "x"})
                    self.assertLess(time.time() - t, 15)
                    self.assertTrue(r["hang1"]["timeout"]); self.assertFalse(r["hang1"]["ok"])
                    self.assertTrue(all(r[k]["ok"] for k in "abc"), r)
                    self.assertGreaterEqual(p.restarts, 1)
                    self.assertTrue(p.check({"d": "x"})["d"]["ok"])          # 重启后照常可用
        finally:
            os.unlink(path)

    def test_deadline(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(FAKE_SERVE); path = f.name
        try:
            with hero_checker.CheckerPool(concurrency=1, timeout=30, cmd=[sys.executable, path]) as p:
                r = p.check({"hang2": "x"}, deadline=time.time() + 1)
                self.assertTrue(r["hang2"].get("budget"))
        finally:
            os.unlink(path)

    def test_unstartable(self):
        with hero_checker.CheckerPool(cmd=["/nonexistent/node"]) as p:
            r = p.check({"a": "x"})
        self.assertTrue(r["a"]["infra"])


@unittest.skipUnless(HAVE_VERIFY, "需要 Chromium + puppeteer-core")
class RealBrowser(unittest.TestCase):
    def test_infinite_loop_module_times_out_alone(self):
        chars = render.fix_colors([dict(c) for c in CHARS])
        data = {"series": {"te": [.2, .5], "in": [.3, .6]}}
        good = HP.build_doc(HP.page_payload(HP.validate(GOOD, IDS, "")[0], 1, 12, chars, data), chars)
        loop = good.replace("</body>", "<script>addEventListener('load',function(){setTimeout(function(){for(;;){}},50)})</script></body>")
        with hero_checker.CheckerPool(concurrency=2, timeout=20) as p:
            t = time.time()
            r = p.check({"g1": good, "loop": loop, "g2": good})
            dt = time.time() - t
        self.assertTrue(r["g1"]["ok"], r["g1"]["errors"]); self.assertTrue(r["g2"]["ok"], r["g2"]["errors"])
        self.assertFalse(r["loop"]["ok"])
        self.assertLess(dt, 80)


if __name__ == "__main__":
    unittest.main()
