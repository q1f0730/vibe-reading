"""关键场景动画 · 原语模式（默认）测试：合成文本 + 模拟 LLM，不含任何版权作品、不发真实请求。
python3 -m unittest tests.test_hero_prims -v
- 编排校验：未知原语 / 空档 / 叠层 / 暗色上限 / 具象原语要有对应物件 / 本章次数上限 / 原文重合 / 台词 / 标点 / 重复词 / chart 自动修正
- 规则编排：一定合格，遵守次数上限
- 选场景：按前/中/后分区各取 1 个；模型扎堆在后半章时，为空的分区补一次调用；落在日期行/脚注上的挪到正文段
- 编排流程：一次通过 / 修一次 / 规则编排；标题若是原文台词会被换掉
- 渲染：编排 JSON 进 .hs-comp，原语库全页只放一份（#hs-lib）；落在日期行/脚注上的 hero 挪到正文段
- 浏览器（有 Chromium 时）：11 个原语全部通过 hero_check（报错/外链/溢出/空白/对比度/随滚动变化/帧率/减弱动效）；页面原文逐段一致
"""
import json, os, pathlib, re, subprocess, tempfile, unittest
from unittest import mock

from vibereading import segment, planner, render, hero, hero_prims as HP
from tests.test_web import HAVE_VERIFY
from tests.test_hero import synth_text, synth_plan, CHARS, SELECT_JSON

ROOT = pathlib.Path(__file__).resolve().parent.parent
IDS = {"aqing", "laozhou"}
SRC = "阿青推开门走进来，老周看着阿青，过了一会儿说：今天的风很大。屋里很安静。"


def comp(*shots, slug="厨房·傍晚"):
    return {"slug": slug, "shots": [dict(prim=p, **{"from": a, "to": b}, params=pr) for p, a, b, pr in shots]}


GOOD = comp(("window", 0, .5, {"word": "余温", "from": "noon", "to": "dusk"}),
            ("orbit", .44, 1, {"a": "aqing", "b": "laozhou", "dist": [.9, .3], "tension": [.3, .7], "measure": "隔桌"}))


class Validate(unittest.TestCase):
    def test_good(self):
        c, errs = HP.validate(GOOD, IDS, SRC)
        self.assertEqual(errs, []); self.assertEqual([s["prim"] for s in c["shots"]], ["window", "orbit"])

    def test_structure_errors(self):
        cases = [
            (comp(("bogus", 0, 1, {}), ("typeset", 0, 1, {"words": ["门"]})), "prim 必须是"),
            (comp(("typeset", 0, .4, {"words": ["门"]}), ("press", .6, 1, {"word": "余温"})), "空档"),
            (comp(("typeset", .1, .6, {"words": ["门"]}), ("press", .5, 1, {"word": "余温"})), "p=0"),
            (comp(("typeset", 0, .55, {"words": ["门"]}), ("press", .3, 1, {"word": "余温"})), "重叠"),
            (comp(("develop", 0, .4, {"word": "旧照"}), ("focus", .36, .7, {"far": "远", "near": "近"}), ("passing", .66, 1, {})), "暗色镜头 3 个"),
            (comp(("develop", 0, .55, {"word": "旧照"}), ("focus", .5, 1, {"far": "远处", "near": "近处"})), "全部是暗色"),
            (comp(("orbit", 0, .6, {"a": "nobody", "b": "aqing"}), ("press", .55, 1, {"word": "余温"})), "不是人物 id"),
            (comp(("typeset", 0, .55, {"words": ["余温"]}), ("press", .5, 1, {"word": "余温"})), "重复"),
        ]
        for c, why in cases:
            _, errs = HP.validate(c, IDS, SRC)
            self.assertTrue(any(why in e for e in errs), (why, errs))

    def test_text_rules(self):
        # 原文 ≥4 字的片段、台词口吻、标点都不许上画面
        for word, why in (("的风很大", "原文重合"), ("别走", "台词"), ("你好", "台词"), ("门，开", "标点")):
            _, errs = HP.validate(comp(("press", 0, .55, {"word": word}), ("typeset", .5, 1, {"words": ["余温"]})), IDS, SRC)
            self.assertTrue(any(why in e for e in errs), (word, errs))
        self.assertIsNone(HP.leaks("风", SRC)); self.assertEqual(HP.leaks("今天的风", SRC), "今天的风")

    def test_needs_and_chapter_limits(self):
        c = comp(("spoon", 0, .55, {"word": "反光"}), ("press", .5, 1, {"word": "余温"}))
        _, errs = HP.validate(c, IDS, SRC, desc_text="傍晚的客厅，两人对坐")
        self.assertTrue(any("不能用 spoon" in e for e in errs), errs)
        _, errs = HP.validate(c, IDS, SRC, desc_text="厨房台面上的勺子和果酱罐")
        self.assertEqual(errs, [])
        _, errs = HP.validate(c, IDS, SRC, desc_text="勺子", used=["spoon"])          # 同一章 spoon 只能一次
        self.assertTrue(any("用够次数" in e for e in errs), errs)
        _, errs = HP.validate(comp(("press", 0, .55, {"word": "余温"}), ("typeset", .5, 1, {"words": ["门"]})), IDS, SRC, used=["press"])
        self.assertEqual(errs, [])                                                     # press 可以两次
        self.assertEqual(HP.blocked(["press", "press", "ink", "typeset"]), {"press", "ink"})

    def test_chart_autorepair_and_truncation(self):
        c, errs = HP.validate(comp(("chart", 0, .6, {"series": [0, 1, "te"], "notes": [{"x": .3}, {"x": .7, "word": "转折"}]}),
                                   ("typeset", .55, 1, {"words": ["余温很长很长很长很长"], "zzz": 1})), IDS, SRC)
        self.assertEqual(errs, [])
        self.assertEqual(c["shots"][0]["params"]["series"], ["te"]); self.assertEqual(c["shots"][0]["params"]["notes"], [{"x": .7, "word": "转折"}])
        self.assertEqual(c["shots"][1]["params"], {"words": ["余温很长很长很长"]})        # 截断到 8 字、去掉未知键

    def test_rule_compose_always_valid(self):
        hs = [{"title": "推门", "objects": ["门", "楼梯"], "characters": [{"id": "aqing"}, {"id": "laozhou"}], "curve": [{"t": 0, "beat": "a", "intimacy": .2, "tension": .3}, {"t": 1, "beat": "b", "intimacy": .8}]},
              {"title": "车里", "setting": "夜路", "characters": []},
              {"title": "今天的风", "objects": [], "characters": [{"id": "aqing"}]},
              {}]
        for h in hs:
            for used in ([], ["door", "orbit", "passing", "window", "typeset", "typeset", "press", "press", "ink"]):
                c = HP.rule_compose(h, IDS, SRC, used)
                _, errs = HP.validate(c, IDS, SRC)
                self.assertEqual(errs, [], (h, used, c))
                for s in c["shots"]:
                    self.assertNotIn(s["prim"], HP.blocked(used) - {"typeset"}, (h, used, c))
        self.assertEqual(HP.rule_compose(hs[0], IDS, SRC)["shots"][0]["prim"], "door")

    def test_build_doc(self):
        payload = HP.page_payload(HP.validate(GOOD, IDS, SRC)[0], 1, 12, render.fix_colors([dict(c) for c in CHARS]), {"series": {"te": [.1, .2]}})
        doc = HP.build_doc(payload, render.fix_colors([dict(c) for c in CHARS]))
        self.assertIn("default-src 'none'", doc); self.assertIn("window.VRP", doc); self.assertIn("VRP.mount(", doc)
        self.assertNotIn("http://www.w3.org/2000/svg\"", doc.split("<script>VRP.mount")[1])
        tail = HP.mount_tail({"slug": "</script><b>"})
        self.assertNotIn("</script><b>", tail)


def fake_select_back_heavy(seg):
    """模型把 3 个 hero 都选在后半章 → 应只保留一个后段的，并为前段/中段补一次选择。"""
    calls = {"n": 0}
    def sel(o_heroes):
        return {"heroes": o_heroes}
    first = [dict(SELECT_JSON["heroes"][0], scene=2, at=30), dict(SELECT_JSON["heroes"][0], scene=2, at=36), dict(SELECT_JSON["heroes"][0], scene=2, at=39)]
    second = [dict(SELECT_JSON["heroes"][0], scene=0, at=0), dict(SELECT_JSON["heroes"][0], scene=1, at=20)]   # at=0 是日期行
    return [sel(first), sel(second)]


class FakeMercury:
    def __init__(self, selects, comps):
        self.selects = list(selects); self.comps = list(comps); self.calls = []

    def __call__(self, messages, model, max_tokens, stats, provider="qwen", json_mode=True, extra_override=None, timeout=None):
        sysmsg = messages[0]["content"]; self.calls.append((sysmsg[:16], messages)); stats.calls += 1
        if sysmsg.startswith("你是图文长篇"):
            return json.dumps(self.selects.pop(0) if self.selects else {"heroes": []}, ensure_ascii=False), "stop"
        if sysmsg.startswith("你是 NYT / The Pudding 图像部的艺术指导"):
            return json.dumps(self.comps.pop(0), ensure_ascii=False) if self.comps else "{}", "stop"
        raise AssertionError("unexpected call " + sysmsg[:30])


class Orchestration(unittest.TestCase):
    def setUp(self):
        self.seg = segment.split(synth_text())
        self.plan = synth_plan(self.seg)

    def ok_check(self, items, chars, hs):
        return {k: [] for k in items}

    def test_zones_spread_and_snap(self):
        zs = hero.zones(self.seg, 0, self.seg["count"] - 1, 3)
        self.assertEqual(zs[0][0], 0); self.assertEqual(zs[-1][1], self.seg["count"] - 1)
        comps = [GOOD, comp(("ink", 0, .55, {"word": "余温"}), ("press", .5, 1, {"word": "风声"})),
                 comp(("typeset", 0, .55, {"words": ["楼梯"], "mode": "mask"}), ("press", .5, 1, {"word": "回头"}))]
        fake = FakeMercury(fake_select_back_heavy(self.seg), comps)
        with mock.patch.object(planner, "chat", fake), mock.patch.object(hero, "_check_comps", self.ok_check):
            hs = hero.run(self.seg, self.plan, per_chapter=3)
        ats = sorted(h["at"] for h in self.plan["heroes"])
        self.assertEqual(len(ats), 3, (ats, hs.warnings))
        self.assertEqual(sorted(hero.zone_of(zs, a) for a in ats), [0, 1, 2])          # 前/中/后各一个
        for a in ats:
            self.assertEqual(self.seg["segments"][a]["role"], "text")                  # 日期行上的被挪到正文段
        sels = [m for tag, m in fake.calls if tag.startswith("你是图文长篇")]
        self.assertEqual(len(sels), 2)
        self.assertIn("这次只为这些分区各选 1 个", sels[1][1]["content"])
        self.assertTrue(all(h["kind"] == "prims" and h["status"] == "ok" and h["comp"]["shots"] for h in self.plan["heroes"]))
        self.assertEqual(sorted(h["comp"]["fig"] for h in self.plan["heroes"]), [1, 2, 3])

    def test_fix_then_rule(self):
        sel = {"heroes": [dict(SELECT_JSON["heroes"][0], title="今天的风很大"), dict(SELECT_JSON["heroes"][1], at=33)]}
        bad = comp(("press", 0, .55, {"word": "今天的风"}), ("typeset", .5, 1, {"words": ["余温"]}))     # 原文重合
        # 调用顺序（章内逐个 hero 独立流水线）：h1-1 首次（原文重合）→ h1-1 修复（成功）→ h1-2 首次（坏）→ h1-2 修复（仍坏 → 规则编排）
        fake = FakeMercury([sel], [bad, comp(("ink", .2, 1, {"word": "余温"}), ("press", 0, .3, {"word": "风"})), {"shots": "nope"}, {"shots": []}])
        with mock.patch.object(planner, "chat", fake), mock.patch.object(hero, "_check_comps", self.ok_check):
            hs = hero.run(self.seg, self.plan, per_chapter=2)
        H = {h["id"]: h for h in self.plan["heroes"]}
        self.assertEqual(H["h1-1"]["status"], "fixed"); self.assertEqual(H["h1-1"]["attempts"], 2)
        self.assertEqual(H["h1-2"]["status"], "rule"); self.assertTrue(H["h1-2"]["comp"]["shots"])
        self.assertEqual((hs.passed_first, hs.passed_fixed, hs.rule, hs.fallback), (0, 1, 1, 0))
        self.assertNotIn("今天的风", H["h1-1"]["title"])                                   # 标题是原文 → 被换掉
        fix = [m for tag, m in fake.calls if tag.startswith("你是 NYT") and len(m) > 2][0]
        self.assertIn("原文重合", fix[-1]["content"])
        for tag, m in fake.calls:   # 编排请求里没有原文段落
            if tag.startswith("你是 NYT"):
                self.assertNotIn("今天的风很大。", m[1]["content"])

    def test_browser_reject_goes_to_fix(self):
        sel = {"heroes": SELECT_JSON["heroes"][:1]}
        seen = []
        def chk(items, chars, hs):
            seen.append(sorted(items)); return {k: (["画面随滚动变化太小"] if len(seen) == 1 else []) for k in items}
        fake = FakeMercury([sel], [GOOD, comp(("press", 0, .55, {"word": "余温"}), ("ink", .5, 1, {"word": "风"}))])
        with mock.patch.object(planner, "chat", fake), mock.patch.object(hero, "_check_comps", chk):
            hero.run(self.seg, self.plan, per_chapter=1)
        self.assertEqual(self.plan["heroes"][0]["status"], "fixed"); self.assertEqual(len(seen), 2)

    def test_render_comp_and_snap(self):
        c = HP.page_payload(HP.validate(GOOD, IDS, "")[0], 1, 12, CHARS, {"series": {"te": [.1, .5]}})
        n = self.seg["count"]
        self.plan["heroes"] = [{"id": "h1-1", "kind": "prims", "at": 0, "title": "推门", "comp": c, "status": "ok", "seconds": 12},    # 0 = 日期行
                               {"id": "h1-2", "kind": "prims", "at": 20, "title": "窗边", "comp": dict(c, fig=2), "status": "ok", "seconds": 10}]
        html, warns = render.render(self.seg, self.plan)
        self.assertEqual(html.count('class="hs"'), 2); self.assertEqual(html.count('class="hs-comp"'), 2)
        self.assertEqual(html.count('id="hs-lib"'), 1)
        self.assertTrue(any("挪到正文段" in w for w in warns), warns)
        first = html.index('class="hs"'); self.assertGreater(first, html.index('data-src="1"'))
        lib = re.search(r'<script type="application/json" id="hs-lib">(.*?)</script>', html, flags=re.S).group(1)
        self.assertNotIn("<", lib); self.assertIn("window.VRP", json.loads(lib))
        self.assertIn("VRP.mount(", html)                                                 # 父页面拼 srcdoc 的代码
        plain, _ = render.render(self.seg, dict(self.plan, heroes=[]))
        para = lambda h: re.findall(r'<(?:p|h2)[^>]*data-src="\d+"[^>]*>.*?</(?:p|h2)>', h, flags=re.S)
        self.assertEqual(para(html), para(plain))
        # 复用分镜时编排会被重新校验：被篡改成原文的文字 → 回退
        bad = dict(c, shots=[dict(c["shots"][0], params={"word": "今天的风很大"}), c["shots"][1]])
        out = hero.sanitize_heroes([{"id": "x", "at": 3, "comp": bad}], n, IDS, "今天的风很大")
        self.assertIsNone(out[0]["comp"]); self.assertEqual(out[0]["status"], "fallback")


@unittest.skipUnless(HAVE_VERIFY, "需要 Chromium + puppeteer-core")
class Browser(unittest.TestCase):
    def test_all_prims_pass_checks(self):
        chars = render.fix_colors([dict(c) for c in CHARS])
        data = {"series": {"te": [.2, .4, .7, .5], "in": [.3, .5, .6, .8], "bl": [0, .4, .1, 0]}}
        groups = [
            comp(("typeset", 0, .28, {"words": ["余温", "厨房"], "mode": "assemble", "accent": 1}), ("develop", .25, .52, {"word": "旧照"}),
                 ("ink", .49, .76, {"word": "风", "drops": 2}), ("press", .73, 1, {"word": "回头", "seal": "印"})),
            comp(("window", 0, .28, {"word": "傍晚"}), ("focus", .25, .52, {"far": "远处", "near": "她"}),
                 ("orbit", .49, .76, {"a": "aqing", "b": "laozhou", "dist": [.9, .2], "tension": [.2, .8]}), ("typeset", .73, 1, {"words": ["楼梯", "回声"], "mode": "mask"})),
            comp(("door", 0, .28, {"word": "门缝"}), ("spoon", .25, .52, {"word": "反光"}),
                 ("passing", .49, .76, {"word": "夜路"}), ("chart", .73, 1, {"notes": [{"x": .5, "word": "转折"}]})),
            comp(("typeset", 0, .55, {"words": ["距离", "一步"], "mode": "scatter"}), ("typeset", .5, 1, {"words": ["沉默", "车里"], "mode": "vertical"})),
        ]
        docs = {f"g{i}": HP.build_doc(HP.page_payload(g, i + 1, 14, chars, data), chars) for i, g in enumerate(groups)}
        r = hero.browser_check(docs, timeout=600)
        for k, v in r.items():
            self.assertTrue(v["ok"], (k, v["errors"], v["metrics"]))
            self.assertGreaterEqual(v["metrics"]["contrast"]["p1"], 3)
        self.assertEqual(set(HP.PRIMS), {s["prim"] for g in groups for s in g["shots"]})

    def test_page_with_prims_still_verifies(self):
        seg = segment.split(synth_text()); plan = synth_plan(seg)
        c = HP.page_payload(HP.validate(GOOD, IDS, "")[0], 1, 12, CHARS, {"series": {"te": [.1, .5]}})
        plan["heroes"] = [{"id": "h1-1", "kind": "prims", "at": 10, "title": "推门", "comp": c, "status": "ok", "seconds": 10}]
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "p.html"; segp = pathlib.Path(d) / "p.segments.json"
            html, _ = render.render(seg, plan)
            out.write_text(html, encoding="utf-8"); segp.write_text(json.dumps(seg, ensure_ascii=False), encoding="utf-8")
            r = subprocess.run(["node", str(ROOT / "tools/verify.js"), str(out), str(segp)], capture_output=True, text=True, timeout=300)
            rep = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(rep["ok"], rep); self.assertTrue(rep["text_identical"]); self.assertEqual(rep["external_requests"], [])


if __name__ == "__main__":
    unittest.main()
