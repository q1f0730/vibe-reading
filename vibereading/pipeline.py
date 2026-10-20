"""切分 → 规划 → 渲染 → (可选) 校验/截图。CLI 与上传服务共用。"""
import json, pathlib, subprocess, time, os
from . import segment, planner, render

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def build(text, out_html, title=None, author=None, work=None, theme="paper", plan_in=None,
          reuse_plan=False, verify=False, shots=False, model=None, effort=None, progress=lambda stage, msg: None):
    """plan_in: 外部分镜（dict 或 JSON 文件路径）→ 跳过 LLM；reuse_plan: 复用 <输出>.plan.json。"""
    out_html = pathlib.Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    stem = out_html.name[:-5] if out_html.name.endswith(".html") else out_html.name   # a.b.html → a.b
    plan_path = out_html.parent / f"{stem}.plan.json"
    seg_path = out_html.parent / f"{stem}.segments.json"
    rep_path = out_html.parent / f"{stem}.report.json"
    t0 = time.time(); report = {"input_chars": len(text)}

    progress("segment", "切分原文")
    seg = segment.split(text)
    if not segment.verify_lossless(text, seg):
        raise RuntimeError("切分自检失败：段落拼接后与原文不一致")
    seg_path.write_text(json.dumps(seg, ensure_ascii=False, indent=1), encoding="utf-8")
    report.update(segments=seg["count"], split_mode=seg["mode"])

    t1 = time.time()
    if plan_in is not None or (reuse_plan and plan_path.exists()):
        src = plan_in if plan_in is not None else plan_path
        if not isinstance(src, dict):
            src = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))
        progress("plan", "使用外部分镜 JSON（跳过 LLM）")
        plan, warns = planner.validate_plan(src, seg)
        report["llm"] = {"provider": "external", "model": "plan.json", "calls": 0, "warnings": warns}
    else:
        if not planner.llm_available(model):
            prov, _ = planner.resolve_model(model)
            raise RuntimeError(f"没有可用的 LLM 凭证：请设置环境变量 {planner.PROVIDERS[prov]['key_env']}，"
                               f"或用 --plan 传入现成的分镜 JSON。")
        plan, st = planner.plan(seg, progress=progress, model_spec=model, effort=effort)
        report["llm"] = st.as_dict()
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    report["plan_seconds"] = round(time.time() - t1, 1)

    progress("render", "组装阅读页")
    html, warns = render.render(seg, plan, title=title, author=author, work=work, theme=theme)
    out_html.write_text(html, encoding="utf-8")
    report.update(render_warnings=warns, html_bytes=len(html.encode()), scenes=len(plan["scenes"]),
                  visuals_planned=len(plan["visuals"]), quotes_planned=len(plan["quotes"]),
                  characters=[c["name"] for c in plan["characters"]])
    if verify:
        progress("verify", "逐段比对原文 / 外链 / JS 报错 / 溢出")
        r = subprocess.run(["node", str(TOOLS / "verify.js"), str(out_html.resolve()), str(seg_path.resolve())],
                           capture_output=True, text=True, timeout=180)
        try:
            report["verify"] = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            report["verify"] = {"ok": False, "raw": (r.stdout + r.stderr)[-2000:]}
    if shots:
        progress("shots", "截图")
        sd = out_html.parent / "screenshots" / out_html.stem
        r = subprocess.run(["node", str(TOOLS / "shots.js"), str(out_html.resolve()), str(sd.resolve())],
                           capture_output=True, text=True, timeout=300)
        report["screenshots"] = sorted(str(p) for p in sd.glob("*.png"))
        if r.returncode:
            report["shots_error"] = r.stderr[-1500:]
    report["total_seconds"] = round(time.time() - t0, 1)
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    progress("done", str(out_html))
    return report
