"""关键场景动画 · 原语模式（默认）。

模型不画画：Hark 手写的原语库（hero_lib/lib.js + lib.css，零依赖）负责全部视觉；模型只输出 JSON 编排：
选哪几个原语、分几个镜头、每个镜头的参数和滚动区间。画面上的文字只能是场景意象短词（≤8 字），
并且逐条和原文比对：≥4 字且在原文里出现过的串一律拒收（防止原文句子漏进画面）。

编排不合格 → 把错误回灌让模型修一次 → 仍不合格 → 规则编排（按场景描述里的物件/隐喻关键词挑原语，一定合格）。
"""
import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent
LIB_JS = (ROOT / "hero_lib" / "lib.js").read_text(encoding="utf-8")
LIB_CSS = (ROOT / "hero_lib" / "lib.css").read_text(encoding="utf-8")

DARK = {"develop", "focus", "door", "passing"}
# 具象原语只能用在场景里真有这个东西的时候（关键词在场景描述里）
NEEDS = {"door": ("门", "玄关", "进屋", "出去", "离开", "到达"), "spoon": ("勺", "餐具", "碗", "罐", "酱"), "passing": ("车", "开车", "驾驶", "公路", "夜路")}
# 同一章里：这些原语最多用 1 次，其余（排版/压印）最多 2 次
ONCE = {"develop", "focus", "door", "spoon", "passing", "window", "ink", "chart", "orbit"}
W = lambda n: {"type": "string", "maxLength": n}

PRIMS = {
    "typeset": {"label": "动态排版", "params": {
        "words": {"type": "array", "minItems": 1, "maxItems": 3, "items": W(8)},
        "mode": {"enum": ["assemble", "scatter", "mask", "vertical"]},
        "tone": {"enum": ["ink", "safe", "cool"]}, "accent": {"type": "integer", "minimum": -1, "maximum": 7}},
        "required": ["words"],
        "doc": "大号衬线字的拆分与重组。mode：assemble=字从四散、模糊中聚拢，然后字距慢慢呼吸拉开；scatter=一个词的字被拉开，中间出现尺寸标注线（适合“距离/疏远”）；"
               "mask=1–3 行字依次从遮罩下滑出、逐行错位（像报纸标题）；vertical=竖排大字自上而下显出，配一行横排小字。"
               "words[0] 是主词（1–4 字最好看），其余是注释小字。tone：ink 墨 / safe 暗房红 / cool 冷灰蓝；accent=主词里第几个字用红色（-1 不用）。"},
    "develop": {"label": "暗房显影", "dark": True, "params": {"word": W(6), "note": W(10)}, "required": ["word"],
        "doc": "暗色镜头。红色安全灯下的药水盘，一张相纸从一片模糊的灰慢慢显影到清晰，相纸上是 word；边缘有胶片编号，药水有涟漪。适合“记忆/被看见/真相浮现”。"},
    "ink": {"label": "墨迹晕染", "params": {"word": W(4), "note": W(10), "drops": {"type": "integer", "minimum": 1, "maximum": 3}}, "required": ["word"],
        "doc": "墨滴落在纸上沿纤维晕开、边缘积墨；墨团中央把 word 留白显出。drops=几滴墨（1 最干净，2–3 表示几个人/几件事彼此渗透）。适合“情绪蔓延/脸红/羞耻扩散”。"},
    "press": {"label": "凸版压印", "params": {"word": W(6), "note": W(10), "seal": W(2)}, "required": ["word"],
        "doc": "word 被压进纸里（凹痕、高光），墨不均匀地吃进纤维；四角是印刷角线和套准标。seal=可选 1–2 字的小红印。适合“定论/承诺/被记住的一句话的主题词”。"},
    "window": {"label": "窗格投影", "params": {"word": W(6), "cols": {"type": "integer", "minimum": 2, "maximum": 4},
        "rows": {"type": "integer", "minimum": 2, "maximum": 4}, "from": {"enum": ["noon", "dusk", "night"]}, "to": {"enum": ["noon", "dusk", "night"]}}, "required": [],
        "doc": "墙上一块窗格形状的光随滚动移动、拉长、变色（from→to：noon 暖白 / dusk 橙 / night 冷蓝），尘埃只在光里亮；word 只在光扫过的地方显出。适合“时间流逝/室内/等待”。"},
    "focus": {"label": "景深对焦", "dark": True, "params": {"far": W(6), "near": W(6), "light": {"enum": ["warm", "cool", "safe"]}}, "required": ["far", "near"],
        "doc": "暗色镜头。远处一个词、近处一个词，背后是失焦光斑；焦点从远拉到近（远词糊掉、近词清晰），底部是镜头距离刻度。适合“注意力转移/从人群到一个人”。"},
    "orbit": {"label": "关系引力", "params": {"a": W(24), "b": W(24), "c": W(24),
        "dist": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "number", "minimum": 0, "maximum": 1}},
        "tension": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "number", "minimum": 0, "maximum": 1}}, "measure": W(6)},
        "required": ["a", "b"],
        "doc": "两个人是两个点（a、b 填人物 id，c 可选第三人），之间是一束细张力线，周围的场线被对方挤压；dist=距离关键帧（1 远 0 近），tension=张力关键帧（高=绷直颤动，低=松弛下垂）；"
               "下方是尺寸标注线，measure 是标注上的短词。适合“关系变化/靠近/疏远/沉默对峙”。"},
    "door": {"label": "门缝光", "dark": True, "params": {"word": W(4), "light": {"enum": ["warm", "cool"]}}, "required": [],
        "doc": "暗色镜头。黑暗里一扇门慢慢开出一条光缝，光在地板上铺成梯形，光里有浮尘；word 以透视躺在地上的光里。适合“到达/进入/离开/秘密”。"},
    "spoon": {"label": "物件特写·勺", "params": {"word": W(6), "note": W(10)}, "required": [],
        "doc": "一把金属勺的特写：高光滑过勺面、勺里有窗形反光、投影随光移动；一条细引线指向反光处，标注 word。适合“厨房/漫不经心的小动作/被凝视的细节”。"},
    "passing": {"label": "夜行光影", "dark": True, "params": {"word": W(6), "lamps": {"type": "integer", "minimum": 2, "maximum": 6}, "rain": {"type": "boolean"}}, "required": [],
        "doc": "暗色镜头。夜里车内：路灯一盏盏从头顶扫过，光带斜穿车厢，远处车灯是失焦光点，玻璃上的雨珠在灯过时亮起；word 在灯光里时隐时现。适合“车里/夜路/沉默的同行”。"},
    "chart": {"label": "情绪读数", "params": {"series": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"enum": ["te", "in", "bl"]}},
        "notes": {"type": "array", "maxItems": 3, "items": {"type": "object", "required": ["x", "word"],
                  "properties": {"x": {"type": "number", "minimum": 0, "maximum": 1}, "word": W(6)}}}, "title": W(8)}, "required": [],
        "doc": "编辑部折线图：本场景真实的逐段情绪数值（te 紧张 / in 亲密 / bl 脸红，由系统填入，不用你给数据）随滚动画出，线尾直接标名；series 只写要画哪几条线，如 [\"te\",\"in\"]；"
               "notes 在横轴位置 x(0–1) 处加竖线注释短词。适合“情绪起伏本身就是看点”的场景，每章最多用一次。"},
}

COMP_SCHEMA = {
    "type": "object", "required": ["shots"],
    "properties": {
        "slug": W(10),
        "shots": {"type": "array", "minItems": 2, "maxItems": 4, "items": {
            "type": "object", "required": ["prim", "from", "to"],
            "properties": {"prim": {"enum": list(PRIMS)}, "from": {"type": "number", "minimum": 0, "maximum": 1},
                           "to": {"type": "number", "minimum": 0, "maximum": 1}, "params": {"type": "object"}}}}}}


def catalog():
    out = []
    for k, v in PRIMS.items():
        ps = []
        for pk, ps_ in v["params"].items():
            if "enum" in ps_:
                ps.append(f"{pk}:{'|'.join(ps_['enum'])}")
            elif ps_.get("type") == "string":
                ps.append(f"{pk}:≤{ps_['maxLength']}字")
            elif ps_.get("type") == "array":
                ps.append(f"{pk}:[…]")
            else:
                ps.append(f"{pk}:{ps_.get('type')}")
        out.append(f"- {k}（{v['label']}{'，暗色' if v.get('dark') else ''}）参数 {{{', '.join(ps)}}}：{v['doc']}")
    return "\n".join(out)


COMPOSE_SYS = """你是 NYT / The Pudding 图像部的艺术指导。一个小说关键场景要做成一段随滚动推进的“编辑部视觉短片”。
你不画画：视觉全部来自一套已经做好的高级原语（动态排版、暗房显影、墨迹、压印、窗光、景深、关系引力图、物件特写……）。
你只做编排：选原语、分镜头、给参数和滚动区间。只输出 JSON。

## 原语
{catalog}

## 输出
{{"slug":"≤8字 图号旁的小标题（场景意象，如 厨房·一月）",
 "shots":[{{"prim":"原语名","from":0.0,"to":0.45,"params":{{…}}}}, …]}}

## 规则（违反会被自动拒收）
1. 2–3 个镜头（最多 4）；from/to 是滚动进度 0–1，每个镜头至少 0.25 长，让原语有时间展开；所有镜头的区间合起来必须覆盖 0→1，不留空档；
   相邻镜头重叠 0.04–0.1 做转场，不要让两个镜头长时间叠在一起。
2. 画面上的所有文字只能是**场景意象短词**（名词/动词短语，1–6 字，自己想，贴合本场景），同一段里不要重复同一个词，
   绝不能是原文句子或台词，不能带标点；不要写人物的心理独白。人物名字只出现在 orbit 的 a/b/c（填人物 id）。
3. 暗色镜头（develop / focus / door / passing）每段最多 2 个，并且不能全部是暗色：页面整体是米白纸面，暗色是重音不是底色。
4. 不要每段都用同一套组合；镜头之间要有反差（大字 ↔ 细线图、暗 ↔ 亮、近景物件 ↔ 抽象关系），并且贴合这个场景的物件与情绪曲线：
   有门/到达→door；有车/夜路→passing；场景里真有勺子/碗罐→spoon（只在描述里有这些物件时才能用 door/spoon/passing）；时间推移/窗→window；
   关系距离变化→orbit；羞耻、脸红、情绪扩散→ink；回忆、被看穿→develop；注意力转移→focus；结论或主题词→press 或 typeset。
   同一章里 develop/focus/door/spoon/passing/window/ink/chart/orbit 各最多用一次，typeset/press 各最多两次；结尾镜头也要换着来，别每段都以大字收尾。
5. 最后一个镜头的终态（p=1）要像一张可以单独截图的报纸配图。
6. 参数只用上面列出的键；数组长度、字数遵守上限。"""


def compose_user(desc, chars, used=None, zone=None):
    s = ["场景描述：", desc, "", "可用人物 id：" + "，".join(f"{c['id']}（{c['name']}）" for c in chars)]
    if used:
        s.append("本章前面的关键场景已经用过：" + "、".join(used) + "。按次数上限，不能再用的原语：" +
                 ("、".join(sorted(blocked(used))) or "无") + "；开场和结尾请换别的原语。")
    s.append("\n请输出编排 JSON。")
    return "\n".join(s)


# ------------------------------------------------------------------ validation
_PUNCT = re.compile(r"[，。！？；：、“”‘’\"'「」『』（）()…—,.!?;:]")


def _texts(prim, params):
    """该镜头里会出现在画面上的文字（name, value）。"""
    out = []
    for k in ("word", "note", "seal", "far", "near", "measure", "title"):
        if isinstance(params.get(k), str) and params[k].strip():
            out.append((k, params[k]))
    for w in params.get("words") or []:
        if isinstance(w, str):
            out.append(("words", w))
    for n in params.get("notes") or []:
        if isinstance(n, dict) and isinstance(n.get("word"), str):
            out.append(("notes.word", n["word"]))
    return out


def leaks(text, source, n=4):
    """text 里有没有 ≥n 字的片段原样出现在原文里。返回命中的片段或 None。"""
    t = re.sub(r"\s+", "", text)
    if len(t) < n or not source:
        return None
    for i in range(len(t) - n + 1):
        if t[i:i + n] in source:
            return t[i:i + n]
    return None


def blocked(used):
    from collections import Counter
    c = Counter(used or [])
    return {k for k, v in c.items() if (k in ONCE and v >= 1) or v >= 2}


def validate(comp, char_ids, source="", desc_text=None, used=None):
    """返回 (清洗后的 comp, errors[])。errors 为空才算合格。会做小修：夹紧区间、截断超长字、丢弃未知参数。
    desc_text：场景描述文本（检查具象原语的物件是否真在场景里）；used：本章前面 hero 已用过的原语（检查次数上限）。"""
    import jsonschema
    errs = []
    nope = blocked(used)
    if not isinstance(comp, dict):
        return None, ["输出不是 JSON 对象"]
    shots = comp.get("shots")
    if not isinstance(shots, list) or not shots:
        return None, ["缺少 shots 数组"]
    clean = []
    for k, s in enumerate(shots[:5]):
        if not isinstance(s, dict) or s.get("prim") not in PRIMS:
            errs.append(f"镜头 {k}：prim 必须是 {'/'.join(PRIMS)} 之一，收到 {s.get('prim') if isinstance(s, dict) else s!r}")
            continue
        spec = PRIMS[s["prim"]]
        try:
            a, b = float(s.get("from", 0)), float(s.get("to", 1))
        except (TypeError, ValueError):
            errs.append(f"镜头 {k}（{s['prim']}）：from/to 必须是 0–1 的数"); continue
        a, b = min(1.0, max(0.0, a)), min(1.0, max(0.0, b))
        if b - a < 0.15:
            errs.append(f"镜头 {k}（{s['prim']}）：区间 {a:.2f}–{b:.2f} 太短（至少 0.25）"); continue
        if s["prim"] in nope:
            errs.append(f"镜头 {k}：{s['prim']} 在本章已经用够次数了，换一个原语")
        if desc_text is not None and s["prim"] in NEEDS and not any(w in desc_text for w in NEEDS[s["prim"]]):
            errs.append(f"镜头 {k}：场景描述里没有 {'/'.join(NEEDS[s['prim']][:3])} 这类物件，不能用 {s['prim']}")
        params = s.get("params") if isinstance(s.get("params"), dict) else {}
        params = {pk: pv for pk, pv in params.items() if pk in spec["params"]}
        for pk, ps in spec["params"].items():   # 截断超长字符串
            if ps.get("type") == "string" and isinstance(params.get(pk), str):
                params[pk] = params[pk].strip()[:ps["maxLength"]]
            if ps.get("type") == "array" and isinstance(params.get(pk), list):
                params[pk] = params[pk][:ps.get("maxItems", 9)]
                if ps.get("items", {}).get("type") == "string":
                    params[pk] = [str(x).strip()[:ps["items"]["maxLength"]] for x in params[pk] if str(x).strip()]
        if s["prim"] == "chart":   # 常见误解：以为要自己给数据 → 自动修正（数据一律由系统填）
            ser = [x for x in (params.get("series") or []) if x in ("te", "in", "bl")]
            params["series"] = list(dict.fromkeys(ser)) or ["te", "in"]
            params["notes"] = [{"x": min(1, max(0, float(n.get("x", 0) or 0))), "word": str(n.get("word") or n.get("text") or "").strip()[:6]}
                               for n in (params.get("notes") or []) if isinstance(n, dict) and (n.get("word") or n.get("text"))
                               and isinstance(n.get("x", 0), (int, float))][:3]
        for e in jsonschema.Draft202012Validator({"type": "object", "properties": spec["params"], "required": spec.get("required", [])}).iter_errors(params):
            errs.append(f"镜头 {k}（{s['prim']}）参数 {'/'.join(map(str, e.absolute_path)) or ''}：{e.message[:90]}")
        if s["prim"] == "orbit":
            for pk in ("a", "b", "c"):
                if params.get(pk) and params[pk] not in char_ids:
                    if pk == "c":
                        params.pop("c")
                    else:
                        errs.append(f"镜头 {k}（orbit）：{pk}={params[pk]!r} 不是人物 id（可用：{', '.join(sorted(char_ids))}）")
        for name, val in _texts(s["prim"], params):
            if re.search(r"[你我吗呢吧啊呀]|^(?:别|不要|求)", val):
                errs.append(f"镜头 {k}（{s['prim']}）{name}={val!r}：像人物台词（人称/语气词/祈使），画面上只能是意象短词")
            if _PUNCT.search(val):
                errs.append(f"镜头 {k}（{s['prim']}）{name}={val!r}：画面文字不能带标点，只能是意象短词")
            hit = leaks(val, source)
            if hit:
                errs.append(f"镜头 {k}（{s['prim']}）{name}={val!r}：与原文重合（“{hit}”），画面上只能用你自己的意象短词，不能摘原文")
        clean.append({"prim": s["prim"], "from": round(a, 3), "to": round(b, 3), "params": params})
    words = [v for s in clean for _, v in _texts(s["prim"], s["params"]) if len(v) >= 2]
    dup = sorted({w for w in words if words.count(w) > 1})
    if dup:
        errs.append(f"同一段里重复用了同一个词：{'、'.join(dup)}（每个镜头换一个意象）")
    if len(clean) < 2 and not errs:
        errs.append("至少要 2 个镜头")
    clean.sort(key=lambda s: (s["from"], s["to"]))
    if clean:
        if clean[0]["from"] > 0.001:
            errs.append(f"p=0 时没有任何镜头（第一个镜头从 {clean[0]['from']} 开始）")
        reach = 0.0
        for s in clean:
            if s["from"] > reach + 0.001:
                errs.append(f"滚动区间 {reach:.2f}–{s['from']:.2f} 没有镜头（留了空档）")
            elif reach - s["from"] > 0.16 and s is not clean[0]:
                errs.append(f"{s['prim']} 和前一个镜头重叠了 {reach - s['from']:.2f}（最多 0.12），两个画面会长时间叠在一起")
            reach = max(reach, s["to"])
        if reach < 0.999:
            errs.append(f"p={reach:.2f} 之后没有镜头，必须覆盖到 1")
        for x in [i / 40 for i in range(41)]:
            on = [s for s in clean if s["from"] <= x <= s["to"]]
            if len(on) > 2:
                errs.append(f"p={x:.2f} 同时叠了 {len(on)} 个镜头（最多 2 个）"); break
        dk = [s for s in clean if s["prim"] in DARK]
        if len(dk) > 2:
            errs.append(f"暗色镜头 {len(dk)} 个（最多 2 个）")
        if dk and len(dk) == len(clean):
            errs.append("全部是暗色镜头：至少要有一个纸面（亮）镜头")
        if sum(1 for s in clean if s["prim"] == "chart") > 1:
            errs.append("chart 最多用一次")
    slug = comp.get("slug") if isinstance(comp.get("slug"), str) else ""
    slug = _PUNCT.sub("·", slug.strip())[:10]
    if slug and leaks(slug, source):
        slug = ""
    return {"slug": slug, "shots": clean}, errs


# ------------------------------------------------------------------ rule fallback
def desc_text(h):
    return " ".join(str(x) for x in [h.get("title", ""), h.get("summary", ""), h.get("setting", ""), h.get("motif", ""), " ".join(map(str, h.get("objects") or []))])


def rule_compose(h, char_ids, source="", used=None):
    """规则编排：一定合格。按场景描述里的物件/隐喻关键词挑一个“物件/光影”镜头，再接关系图或排版；遵守本章次数上限。"""
    text = desc_text(h)
    nope = blocked(used)
    def word(*cands, n=4):
        for c in cands:
            c = re.split(r"[，。、,.;；：:\s（）()·]", str(c or "").strip())[0][:n]
            if len(c) >= 2 and not leaks(c, source):
                return c
        return ""
    objs = [str(x) for x in (h.get("objects") or [])]
    title = word(h.get("title"), *objs, "此刻")
    cands = [(("门", "玄关", "进门"), "door", {"word": word(*[o for o in objs if "门" in o], "门缝")}),
             (("车", "夜路", "开车"), "passing", {"word": title}),
             (("勺", "罐", "碗"), "spoon", {"word": word(*[o for o in objs if "勺" in o], "勺子"), "note": word(h.get("setting"), n=6)}),
             (("窗", "光", "傍晚", "黄昏", "午后"), "window", {"word": title, "from": "noon", "to": "dusk"}),
             (("脸红", "红晕", "羞", "烫", "秘密"), "ink", {"word": title[:4], "drops": 2 if "秘密" in text else 1}),
             (("回忆", "记忆", "照片", "告白"), "develop", {"word": title}),
             ((), "typeset", {"words": [title], "mode": "assemble"})]
    lead = next(({"prim": p, "params": {k: v for k, v in pr.items() if v}} for kws, p, pr in cands
                 if p not in nope and (not kws or any(k in text for k in kws)) and (p not in NEEDS or any(w in text for w in NEEDS[p]))),
                None)
    if not lead:   # 关键词原语都用完了：从还能用的通用原语里挑，实在没有才超额用排版
        alt0 = word(*objs, h.get("motif"), "此刻")
        for p_, pr in (("press", {"word": title}), ("develop", {"word": title}), ("chart", {"series": ["te", "in"]}),
                       ("focus", {"far": title, "near": alt0 if alt0 != title else "此刻"})):
            if p_ not in nope:
                lead = {"prim": p_, "params": pr}; break
        lead = lead or {"prim": "typeset", "params": {"words": [title], "mode": "assemble"}}
    ids = [c.get("id") for c in h.get("characters") or [] if isinstance(c, dict) and c.get("id") in char_ids]
    curve = [c for c in h.get("curve") or [] if isinstance(c, dict)]
    second = None
    if len(ids) >= 2 and "orbit" not in nope:
        inn = [min(1, max(0, float(c.get("intimacy", .4) or .4))) for c in curve][:5] or [.3, .6]
        ten = [min(1, max(0, float(c.get("tension", .4) or .4))) for c in curve][:5] or [.4, .6]
        inn = inn if len(inn) >= 2 else inn * 2; ten = ten if len(ten) >= 2 else ten * 2
        second = {"prim": "orbit", "params": {"a": ids[0], "b": ids[1], "dist": [round(1 - v * .85, 2) for v in inn], "tension": [round(v, 2) for v in ten]}}
    if not second:
        alt = word(*objs[1:], h.get("motif"), "余音")
        for p_, pr in (("press", {"word": alt}), ("typeset", {"words": [alt], "mode": "mask"}), ("ink", {"word": alt[:4]}),
                       ("window", {"word": alt, "from": "noon", "to": "dusk"}), ("chart", {"series": ["te", "in"]})):
            if p_ not in nope and p_ != lead["prim"]:
                second = {"prim": p_, "params": pr}; break
    second = second or {"prim": "typeset", "params": {"words": [word(h.get("motif"), "余音")], "mode": "scatter"}}
    slug = "·".join(x for x in [word(h.get("setting"), n=4), title if title != word(h.get("setting"), n=4) else ""] if x)[:10]
    shots = [dict(lead, **{"from": 0, "to": .56}), dict(second, **{"from": .48, "to": 1})]
    comp, errs = validate({"slug": slug, "shots": shots}, char_ids, source)
    if errs:   # 极端情况（关键词全撞原文）：退到纯排版 + 窗光
        comp, _ = validate({"slug": "", "shots": [{"prim": "typeset", "from": 0, "to": .56, "params": {"words": ["此刻"], "mode": "assemble"}},
                                                    {"prim": "window", "from": .48, "to": 1, "params": {"from": "noon", "to": "dusk"}}]}, char_ids, "")
    return comp


# ------------------------------------------------------------------ data + documents
def series_for(plan, a, b, maxn=36):
    P = plan["paras"][a:b + 1]
    if len(P) < 2:
        lo = max(0, a - 3); P = plan["paras"][lo:lo + 6]
    step = max(1, -(-len(P) // maxn))
    pick = P[::step]
    return {k: [round(float(p.get(k, 0) or 0), 3) for p in pick] for k in ("te", "in", "bl")}


def page_payload(comp, fig, seconds, chars, data):
    return {"fig": fig, "slug": comp.get("slug", ""), "seconds": seconds, "shots": comp["shots"],
            "chars": [{"id": c["id"], "name": c["name"]} for c in chars], "data": data}


def _js_json(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def prefix_doc(chars):
    """所有原语模式 hero 共用的文档前缀（CSP + 主题变量 + VRH 运行时 + 原语库）。页面里只放一份。"""
    from .hero import CSP, RUNTIME_JS, theme_css
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP}\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<style>{theme_css(chars)}{LIB_CSS}</style><script>{RUNTIME_JS}</script><script>{LIB_JS}</script></head><body>")


def mount_tail(payload):
    return f"<script>VRP.mount({_js_json(payload)})</script></body></html>"


def build_doc(payload, chars):
    return prefix_doc(chars) + mount_tail(payload)
