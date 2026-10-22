"""关键场景动画在“用户真实浏览器”条件下的回归测试（合成文本，不含版权作品）。
python3 -m unittest tests.test_hero_realbrowser -v

事故（2026-10-22）：用户用 file:// 打开导出的整本 HTML，hero 画框里一片空白/纯黑，元素全挤在左上角。
根因：iframe 刚创建时视口是 0×0（Firefox、进程隔离的沙箱 iframe），原语库按 0×0 一次性布局，之后浏览器不一定补发 resize，
于是再也不重排；ink/focus 原语在 0 宽时 createImageData 直接抛错。服务器 headless 校验从来没在这种条件下测过。
"""
import json, pathlib, subprocess, tempfile, unittest

from vibereading import render, hero, hero_prims as HP, segment
from tests.test_web import HAVE_VERIFY
from tests.test_hero import synth_text, synth_plan, CHARS
from tests.test_hero_prims import comp, IDS

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = (ROOT / "vibereading" / "template.html").read_text(encoding="utf-8")


class Static(unittest.TestCase):
    def test_lib_defers_zero_size_and_observes_resize(self):
        js = HP.LIB_JS
        self.assertIn("ResizeObserver", js)
        self.assertIn("if (s[0] < 2 || s[1] < 2) return false", js)      # 0 尺寸不建
        self.assertIn("if (stale()) build(); render(p);", js)              # 每次进度消息都核对尺寸
        self.assertIn("data-w", js)

    def test_runtime_fallbacks(self):
        rt = hero.RUNTIME_JS
        self.assertIn("ResizeObserver(chk)", rt)                           # 视口变化补发 resize（给代码模式模块用）
        self.assertIn("if(!got){got=1;st.p=1", rt)                         # 收不到进度 → 终态
        self.assertIn("e.source!==parent", rt)                            # 只认父窗口，不检查 origin（file:// 下是 'null'）

    def test_host_bridge(self):
        self.assertIn("postMessage({vrh: 1, p, theme: th, force: !!force}, '*')", TPL)   # targetOrigin '*'：沙箱 origin 是 'null'
        self.assertIn("if (d.t === 'ready'){ el._p = -1; hsTick(true); }", TPL)          # 模块就绪后补发进度
        self.assertIn("addEventListener('resize', () => { clearTimeout(hsTick._rt)", TPL)


def _page(tmp):
    seg = segment.split(synth_text()); plan = synth_plan(seg)
    chars = render.fix_colors([dict(c) for c in CHARS])
    g = comp(("ink", 0, .5, {"word": "风", "drops": 2}), ("orbit", .45, 1, {"a": "aqing", "b": "laozhou", "dist": [.9, .3], "tension": [.3, .7], "measure": "隔桌"}))
    c = HP.page_payload(HP.validate(g, IDS, "")[0], 1, 12, chars, {"series": {"te": [.1, .5]}})
    g2 = comp(("focus", 0, .5, {"far": "走廊", "near": "低语"}), ("typeset", .45, 1, {"words": ["寂静"], "mode": "assemble"}))
    c2 = HP.page_payload(HP.validate(g2, IDS, "")[0], 2, 12, chars, {})
    n = len(seg["segments"])
    plan["heroes"] = [{"id": "h1-1", "kind": "prims", "at": 10, "title": "推门", "comp": c, "status": "ok", "seconds": 10},
                      {"id": "h1-2", "kind": "prims", "at": min(n - 3, 40), "title": "走廊", "comp": c2, "status": "ok", "seconds": 10}]
    html, _ = render.render(seg, plan)
    out = pathlib.Path(tmp) / "p.html"; out.write_text(html, encoding="utf-8")
    return out


@unittest.skipUnless(HAVE_VERIFY, "需要 Chromium + puppeteer-core")
class RealBrowser(unittest.TestCase):
    def test_page_check_file_url_hostile(self):
        """file:// + 懒加载 + 1000×650@1.5 + iframe 0×0 启动且不补发 resize + 中途改窗口：布局正确、不空白。"""
        with tempfile.TemporaryDirectory() as d:
            out = _page(d)
            r = subprocess.run(["node", str(ROOT / "tools/page_hero_check.js"), str(out), str(pathlib.Path(d) / "shots"), "--max", "4"],
                               capture_output=True, text=True, timeout=300)
            rep = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(rep["ok"], rep); self.assertEqual(rep["checked"], 2)

    def test_module_check_catches_one_shot_layout(self):
        """按 innerWidth 一次性布局、不监听尺寸变化的模块，必须被 hero_check 的恶劣条件测出来（旧 bug 的形态）。"""
        mod = ("<div id='d' style='position:absolute;width:120px;height:120px;background:var(--ink)'></div>"
               "<script>(function(){var d=document.getElementById('d');d.style.left=(innerWidth/2-60)+'px';d.style.top=(innerHeight/2-60)+'px';"
               "VRH.onProgress(function(p){d.style.transform='rotate('+(p*90)+'deg)';});})();</script>")
        r = hero.browser_check({"bad": hero.wrap(mod, CHARS)}, timeout=200)
        errs = " ".join(r["bad"]["errors"])
        self.assertIn("0×0 启动", errs, r["bad"])

    def test_no_progress_shows_final_frame(self):
        """宿主一直不发进度消息时，约 1.5 秒后模块自己显示终态（hero_check 的 no_progress 指标）。"""
        chars = render.fix_colors([dict(c) for c in CHARS])
        from tests.test_hero_prims import GOOD as g
        doc = HP.build_doc(HP.page_payload(HP.validate(g, IDS, "")[0], 1, 12, chars, {}), chars)
        r = hero.browser_check({"g": doc}, timeout=200)["g"]
        self.assertTrue(r["ok"], r)
        self.assertLess(r["metrics"]["no_progress"]["changed_vs_final"], 0.06)
        self.assertLess(r["metrics"]["hostile"]["changed_vs_normal"], 0.06)


if __name__ == "__main__":
    unittest.main()
