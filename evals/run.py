#!/usr/bin/env python3
"""评测套件：对任意模型配置跑 探针 → 完整管线（分镜 + 关键场景动画 + 渲染 + 浏览器校验）→ 分数卡。

  python3 evals/run.py evals/configs/mercury.json [evals/configs/qwen.json …] [--no-hero] [--no-verify] [--text evals/texts/zhufu.txt]
  python3 evals/run.py --scorecard           # 只把 evals/results/*.json 汇总成 evals/results/SCORECARD.md

配置文件里没有 key：只写 api_key_env（环境变量名）。在凭证由网关注入的环境里，环境变量里是占位值也可以。
"simulate" 只用于评测：{"no_json_mode":1,"no_schema":1,"max_output":1200} 把强模型“降级”成弱模型，验证下限。
"force_tier" 只用于对照实验：跳过探针分档，强制用某一档的策略（看“不自适应”会怎样）。
固定测试文本：evals/texts/zhufu.txt（鲁迅《祝福》，1924，公有领域，见 evals/texts/SOURCE.md）。
"""
import argparse, json, os, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vibereading import llm, probe, tiers, pipeline  # noqa: E402

RESULTS = ROOT / "evals" / "results"
TEXT = ROOT / "evals" / "texts" / "zhufu.txt"

COLS = [("tier", "档位"), ("overall", "总分"), ("floor_ok", "下限"), ("schema", "schema 合格率"), ("schema_first_pass", "首次合格"),
        ("verbatim", "逐字一致"), ("degraded", "降级段落"), ("scenes", "场景分布"), ("scene_n", "场景数"), ("missing", "漏原文段"),
        ("visual_consistency", "组件一致(护栏前)"), ("prim_diversity", "原语多样性"), ("richness", "组件/hero 数"), ("hero", "hero 模型通过/降级"),
        ("seconds", "耗时 s"), ("calls", "调用数"), ("tokens_out", "输出 token")]


def load_config(path):
    d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    env = d.pop("api_key_env", None)
    meta = {k: d.pop(k) for k in ("name", "force_tier") if k in d}
    key = os.environ.get(env) if env else None
    if env and not key:
        raise SystemExit(f"[{meta.get('name')}] 环境变量 {env} 没有设置")
    return meta, llm.ModelConfig.from_dict(d, api_key=key)


def run_one(path, hero=True, verify=True, text_path=TEXT):
    meta, cfg = load_config(path)
    name = meta.get("name") or pathlib.Path(path).stem
    out_dir = RESULTS / name; out_dir.mkdir(parents=True, exist_ok=True)
    client = llm.Client(cfg)
    t0 = time.time()
    print(f"[{name}] 探针…", flush=True)
    prof = probe.run(client)
    probe_s = round(time.time() - t0, 1)
    tier = meta.get("force_tier") or prof.get("tier")
    print(f"[{name}] 档位 {prof.get('tier')}" + (f"（强制用 {tier} 档策略）" if meta.get("force_tier") else "") + f" · {'; '.join(prof.get('tier_reasons') or [])}", flush=True)
    if not prof.get("ok"):
        raise SystemExit(f"[{name}] 探针连不上：{prof.get('errors')}")
    if prof.get("max_output_ok") and not cfg.simulate.get("max_output"):
        client.max_cap = max(client.max_cap or 0, int(prof["max_output_ok"])) if client.max_cap else 0
    engine = tiers.Engine(client, prof, tier, fallback=None, source="eval")
    text = pathlib.Path(text_path).read_text(encoding="utf-8")
    t1 = time.time()
    rep = pipeline.build(text, out_dir / "page.html", title="祝福", author="鲁迅", verify=verify, hero=hero, hero_max=2,
                         engine=engine, progress=lambda s, m: print(f"[{name}] {s} {m}"[:220], flush=True) if s in ("outline", "detail", "hero", "render", "verify", "done") else None)
    q = rep["quality"]
    card = {"name": name, "label": cfg.label, "model": cfg.model, "kind": cfg.kind, "simulate": cfg.simulate, "force_tier": meta.get("force_tier"),
            "probe": {k: v for k, v in prof.items() if k != "probes"}, "probe_detail": prof.get("probes"), "probe_seconds": probe_s,
            "strategy": engine.strategy.as_dict(), "pipeline_seconds": round(time.time() - t1, 1), "quality": q,
            "verify": rep.get("verify"), "hero": {k: (rep.get("hero") or {}).get(k) for k in ("selected", "passed_first", "passed_fixed", "rule", "fallback", "filtered", "llm_calls", "tokens_out", "mode", "warnings")},
            "warnings": (rep.get("llm") or {}).get("warnings", [])[:40], "text": str(pathlib.Path(text_path).relative_to(ROOT)), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    card["row"] = row(card)
    (RESULTS / f"{name}.json").write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{name}] 完成：总分 {q['overall']} · 下限{'守住' if q['floor_ok'] else '未守住'} · {card['row']}", flush=True)
    return card


def row(card):
    q = card["quality"]; s = q["scores"]; h = q.get("hero") or {}; c = q.get("cost") or {}
    return {"tier": q.get("tier"), "overall": q.get("overall"), "floor_ok": q.get("floor_ok"), "schema": s.get("schema"),
            "schema_first_pass": s.get("schema_first_pass"), "verbatim": s.get("verbatim"),
            "degraded": f"{round(100 * q.get('degraded_paragraph_ratio', 0), 1)}%", "scenes": s.get("scenes"),
            "scene_n": (q.get("scene_distribution") or {}).get("scenes"), "missing": (q.get("verbatim") or {}).get("missing_count"),
            "visual_consistency": s.get("visual_consistency"), "prim_diversity": (q.get("diversity") or {}).get("prim_diversity"),
            "hero": f"{h.get('model_pass_rate')}/{h.get('degraded_rate')}" if h.get("count") else "—",
            "richness": f"{(q.get('diversity') or {}).get('visuals')}/{h.get('count', 0)}",
            "seconds": q.get("seconds"), "calls": c.get("calls"), "tokens_out": c.get("tokens_out")}


def rescore():
    """不重新调用模型：用 evals/results/<name>/ 里保存的分镜 / 页面 / 报告，按当前 quality.py 重新计分（改了评分规则时用）。"""
    from vibereading import quality
    for p in sorted(RESULTS.glob("*.json")):
        card = json.loads(p.read_text(encoding="utf-8")); d = RESULTS / card["name"]
        if not (d / "page.plan.json").exists():
            continue
        seg = json.loads((d / "page.segments.json").read_text(encoding="utf-8"))
        plan = json.loads((d / "page.plan.json").read_text(encoding="utf-8"))
        rep = json.loads((d / "page.report.json").read_text(encoding="utf-8"))
        html = (d / "page.html").read_text(encoding="utf-8")
        old = card["quality"]
        eng = {"model": old.get("model"), "label": old.get("model_label"), "tier": old.get("tier"), "source": old.get("source"), "adaptations": old.get("adaptations")}
        card["quality"] = quality.build_report(seg, plan, rep.get("llm"), rep.get("hero"), html, rep.get("verify"), old.get("guard"), old.get("seconds"), eng)
        card["row"] = row(card)
        p.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")


def scorecard():
    cards = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RESULTS.glob("*.json"))]
    if not cards:
        return ""
    lines = ["# 评测分数卡", "", f"测试文本：`{cards[0].get('text')}`（鲁迅《祝福》，公有领域）。每行 = 探针分档 → 完整管线（分镜 + 关键场景动画 + 渲染 + 浏览器校验）。", "",
             "| 配置 | " + " | ".join(lab for _, lab in COLS) + " |", "|---|" + "---|" * len(COLS)]
    for c in cards:
        r = c.get("row") or row(c)
        cells = ["✓" if r.get(k) is True else ("✕" if r.get(k) is False else ("—" if r.get(k) is None else str(r.get(k)))) for k, _ in COLS]
        lines.append(f"| {c['name']}<br><small>{c.get('label') or c['model']}</small> | " + " | ".join(cells) + " |")
    lines += ["", "## 探针档案", "", "| 配置 | JSON 模式 | 结构化 | 提示词 JSON | 工具 | 可靠输出 | 上下文 | 中文长文本 | 过滤误伤 | 推理 | 延迟 s | 分档理由 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cards:
        p = c["probe"]; yn = lambda x: "✓" if x else "✕"
        lines.append(f"| {c['name']} | {yn(p.get('json_mode'))} | {yn(p.get('json_schema'))} | {yn(p.get('json_prompt'))} | {yn(p.get('tools'))} | {p.get('max_output_ok')} | "
                     f"≥{p.get('context_ok')} | {p.get('zh_long')} | {p.get('filter_blocked')} | {yn(p.get('reasoning'))} | {p.get('latency')} | {'；'.join(p.get('tier_reasons') or [])} |")
    lines += ["", "## 下限检查（每项都必须 ✓）", "", "| 配置 | " + " | ".join(next(iter([c["quality"]["floor"] for c in cards])).keys()) + " |",
              "|---|" + "---|" * len(cards[0]["quality"]["floor"])]
    for c in cards:
        lines.append(f"| {c['name']} | " + " | ".join("✓" if v else ("✕" if v is False else "—") for v in c["quality"]["floor"].values()) + " |")
    lines += ["", "## 怎么读", "",
              "- **下限**是硬门槛：逐字 100%、无漏段、分镜完整、页面组件全部与原文一致、hero 全部通过实测或已回退、浏览器校验通过。任何配置都必须全 ✓。",
              "- **总分**衡量“模型给出的分镜有多少能直接用”（schema、首次合格、覆盖、场景分布、组件一致、多样性、hero 的加权），不衡量丰富度："
              "C 档按设计更保守（每场景 ≤1 个组件、每章 1 个 hero），出错机会少，总分可能不低于 A 档；丰富度看“组件/hero 数”。",
              "- 对照行 `*-noadapt` 是同一个弱模型强制用 A 档策略：下限同样守住（护栏兜底），但调用数、输出 token、首次合格率明显更差——这是分档省下来的成本与重试。"]
    md = "\n".join(lines) + "\n"
    (RESULTS / "SCORECARD.md").write_text(md, encoding="utf-8")
    return md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("configs", nargs="*")
    ap.add_argument("--no-hero", action="store_true"); ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--text", default=str(TEXT)); ap.add_argument("--scorecard", action="store_true"); ap.add_argument("--rescore", action="store_true")
    a = ap.parse_args()
    for c in a.configs:
        run_one(c, hero=not a.no_hero, verify=not a.no_verify, text_path=a.text)
    if a.rescore:
        rescore()
    print(scorecard())


if __name__ == "__main__":
    main()
