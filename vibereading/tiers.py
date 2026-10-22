"""能力分档（A/B/C）→ 生成策略；Engine = 一个模型调用端 + 它的能力档案 + 策略（+ 内容过滤时的兜底调用端）。

原则：所有档位走同一套校验与兜底（quality.py），档位只改变“给模型多大的活、约束多严、失败后多快交给规则”。
弱模型 = 更小批次 + 更严约束（schema/enum、逐段号清单、few-shot）+ 更多规则兜底；强模型的上限不被压低，但下限一样。
"""
from dataclasses import dataclass, asdict

from . import llm


@dataclass(frozen=True)
class Strategy:
    tier: str
    batch_paras: int          # detail 每批最多段数
    batch_chars: int          # detail 每批最多字数
    outline_budget: int       # outline 里编号原文的字数预算（超了就截断长段）
    book_segs: int            # 超过这么多段就按“章/部分”切块规划（上下文切块）
    pseudo_chapter: int       # 无章名长文本每块段数
    prompt: str               # full | guided | strict
    few_shot: int             # 示例条数
    structured: str           # schema | json | prompt —— 最强可用约束（再由探针档案降级）
    enum_in_prompt: bool      # 在提示词里把 enum / 段号清单写死
    repair_rounds: int        # 带错误回灌的修复轮数
    detail_max_tokens: int    # detail 单批输出上限
    visuals_per_scene: int    # 每场景组件上限（弱模型少放，减少牵强配图）
    hero_per_chapter: int     # 关键场景动画每章上限
    hero_max_shots: int       # 每个 hero 最多镜头数（2 = 不做多镜头编排）
    code_mode: bool           # 是否允许 hero 代码模式（仍需用户显式开启）
    concurrency: int          # 本档建议并发上限

    def as_dict(self):
        return asdict(self)


BASE = {
    "A": dict(batch_paras=28, batch_chars=5000, outline_budget=16000, book_segs=200, pseudo_chapter=120, prompt="full", few_shot=0,
              structured="schema", enum_in_prompt=False, repair_rounds=1, detail_max_tokens=8000, visuals_per_scene=3,
              hero_per_chapter=3, hero_max_shots=4, code_mode=True, concurrency=4),
    "B": dict(batch_paras=16, batch_chars=3000, outline_budget=9000, book_segs=140, pseudo_chapter=80, prompt="guided", few_shot=1,
              structured="schema", enum_in_prompt=True, repair_rounds=2, detail_max_tokens=4000, visuals_per_scene=2,
              hero_per_chapter=2, hero_max_shots=3, code_mode=False, concurrency=3),
    "C": dict(batch_paras=8, batch_chars=1500, outline_budget=5000, book_segs=80, pseudo_chapter=45, prompt="strict", few_shot=2,
              structured="schema", enum_in_prompt=True, repair_rounds=2, detail_max_tokens=2200, visuals_per_scene=1,
              hero_per_chapter=1, hero_max_shots=2, code_mode=False, concurrency=2),
}


def strategy_for(tier, profile=None):
    tier = tier if tier in BASE else "B"
    d = dict(BASE[tier])
    p = profile or {}
    # 结构化输出：按探针结果取最强可用的一档
    if p:
        if p.get("json_schema"):
            d["structured"] = "schema"
        elif p.get("json_mode"):
            d["structured"] = "json"
        else:
            d["structured"] = "prompt"
    # 输出上限 / 上下文小于本档默认 → 收紧批次
    mo = int(p.get("max_output_ok") or 0)
    if mo:
        d["detail_max_tokens"] = max(800, min(d["detail_max_tokens"], int(mo * 0.85)))
        per_para = 160
        d["batch_paras"] = max(4, min(d["batch_paras"], (d["detail_max_tokens"] - 800) // per_para))
    ctx = int(p.get("context_ok") or 0)
    if ctx:
        # 中文约 1–1.5 token/字：输入预算取上下文的 35%，再留给系统提示与输出
        cap = max(2500, int(ctx * 0.35))
        d["outline_budget"] = min(d["outline_budget"], cap)
        d["batch_chars"] = min(d["batch_chars"], max(1200, cap // 2))
    d["code_mode"] = d["code_mode"] and tier == "A" and bool(p.get("code_mode_ok", not p))
    return Strategy(tier=tier, **d)


class Engine:
    """planner / hero 只认 Engine：client 发请求，strategy 定打法，fallback 用于内容过滤时换模型（可为空）。"""

    def __init__(self, client, profile=None, tier=None, fallback=None, source="platform"):
        self.client = client
        self.profile = dict(profile or {})
        self.tier = tier or self.profile.get("tier") or "B"
        self.strategy = strategy_for(self.tier, self.profile)
        self.fallback = fallback
        self.source = source          # platform | user | eval

    @property
    def label(self):
        return self.client.label

    def describe(self):
        return {"model": self.client.cfg.model, "label": self.label, "kind": self.client.cfg.kind, "tier": self.tier,
                "source": self.source, "strategy": self.strategy.as_dict(),
                "fallback": self.fallback.label if self.fallback else None,
                "reasoning": self.client.reason_level, "adaptations": list(self.client.events)}


# 平台默认模型的预置档案（评测里实际探针结果见 evals/results/；这里取保守值）
PLATFORM_PROFILES = {
    "mercury": {"tier": "A", "json_mode": True, "json_schema": True, "tools": True, "max_output_ok": 16000, "context_ok": 128000,
                "reasoning": True, "code_mode_ok": True, "preset": True},
    "qwen": {"tier": "A", "json_mode": True, "json_schema": True, "tools": True, "max_output_ok": 16000, "context_ok": 128000,
             "reasoning": False, "code_mode_ok": True, "preset": True},
}


def platform_engine(name="mercury", model=None, reasoning=None):
    c = llm.platform_client(name, model, reasoning)
    fb = None
    if name != "qwen" and llm.platform_available("qwen"):
        fb = llm.platform_client("qwen")     # 平台 Mercury 被内容过滤 → 本批改用平台千问（沿用旧行为）
    return Engine(c, PLATFORM_PROFILES.get(name), fallback=fb, source="platform")
