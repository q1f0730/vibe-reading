"""vibereading build input.txt -o out.html [选项]"""
import argparse, json, pathlib, sys, time
from .pipeline import build


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vibereading", description="任意小说文本 → 代码图文阅读页（原文不经 LLM 改写）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="生成阅读页")
    b.add_argument("input"); b.add_argument("-o", "--output", required=True)
    b.add_argument("--title"); b.add_argument("--author"); b.add_argument("--work", help="书名")
    b.add_argument("--theme", choices=["paper", "night"], default="paper", help="默认主题（读者可切换）")
    b.add_argument("--plan", help="直接使用这份分镜 JSON，跳过 LLM（人工/外部 agent 写的分镜，格式见 docs/plan-format.md）")
    b.add_argument("--reuse-plan", action="store_true", help="复用上次生成的 <输出>.plan.json，只重新渲染")
    b.add_argument("--verify", action="store_true", help="用 Chromium 校验原文一致性/外链/报错/溢出")
    b.add_argument("--shots", action="store_true", help="生成桌面/手机截图")
    m = b.add_mutually_exclusive_group()
    m.add_argument("--model", help="分镜模型：mercury（默认，快速）/ qwen（质量）/ 具体模型名如 mercury-3、qwen3.8-max、qwen3.7-plus")
    m.add_argument("--fast", action="store_const", const="mercury", dest="model", help="快速模式 = mercury-3（默认）")
    m.add_argument("--quality", action="store_const", const="qwen", dest="model", help="质量模式 = qwen3.8-max")
    b.add_argument("--effort", choices=["instant", "low", "medium", "high"], help="mercury 的 reasoning_effort（默认 instant）")
    h = b.add_mutually_exclusive_group()
    h.add_argument("--hero", dest="hero", action="store_const", const=True, default=None,
                   help="生成关键场景专属动画（每章 1–3 个，Mercury 写沙箱动画模块，自动校验，不合格回退预制组件）")
    h.add_argument("--no-hero", dest="hero", action="store_const", const=False, help="去掉分镜里已有的关键场景动画")
    b.add_argument("--hero-max", type=int, default=2, choices=[1, 2, 3], help="每章关键场景动画上限（默认 2）")
    b.add_argument("--hero-mode", choices=["prims", "code"], default=None,
                   help="prims（默认）：模型只输出编排 JSON，视觉来自内置高级原语库；code：模型自由写动画代码（旧模式，默认关闭）")
    a = ap.parse_args(argv)
    text = pathlib.Path(a.input).read_text(encoding="utf-8")
    t0 = time.time()
    def prog(stage, msg):
        print(f"[{time.time() - t0:6.1f}s] {stage:8s} {msg}", file=sys.stderr, flush=True)
    try:
      rep = build(text, a.output, title=a.title, author=a.author, work=a.work, theme=a.theme, plan_in=a.plan,
                reuse_plan=a.reuse_plan, verify=a.verify, shots=a.shots, model=a.model, effort=a.effort, progress=prog,
                hero=a.hero, hero_max=a.hero_max, hero_mode=a.hero_mode)
    except RuntimeError as e:
        print(f"错误：{e}", file=sys.stderr); return 1
    llm = rep.get("llm", {})
    print(json.dumps({"output": a.output, "segments": rep["segments"], "scenes": rep["scenes"],
                      "model": f"{llm.get('provider')}:{llm.get('model')}", "llm_calls": llm.get("calls"), "retries": llm.get("retries"),
                      "json_first_pass": f"{llm.get('first_pass')}/{llm.get('tasks')}", "plan_seconds": rep["plan_seconds"],
                      "total_seconds": rep["total_seconds"], "verify": rep.get("verify"),
                      "hero": {k: rep["hero"].get(k) for k in ("mode", "selected", "passed_first", "passed_fixed", "rule", "fallback", "filtered", "llm_calls", "seconds", "skipped")} if rep.get("hero") else None}, ensure_ascii=False, indent=1))
    v = rep.get("verify")
    return 0 if (not v or v.get("ok")) else 2


if __name__ == "__main__":
    sys.exit(main())
