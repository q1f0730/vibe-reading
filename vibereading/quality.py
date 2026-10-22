"""质量护栏（与模型无关，所有模型的输出都过这一套）+ 质量报告。

护栏（会改结果）：
  - visual_issues：组件与原文一致性的启发式检查（door 附近必须有“门”、car_night 场景里必须有“车”、object 的物件词、
    timeline 标签必须出自本场景原文…）。detail 校验时作为错误回灌让模型修；修不好的在 guard_plan 里直接去掉。
  - guard_plan：去掉不一致组件、按档位限制每场景组件数、去掉夹带原文长句的图注。
  - hero_word_issues：hero 画面意象词与场景原文毫无关联时报错（回灌修复 → 规则编排）。
度量（只报告）：schema 合格率、逐字一致（静态解析 HTML 的 [data-src]）、漏原文、规则降级段比例、场景分布、
组件/原语多样性、意象一致性、hero 状态、耗时、调用数、输出 token。
"""
import re, statistics
from html.parser import HTMLParser

# 组件 → 需要在原文（附近 / 本场景）里出现的关键词
NEAR = 3      # 段落窗口：at±3
VISUAL_KEYS = {
    "door": ("门",),
    "doorbell": ("铃", "钟", "电话", "响"),
    "knock": ("敲", "叩", "咚", "砰", "心跳", "拍门"),
    "car_night": ("车", "驾驶", "司机", "公路"),
    "sound": ("笑", "声", "喊", "叫", "哭", "叹", "吵", "嚷", "喧", "唱"),
    "ledger": ("账", "板", "名单", "写", "记", "欠"),
    "blur_chart": ("书", "图", "表", "字", "纸", "页", "看", "成绩", "分数"),
    "stamp_cards": ("分", "钱", "价", "元", "文", "成绩", "账", "号", "数"),
}
SCENE_KEYS = {
    "window": ("窗", "雨", "雪", "风", "雾", "天", "月", "阳光", "外面", "夜", "云", "星"),
    "stairs_memory": ("回忆", "想起", "记得", "从前", "那时", "当年", "楼梯", "小时候", "以前", "过去", "旧"),
}
THING_KEYS = {"jar_spoon": ("勺", "罐", "酱"), "bowl": ("碗",), "wine_cup": ("酒", "碗", "杯"), "bean_dish": ("豆", "碟"),
              "coins": ("钱", "文", "铜", "币"), "book": ("书",), "letter": ("信",), "phone": ("电话", "手机"),
              "cigarette": ("烟",), "key": ("钥匙", "锁"), "candle": ("蜡", "烛"), "glass": ("杯", "玻璃")}
ROOM_KEYS = {"kitchen": ("厨", "灶", "锅", "做饭", "碗"), "tavern": ("酒", "店", "柜", "掌柜"), "classroom": ("教室", "课", "学", "黑板", "老师"),
             "office": ("办公", "公司", "单位", "文件"), "bedroom": ("床", "卧", "睡"), "living": ("客厅", "沙发", "屋", "家", "房"),
             "hall": ("堂", "厅", "院", "祠", "宅", "客")}
# 意象一致性里不算“内容字”的虚字
FUNC_CHARS = set("的一了是在不有这个上们来到时大地为子中你我他她它说着就那和要也得里后以会可下而过去能对小多然于之都好看起发当没成"
                 "只如事把还用道想作开从无己面最但现前些所同又行意动方很给间什两次使身者被已其进此话与正见明问点几定本做相果走将向"
                 "全重物每并别真打太新比才便再像等却加让应直至往何度觉带边叫任原吧呢吗啊呀么啦嗯哦")


def _scene_of(plan):
    m = {}
    for k, s in enumerate(plan.get("scenes") or []):
        for i in range(s["start"], s["end"] + 1):
            m[i] = k
    return m


def _texts(segs, a, b):
    return "".join(segs[i]["text"] for i in range(max(0, a), min(len(segs) - 1, b) + 1))


def visual_issues(visuals, segs, scenes):
    """→ [(visual, reason)]。只检查能从原文直接核对的具象组件；silence/pullquote 等抽象组件不查。"""
    sc_of = {}
    for k, s in enumerate(scenes or []):
        for i in range(s["start"], s["end"] + 1):
            sc_of[i] = s
    out = []
    for v in visuals or []:
        if not isinstance(v, dict) or not isinstance(v.get("at"), int) or not (0 <= v["at"] < len(segs)):
            continue
        t, at, p = v.get("type"), v["at"], v.get("params") or {}
        s = sc_of.get(at, {"start": at, "end": at, "tone": "day"})
        near = _texts(segs, at - NEAR, at + NEAR); whole = _texts(segs, s["start"], s["end"])
        why = None
        if t in VISUAL_KEYS and not any(k in near for k in VISUAL_KEYS[t]):
            why = f"{t}@{at} 附近原文没有“{'/'.join(VISUAL_KEYS[t][:3])}”"
        elif t in SCENE_KEYS and not any(k in whole for k in SCENE_KEYS[t]) and not (t == "stairs_memory" and s.get("tone") == "memory"):
            why = f"{t}@{at} 所在场景原文里没有相关描写（{'/'.join(SCENE_KEYS[t][:3])}…）"
        elif t == "object" and p.get("thing") in THING_KEYS and not any(k in near for k in THING_KEYS[p["thing"]]):
            why = f"object({p['thing']})@{at} 附近原文没有这个物件"
        elif t == "interior" and p.get("room") in ROOM_KEYS and not any(k in whole for k in ROOM_KEYS[p["room"]]):
            why = f"interior({p['room']})@{at} 所在场景原文里看不出是这个房间"
        elif t == "timeline":
            bad = [x for x in (p.get("labels") or []) if isinstance(x, str) and x and x not in whole and x not in near]
            if bad:
                why = f"timeline@{at} 标签 {bad[:3]} 不是本场景原文里的时间词"
        if why:
            out.append((v, why))
    return out


def caption_leaks(text, source, n=8):
    """图注/画面文字夹带 ≥n 字的原文片段。"""
    if not isinstance(text, str) or len(text) < n:
        return False
    return any(text[i:i + n] in source for i in range(len(text) - n + 1))


def guard_plan(plan, segs, stats=None, strat=None):
    """统一护栏：作用于任何模型产出的分镜。返回统计 dict，并原地修改 plan。"""
    source = "".join(s["text"] for s in segs)
    issues = visual_issues(plan.get("visuals"), segs, plan.get("scenes"))
    bad_ids = {id(v) for v, _ in issues}
    kept, dropped, capped, leaks = [], len(bad_ids), 0, 0
    cap = getattr(strat, "visuals_per_scene", 3) if strat else 3
    per_scene = {}
    sc_of = _scene_of(plan)
    for v in plan.get("visuals") or []:
        if id(v) in bad_ids:
            continue
        k = sc_of.get(v.get("at"), -1)
        if per_scene.get(k, 0) >= cap:
            capped += 1; continue
        cp = (v.get("params") or {}).get("caption")
        if cp and caption_leaks(cp, source):
            v["params"].pop("caption", None); leaks += 1
        per_scene[k] = per_scene.get(k, 0) + 1
        kept.append(v)
    total = len(plan.get("visuals") or [])
    plan["visuals"] = kept
    if stats is not None:
        stats.dropped_visuals = getattr(stats, "dropped_visuals", 0) + dropped + capped
        for _, why in issues[:12]:
            stats.warnings.append("护栏：去掉与原文不符的组件 " + why)
        if capped:
            stats.warnings.append(f"护栏：每场景组件上限 {cap}，去掉 {capped} 个")
    return {"visuals_total": total, "inconsistent": dropped, "capped": capped, "caption_leaks": leaks, "kept": len(kept)}


# ------------------------------------------------------------------ hero imagery
HERO_WORD_KEYS = ("words", "word", "note", "far", "near", "measure", "title")


def hero_words(comp):
    out = []
    for s in (comp or {}).get("shots") or []:
        p = s.get("params") or {}
        for k in HERO_WORD_KEYS:
            v = p.get(k)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
            elif isinstance(v, list):
                out += [x.strip() for x in v if isinstance(x, str) and x.strip()]
        for n in p.get("notes") or []:
            if isinstance(n, dict) and isinstance(n.get("word"), str):
                out.append(n["word"])
    return out


def word_related(word, text):
    chars = [c for c in word if "\u4e00" <= c <= "\u9fff" and c not in FUNC_CHARS]
    if not chars:
        return True          # 纯虚字 / 非中文：不判
    return any(c in text for c in chars)


# 通用意象：情绪 / 光线 / 时间 / 动作 / 距离类的字。画面词落在这些意象上就不算“凭空编”，
# 即使原文没出现同一个字（相遇、晨光、笑意、靠近、沉默、对峙……）。
IMAGERY_CHARS = set("光影明暗晨昏暮夜晴阴雨雪雾霜烛暖冷温凉寒静默"
                    "笑泪哭怒惊怕慌羞烫跳息痛伤爱恨忧愁梦醒"
                    "拥抱吻握牵触碰靠近远离距遇别逢回望等聚散追逃"
                    "沉寂峙逼爆紧余隔缝隙裂碎")


def word_imagery(word):
    return any(c in IMAGERY_CHARS for c in word)


def hero_word_issues(comp, scene_text):
    """只拒明显无关：全部画面词既和场景原文没有共同实字，也不是情绪/光线/动作/时间类通用意象，才报错。"""
    ws = hero_words(comp)
    if not ws:
        return []
    unrelated = [w for w in ws if not word_related(w, scene_text) and not word_imagery(w)]
    if len(unrelated) == len(ws):
        return [f"画面意象词 {unrelated[:4]} 与本场景明显无关：请从场景里的物件/动作/情绪/光线里取词（可以变形，但别凭空编）"]
    return []


# ------------------------------------------------------------------ static verbatim
class _SrcParser(HTMLParser):
    VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "col", "area", "base", "embed", "param", "track"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []        # [(tag, src_index or None)]
        self.got = []          # [[idx, text]]
        self.cur = []          # indexes into got that are open

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        src = dict(attrs).get("data-src")
        idx = None
        if src is not None and src.isdigit():
            self.got.append([int(src), ""]); idx = len(self.got) - 1; self.cur.append(idx)
        self.stack.append((tag, idx))

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self.stack:
            t, idx = self.stack.pop()
            if idx is not None and self.cur and self.cur[-1] == idx:
                self.cur.pop()
            if t == tag:
                break

    def handle_data(self, data):
        for idx in self.cur:
            self.got[idx][1] += data


def verbatim_static(html, seg):
    """不开浏览器：解析 HTML 里所有 [data-src]，逐段与原文比较（等价于 verify.js 的文本部分）。"""
    segs = seg["segments"]
    p = _SrcParser(); p.feed(html); p.close()
    got = {}
    for i, t in p.got:
        got.setdefault(i, t)
    matched = sum(1 for i, s in enumerate(segs) if got.get(i) == s["text"])
    missing = [i for i in range(len(segs)) if i not in got]
    wrong = [i for i in range(len(segs)) if i in got and got[i] != segs[i]["text"]]
    return {"segments": len(segs), "matched": matched, "missing": missing[:50], "missing_count": len(missing),
            "mismatch": wrong[:50], "identical": matched == len(segs) and len(p.got) == len(segs)}


# ------------------------------------------------------------------ metrics
def scene_distribution(plan, n, segs=None):
    """场景粒度：2–20 段为好；单段场景只有在那一段很长（≥200 字，如《孔乙己》式长段）或是回忆时算好；太粗（平均 >15 段）扣分。"""
    sc = plan.get("scenes") or []
    if not sc or not n:
        return {"scenes": 0, "score": 0.0}
    sizes = [s["end"] - s["start"] + 1 for s in sc]
    clen = lambda s: sum(len(segs[i]["text"]) for i in range(s["start"], min(len(segs) - 1, s["end"]) + 1)) if segs else 0
    good = sum(z for s, z in zip(sc, sizes) if 2 <= z <= 20 or (z == 1 and (s.get("tone") == "memory" or clen(s) >= 200)) or n <= 3)
    contiguous = sc[0]["start"] == 0 and sc[-1]["end"] == n - 1 and all(b["start"] == a["end"] + 1 for a, b in zip(sc, sc[1:]))
    score = good / n
    avg = n / len(sc)
    if avg > 15:
        score *= 15 / avg          # 太粗（一整篇一个场景）
    rule_named = sum(1 for s in sc if re.search(r"·\d+$|^第\d+节$", s.get("name", "")))
    return {"scenes": len(sc), "min": min(sizes), "max": max(sizes), "median": statistics.median(sizes), "avg": round(avg, 1),
            "contiguous": contiguous, "rule_named": rule_named, "score": round(100 * score * (1 if contiguous else 0.5), 1)}


def diversity(plan):
    vis = plan.get("visuals") or []
    types = [v.get("type") for v in vis]
    comp_div = (len(set(types)) / len(types)) if types else None
    shots = [s.get("prim") for h in plan.get("heroes") or [] for s in ((h.get("comp") or {}).get("shots") or [])]
    prim_div = (len(set(shots)) / len(shots)) if shots else None
    return {"visuals": len(vis), "visual_types": len(set(types)), "component_diversity": round(comp_div, 3) if comp_div is not None else None,
            "hero_shots": len(shots), "hero_prims": len(set(shots)), "prim_diversity": round(prim_div, 3) if prim_div is not None else None}


def hero_summary(plan, segs):
    hs = plan.get("heroes") or []
    if not hs:
        return {"count": 0}
    st = {}
    for h in hs:
        st[h.get("status", "?")] = st.get(h.get("status", "?"), 0) + 1
    rel = tot = 0
    sc = plan.get("scenes") or []
    for h in hs:
        comp = h.get("comp")
        if not comp or not isinstance(h.get("scene"), int) or h["scene"] >= len(sc):
            continue
        s = sc[h["scene"]]; text = _texts(segs, s["start"], s["end"])
        for w in hero_words(comp):
            tot += 1; rel += word_related(w, text)
    good = st.get("ok", 0) + st.get("fixed", 0)
    return {"count": len(hs), "status": st, "model_pass_rate": round(good / len(hs), 3),
            "degraded_rate": round((len(hs) - good) / len(hs), 3), "imagery_consistency": round(rel / tot, 3) if tot else None}


def build_report(seg, plan, llm_stats=None, hero=None, html=None, verify=None, guard=None, seconds=None, engine=None):
    segs = seg["segments"]; n = len(segs)
    L = llm_stats or {}
    tasks = L.get("tasks") or 0
    schema_rate = (L.get("passed", 0) / tasks) if tasks else None
    first_rate = (L.get("first_pass", 0) / tasks) if tasks else None
    rule_paras = L.get("rule_paras", 0) or 0
    degraded_ratio = rule_paras / n if n else 0
    vb = verbatim_static(html, seg) if html is not None else None
    sd = scene_distribution(plan, n, segs)
    dv = diversity(plan)
    hsum = hero_summary(plan, segs)
    g = guard or {}
    vis_total = g.get("visuals_total")
    vis_cons = (1 - g.get("inconsistent", 0) / vis_total) if vis_total else None
    final_issues = visual_issues(plan.get("visuals"), segs, plan.get("scenes"))
    scores = {
        "schema": round(100 * schema_rate, 1) if schema_rate is not None else None,
        "schema_first_pass": round(100 * first_rate, 1) if first_rate is not None else None,
        "verbatim": round(100 * vb["matched"] / max(1, n), 1) if vb else None,
        "coverage": round(100 * (1 - degraded_ratio), 1),
        "scenes": sd["score"],
        "visual_consistency": round(100 * vis_cons, 1) if vis_cons is not None else None,
        "diversity": round(100 * statistics.mean([x for x in (dv["component_diversity"], dv["prim_diversity"]) if x is not None]), 1)
                     if any(x is not None for x in (dv["component_diversity"], dv["prim_diversity"])) else None,
        "hero": round(100 * (0.5 * hsum["model_pass_rate"] + 0.5 * (hsum.get("imagery_consistency") or 0)), 1) if hsum.get("count") else None,
    }
    W = {"schema": 15, "coverage": 25, "scenes": 15, "visual_consistency": 15, "diversity": 10, "hero": 10, "schema_first_pass": 10}
    num = sum(W[k] * scores[k] for k in W if scores.get(k) is not None)
    den = sum(W[k] for k in W if scores.get(k) is not None)
    overall = round(num / den, 1) if den else None
    floor = {
        "verbatim_100": bool(vb and vb["identical"]) if vb else None,
        "no_missing_text": bool(vb and vb["missing_count"] == 0) if vb else None,
        "plan_complete": len(plan.get("paras") or []) == n and sd.get("contiguous", False),
        "visuals_consistent_after_guard": not final_issues,
        "heroes_render_ok": all(h.get("status") in ("ok", "fixed", "rule", "simple", "fallback") for h in plan.get("heroes") or []),
    }
    if verify is not None:
        floor["browser_verify"] = bool(verify.get("ok"))
    if overall is not None and vb and not vb["identical"]:
        overall = 0.0
    eng = engine or L.get("engine") or {}
    return {
        "version": 1,
        "model": eng.get("model") or L.get("model"), "model_label": eng.get("label"), "tier": eng.get("tier") or L.get("tier"),
        "source": eng.get("source"), "overall": overall, "scores": scores,
        "floor_ok": all(v for v in floor.values() if v is not None), "floor": floor,
        "degraded_paragraph_ratio": round(degraded_ratio, 4), "rule_paragraphs": rule_paras,
        "scene_distribution": sd, "diversity": dv, "hero": hsum, "guard": g,
        "verbatim": vb, "verify": {k: verify.get(k) for k in ("ok", "text_identical", "matched", "segments", "js_errors", "external_requests")} if verify else None,
        "cost": {"calls": L.get("calls", 0) + ((hero or {}).get("llm_calls") or 0), "plan_calls": L.get("calls", 0),
                 "hero_calls": (hero or {}).get("llm_calls"), "tokens_out": (L.get("tokens_out", 0) or 0) + ((hero or {}).get("tokens_out") or 0),
                 "tokens_in": L.get("tokens_in", 0), "reasoning_tokens": L.get("reasoning_tokens", 0), "retries": L.get("retries", 0),
                 "transient": L.get("transient", 0), "content_filtered": L.get("content_filtered", 0), "fallbacks": L.get("fallbacks", 0)},
        "seconds": seconds,
        "adaptations": (eng.get("adaptations") or [])[:10],
    }
