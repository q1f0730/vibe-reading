"""模型无关脚手架：provider mock（三种协议 × 流式/非流式）、探针与分档、按档策略、加密存储、统一护栏、弱模型下限。
全部走本地假模型服务器（tests/mockllm.py），不联网、不需要 key。"""
import json, os, re, sys, threading, time, unittest, pathlib
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from vibereading import llm, probe, tiers, planner, quality, segment, render, pipeline  # noqa: E402
from tests.mockllm import MockLLM  # noqa: E402

KEY = "sk-test-SECRET-1234567890abcdef"
KONG = (ROOT / "examples" / "kongyiji" / "kongyiji.txt").read_text(encoding="utf-8")


def client(m, kind="openai", **kw):
    return llm.Client(llm.ModelConfig(kind=kind, base_url=m.url(kind), model=kw.pop("model", "mock-1"), api_key=KEY, **kw))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = MockLLM()
        cls._sleep = llm._sleep_for
        llm._sleep_for = lambda *a, **k: 0

    @classmethod
    def tearDownClass(cls):
        cls.m.close(); llm._sleep_for = cls._sleep

    def setUp(self):
        self.m.state.clear(); self.m.requests.clear(); self.m.max_inflight = 0


class ProviderTest(Base):
    MSG = [{"role": "system", "content": "你只输出 JSON。"}, {"role": "user", "content": '输出 JSON：键 n 为整数 3，键 words 为数组 ["门","窗","灯"]。'}]

    def test_three_protocols_stream_and_plain(self):
        for kind in llm.KINDS:
            for stream in (False, True):
                with self.subTest(kind=kind, stream=stream):
                    r = client(self.m, kind, stream=stream).complete(self.MSG, 300, json_mode=True)
                    self.assertEqual(json.loads(r.text)["n"], 3, r)
                    self.assertEqual(r.finish, "stop")
        paths = {x["path"] for x in self.m.requests}
        self.assertIn("/v1/chat/completions", paths); self.assertIn("/v1/messages", paths)
        self.assertTrue(any(":streamGenerateContent" in p for p in paths)); self.assertTrue(any(p.endswith(":generateContent") for p in paths))
        # 密钥只出现在各家约定的请求头里
        hs = [{k.lower(): v for k, v in x["headers"].items()} for x in self.m.requests]
        self.assertTrue(any(h.get("authorization") == "Bearer " + KEY for h in hs))
        self.assertTrue(any(h.get("x-api-key") == KEY for h in hs)); self.assertTrue(any(h.get("x-goog-api-key") == KEY for h in hs))
        self.assertFalse(any(KEY in x["path"] for x in self.m.requests))

    def test_schema_modes_per_protocol(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        client(self.m, "openai").complete(self.MSG, 100, schema=schema)
        client(self.m, "anthropic").complete(self.MSG, 100, schema=schema)
        client(self.m, "gemini").complete(self.MSG, 100, schema=schema)
        b = [x["body"] for x in self.m.requests]
        self.assertEqual(b[0]["response_format"]["type"], "json_schema")
        self.assertEqual(b[1]["tool_choice"], {"type": "tool", "name": "emit"})
        self.assertEqual(b[2]["generationConfig"]["responseJsonSchema"], schema)

    def test_429_backoff_and_retry_after(self):
        self.m.state["rate_limit_n"] = 3
        st = planner.Stats()
        r = client(self.m).complete(self.MSG, 100, stats=st)
        self.assertEqual(json.loads(r.text)["n"], 3)
        self.assertEqual(st.transient, 3); self.assertEqual(st.calls, 4)

    def test_concurrency_cap(self):
        self.m.state["delay"] = 0.15
        c = client(self.m, concurrency=2)
        ts = [threading.Thread(target=c.complete, args=(self.MSG, 50)) for _ in range(6)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertLessEqual(self.m.max_inflight, 2)

    def test_content_filter_unified(self):
        self.m.state["filter_words"] = ["门"]
        for kind in llm.KINDS:
            with self.subTest(kind=kind):
                with self.assertRaises(llm.ContentFiltered):
                    client(self.m, kind).complete(self.MSG, 100)

    def test_unsupported_response_format_degrades(self):
        self.m.state["weak"] = True
        c = client(self.m)
        r = c.complete(self.MSG, 300, schema={"type": "object"})
        self.assertEqual(planner.parse_json(r.text)["n"], 3)
        self.assertTrue(c.no_schema and c.no_json_mode)
        self.assertNotIn("response_format", self.m.requests[-1]["body"])

    def test_reasoning_starvation_downgrades(self):
        self.m.state["starve_reasoning"] = True
        for kind, start in (("openai", "high"), ("anthropic", "high"), ("gemini", "medium")):
            with self.subTest(kind=kind):
                c = client(self.m, kind, reasoning=start)
                st = planner.Stats()
                r = c.complete(self.MSG, 300, stats=st)
                self.assertEqual(json.loads(r.text)["n"], 3)
                self.assertIn(c.reason_level, ("off", "minimal"))
                self.assertTrue(any("推理" in w for w in st.warnings))
        # 千问方言：enable_thinking=true 吃光 → 关推理
        c = llm.Client(llm.ModelConfig(kind="openai", base_url=self.m.url(), model="qwen3.8-max", api_key=KEY, reasoning="medium"))
        c.complete(self.MSG, 300)
        self.assertIs(self.m.requests[-1]["body"].get("enable_thinking"), False)

    def test_key_never_in_errors_or_repr(self):
        self.m.state["echo_key_on_500"] = True
        c = client(self.m)
        with self.assertRaises(llm.LLMError) as cm:
            c.complete(self.MSG, 100)
        self.assertNotIn(KEY, str(cm.exception)); self.assertNotIn(KEY, cm.exception.body)
        self.assertNotIn(KEY, repr(c.cfg)); self.assertNotIn(KEY, json.dumps(c.cfg.to_public()))
        self.assertEqual(c.cfg.to_public()["key"], "…cdef")
        self.assertNotIn("sk-abcdefghijklmnop", llm.redact("bad key sk-abcdefghijklmnop here"))
        self.assertNotIn("AIzaSyA1234567890123456789", llm.redact("key=AIzaSyA1234567890123456789"))

    def test_max_output_cap_and_length(self):
        self.m.state["max_output"] = 5
        r = client(self.m).complete(self.MSG, 300)
        self.assertEqual(r.finish, "length")


class ProbeTierTest(Base):
    def test_strong_mock_is_A(self):
        p = probe.run(client(self.m))
        self.assertTrue(p["ok"]); self.assertEqual(p["tier"], "A", p.get("tier_reasons"))
        self.assertTrue(p["json_mode"] and p["json_schema"] and p["tools"])
        self.assertEqual(p["filter_blocked"], 0); self.assertGreaterEqual(p["zh_long"], 0.9)
        self.assertNotIn(KEY, json.dumps(p, ensure_ascii=False))

    def test_weak_mock_is_C_and_filter_counted(self):
        self.m.state.update(weak=True, max_output=900, filter_words=["香案"])
        p = probe.run(client(self.m))
        self.assertFalse(p["json_mode"]); self.assertFalse(p["json_schema"]); self.assertTrue(p["json_prompt"])
        self.assertEqual(p["filter_blocked"], 1)
        self.assertEqual(p["tier"], "C", p.get("tier_reasons"))

    def test_quick_probe_only_pings(self):
        p = probe.run(client(self.m), quick=True)
        self.assertTrue(p["ok"]); self.assertEqual(len(self.m.requests), 1)

    def test_unreachable(self):
        c = llm.Client(llm.ModelConfig(kind="openai", base_url="http://127.0.0.1:9", model="x", api_key=KEY, timeout=5))
        with mock.patch.object(llm, "MAX_TRANSIENT", 0):
            p = probe.run(c)
        self.assertFalse(p["ok"]); self.assertEqual(p["tier"], "C")

    def test_classify_rules(self):
        good = dict(ok=True, json_schema=True, json_mode=True, json_prompt=True, zh_long=1.0, max_output_ok=16000, context_ok=32000, filter_blocked=0)
        self.assertEqual(probe.classify(good)[0], "A")
        self.assertEqual(probe.classify(dict(good, filter_blocked=1))[0], "B")
        self.assertEqual(probe.classify(dict(good, json_schema=False, json_mode=False))[0], "B")
        self.assertEqual(probe.classify(dict(good, zh_long=0.7))[0], "B")
        self.assertEqual(probe.classify(dict(good, json_schema=False, json_mode=False, json_prompt=False))[0], "C")
        self.assertEqual(probe.classify(dict(good, max_output_ok=1000))[0], "C")
        self.assertEqual(probe.classify(dict(good, context_ok=4000))[0], "C")
        self.assertEqual(probe.classify(dict(ok=False))[0], "C")

    def test_strategy_by_tier(self):
        A, B, C = (tiers.strategy_for(t) for t in "ABC")
        self.assertGreater(A.batch_paras, B.batch_paras); self.assertGreater(B.batch_paras, C.batch_paras)
        self.assertLess(A.few_shot, C.few_shot); self.assertTrue(C.enum_in_prompt); self.assertFalse(A.enum_in_prompt)
        self.assertEqual(C.hero_max_shots, 2); self.assertEqual(A.hero_max_shots, 4)
        self.assertTrue(A.code_mode); self.assertFalse(B.code_mode); self.assertFalse(C.code_mode)
        # 探针档案收紧：输出小 → 批次小；无 JSON 模式 → prompt 约束；A 档但探针说不行 → 关代码模式
        s = tiers.strategy_for("A", {"json_mode": False, "json_schema": False, "max_output_ok": 2400, "context_ok": 12000, "code_mode_ok": False})
        self.assertEqual(s.structured, "prompt"); self.assertLessEqual(s.batch_paras, 17); self.assertFalse(s.code_mode)
        self.assertLessEqual(s.outline_budget, 4200)


def _engine(m, tier, **kw):
    c = client(m, **kw)
    prof = {"json_mode": tier != "C", "json_schema": tier != "C", "max_output_ok": 6000 if tier == "C" else 16000, "context_ok": 64000}
    return tiers.Engine(c, prof, tier, source="eval")


class PlannerTierTest(Base):
    def setUp(self):
        super().setUp()
        self.seg = segment.split(KONG)

    def test_tier_changes_batching_and_prompts(self):
        sizes = {}
        for t in "AC":
            self.m.requests.clear(); self.m.state.clear()
            plan, st = planner.plan(self.seg, engine=_engine(self.m, t))
            self.assertEqual(len(plan["paras"]), self.seg["count"])
            sizes[t] = max(len(b) for b in self.m.state["detail_batches"])
            sysd = [x["body"]["messages"][0]["content"] for x in self.m.requests if "逐字子串" in x["body"]["messages"][0]["content"]]
            usr = [x["body"]["messages"][1]["content"] for x in self.m.requests if "逐字子串" in x["body"]["messages"][0]["content"]]
            if t == "C":
                self.assertTrue(all("格式示例" in s for s in sysd)); self.assertTrue(all("【严格要求】" in u for u in usr))
                self.assertTrue(all("response_format" not in x["body"] for x in self.m.requests))
            else:
                self.assertFalse(any("格式示例" in s for s in sysd)); self.assertFalse(any("【严格要求】" in u for u in usr))
        self.assertLessEqual(sizes["C"], tiers.strategy_for("C").batch_paras)
        self.assertGreater(sizes["A"], sizes["C"])

    def test_weak_model_repairs_and_floor_holds(self):
        self.m.state.update(weak=True, drop_first_detail=True, bad_visuals=True)
        eng = _engine(self.m, "C")
        plan, st = planner.plan(self.seg, engine=eng)
        guard = plan.pop("_guard")
        self.assertEqual(len(plan["paras"]), self.seg["count"])
        self.assertGreaterEqual(st.retries, 1)                         # 漏段 / 不符组件 → 带错误回灌修复
        self.assertTrue(any("组件与原文不符" in json.dumps(x, ensure_ascii=False) for x in st.log if "invalid" in x))
        self.assertGreater(guard["inconsistent"], 0)                    # 修不好的不符组件被护栏去掉
        self.assertEqual(quality.visual_issues(plan["visuals"], self.seg["segments"], plan["scenes"]), [])
        html, _ = render.render(self.seg, plan, title="孔乙己")
        q = quality.build_report(self.seg, plan, st.as_dict(), None, html, None, guard, 1.0, eng.describe())
        self.assertTrue(q["floor_ok"], q["floor"]); self.assertEqual(q["tier"], "C")
        self.assertEqual(q["scores"]["verbatim"], 100.0)

    def test_content_filter_without_fallback_degrades_locally(self):
        self.m.state["filter_words"] = ["打折了腿"]
        plan, st = planner.plan(self.seg, engine=_engine(self.m, "A"))
        self.assertEqual(len(plan["paras"]), self.seg["count"])
        self.assertGreaterEqual(st.content_filtered, 1)
        self.assertGreater(st.rule_paras, 0); self.assertLess(st.rule_paras, self.seg["count"])

    def test_content_filter_uses_engine_fallback(self):
        m2 = MockLLM()
        try:
            self.m.state["filter_words"] = ["打折了腿"]
            eng = _engine(self.m, "A"); eng.fallback = client(m2, model="fallback-1")
            plan, st = planner.plan(self.seg, engine=eng)
            self.assertGreaterEqual(st.fallbacks, 1); self.assertEqual(st.rule_paras, 0)
            self.assertTrue(any(r["body"]["model"] == "fallback-1" for r in m2.requests))
        finally:
            m2.close()


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.seg = segment.split(KONG); self.S = self.seg["segments"]

    def test_visual_issues(self):
        sc = [{"start": 0, "end": len(self.S) - 1, "tone": "day"}]
        door_at = next(i for i, s in enumerate(self.S) if "门" in s["text"]) if any("门" in s["text"] for s in self.S) else None
        vis = [{"at": 0, "type": "car_night", "params": {}}, {"at": 1, "type": "object", "params": {"thing": "phone"}},
               {"at": 2, "type": "timeline", "params": {"labels": ["中秋", "火星"]}}, {"at": 3, "type": "silence", "params": {}}]
        why = [w for _, w in quality.visual_issues(vis, self.S, sc)]
        self.assertEqual(len(why), 3, why)
        self.assertTrue(any("火星" in w for w in why))
        ok = [{"at": i, "type": "object", "params": {"thing": "wine_cup"}} for i, s in enumerate(self.S) if "酒" in s["text"]][:1]
        self.assertEqual(quality.visual_issues(ok, self.S, sc), [])

    def test_guard_caps_and_strips_leaky_caption(self):
        txt = self.S[3]["text"]
        plan = {"scenes": [{"start": 0, "end": len(self.S) - 1, "tone": "day", "name": "x"}],
                "visuals": [{"at": 3, "type": "silence", "params": {"length": "short", "caption": txt[:12]}}] +
                           [{"at": 5, "type": "silence", "params": {}} for _ in range(3)]}
        g = quality.guard_plan(plan, self.S, None, tiers.strategy_for("C"))
        self.assertEqual(len(plan["visuals"]), 1); self.assertEqual(g["capped"], 3); self.assertEqual(g["caption_leaks"], 1)
        self.assertNotIn("caption", plan["visuals"][0]["params"])

    def test_verbatim_static_detects_tamper(self):
        plan, _ = planner.validate_plan(json.loads((ROOT / "examples/kongyiji/kongyiji.plan.json").read_text(encoding="utf-8")), self.seg)
        html, _ = render.render(self.seg, plan, title="孔乙己")
        v = quality.verbatim_static(html, self.seg)
        self.assertTrue(v["identical"], v)
        t = self.S[4]["text"]
        bad = html.replace(t[:10], t[:9] + "X", 1)
        self.assertFalse(quality.verbatim_static(bad, self.seg)["identical"])

    def test_hero_words(self):
        comp = {"shots": [{"prim": "typeset", "params": {"words": ["量子", "火箭"]}}]}
        self.assertTrue(quality.hero_word_issues(comp, "孔乙己喝酒"))
        self.assertEqual(quality.hero_word_issues({"shots": [{"prim": "press", "params": {"word": "酒债"}}]}, "孔乙己还欠十九个钱，喝酒"), [])


class NormalizeTest(unittest.TestCase):
    """评测里真实遇到的模型输出变体（千问 Anthropic 接口 / Mercury），无歧义地修正，不让整批降级。"""
    def test_variants(self):
        o = planner.normalize({"paras": [{"i": 0, "sp": "", "with": ["wo"], "int": 0.25, "ten": 0.35, "emb": 0.0, "who": ""},
                                         {"i": 1, "sp": 0, "with": "wo", "intimacy": 0.3, "tension": 0.2, "blush": 0.0},
                                         [2, None, [], "0.1", "0.2", "0", 0]],
                               "visuals": '[{"at": 1, "type": "door", "params": {}}]', "quotes": []})
        self.assertEqual(o["paras"][0], [0, "", ["wo"], 0.25, 0.35, 0.0, ""])
        self.assertEqual(o["paras"][1], [1, "", ["wo"], 0.3, 0.2, 0.0, ""])
        self.assertEqual(o["paras"][2], [2, "", [], 0.1, 0.2, 0.0, ""])
        self.assertEqual(o["visuals"][0]["type"], "door")
        c = planner.normalize({"characters": [{"id": "a", "name": "甲"}, {"id": "b", "name": "乙", "color": "#123456"}], "scenes": []})
        self.assertRegex(c["characters"][0]["color"], r"^#[0-9a-f]{6}$"); self.assertEqual(c["characters"][1]["color"], "#123456")


class CryptoTest(unittest.TestCase):
    def test_roundtrip_binding_and_missing_key(self):
        from vibereading.web import crypto
        with mock.patch.dict(os.environ, {"VR_SECRET_KEY": "x" * 40}):
            tok = crypto.encrypt(KEY, 7)
            self.assertTrue(tok.startswith("v1:")); self.assertNotIn(KEY, tok); self.assertNotIn("SECRET", tok)
            self.assertEqual(crypto.decrypt(tok, 7), KEY)
            with self.assertRaises(Exception):
                crypto.decrypt(tok, 8)                        # 绑定 user_id
            self.assertNotEqual(crypto.encrypt(KEY, 7), tok)  # 随机 nonce
        with mock.patch.dict(os.environ, {"VR_SECRET_KEY": "y" * 40}):
            with self.assertRaises(Exception):
                crypto.decrypt(tok, 7)                        # 换主密钥解不开
        with mock.patch.dict(os.environ, {"VR_SECRET_KEY": ""}):
            with self.assertRaises(crypto.CryptoUnavailable):
                crypto.encrypt(KEY, 1)


if __name__ == "__main__":
    unittest.main()
