"""Stage 2 · 规划：千问只输出分镜 JSON（不输出正文）。

两步：
  A. outline（1 次调用）：元信息、人物表、焦点关系、场景划分。
  B. detail（按场景分批，并行）：每段说话人/在场/情绪、句内对话归属、视觉节点、摘句。
每次调用：JSON schema + 语义校验，不合格把错误回灌重试一次；仍不合格则规则修复并记录 warning。
网关单请求 120s 硬上限 → enable_thinking=false、max_tokens 有上限、每批 ≤ 28 段 / 5000 字。

整本书（≥2 章且 ≥15000 字，或 >200 段）走 plan_book：
  0. cast（1 次）：每章开头摘样 → 全书人物表 / 焦点关系 / 元信息。
  A'. 每章一次 outline（只划分本章场景），B. 每章的 detail 批次 —— 章与章并发，所有 HTTP 调用共用一个
     并发闸（VR_LLM_CONCURRENCY，默认 3）；429/5xx/超时指数退避重试；单章异常只让这一章降级为规则分镜。
  内容过滤拒答：有千问 key → 本批改用千问；没有 → 批次对半拆开重试（最多拆两层），仍被拒的段落用规则化默认分镜
  （按人名+说/问/道判断说话人、场景内出现过的人名算在场、中性数值），绝不复制上一段。
"""
import json, math, re, threading, time, urllib.request, urllib.error, concurrent.futures as cf
import jsonschema
from .registry import COMPONENTS, HERO_MOTIFS, TONES, prompt_catalog

import os

# provider 配置：凭证由网关按 host 注入；若本地直连，可设环境变量（代码与日志里不出现 key）。
PROVIDERS = {
    "qwen": {   # 质量模式
        "endpoint": "https://maas.qianwenaiapi.com/compatible-mode/v1/chat/completions",
        "model": "qwen3.8-max", "fallback": "qwen3.7-plus", "key_env": "DASHSCOPE_API_KEY",
        "extra": {"enable_thinking": False, "temperature": 0.4},
        "reasoning_headroom": 0, "timeout": 115,
    },
    "mercury": {  # 快速模式（默认）：扩散式 LLM，带 reasoning_tokens，会吃掉 max_tokens → 额外留足余量
        "endpoint": "https://api.inceptionlabs.ai/v1/chat/completions",
        "model": "mercury-3", "fallback": "mercury-3", "key_env": "INCEPTION_API_KEY",
        # 文档：mercury-3 温度 0.5–1.0；reasoning_effort instant|low|medium|high；最大输出 131072。
        "extra": {"temperature": 0.5, "reasoning_effort": "instant"},  # 实测 low 仍有 ~90% reasoning_tokens、耗时≈千问；instant 无推理、快 3 倍
        "reasoning_headroom": 12000, "timeout": 90, "max_key": "max_completion_tokens",
    },
}
DEFAULT_PROVIDER = "mercury"


def resolve_model(spec):
    """'mercury' / 'qwen' / 具体模型名（mercury-3、qwen3.7-plus…）→ (provider, model)"""
    spec = (spec or DEFAULT_PROVIDER).strip()
    if spec in PROVIDERS:
        return spec, PROVIDERS[spec]["model"]
    if spec.startswith("mercury"):
        return "mercury", spec
    if spec.startswith("qwen"):
        return "qwen", spec
    raise ValueError(f"未知模型/提供方：{spec}（可用 mercury / qwen / 具体模型名）")


class Stats:
    def __init__(self, provider="", model=""):
        self.provider = provider; self.model = model
        self.effort = PROVIDERS.get(provider, {}).get("extra", {}).get("reasoning_effort")
        self.calls = 0; self.retries = 0; self.failed = 0; self.tokens_in = 0; self.tokens_out = 0; self.reasoning_tokens = 0
        self.tasks = 0; self.first_pass = 0; self.passed = 0; self.fallbacks = 0
        self.transient = 0; self.content_filtered = 0; self.rule_paras = 0; self.rule_scenes_chapters = 0   # JSON 合格率：首次合格 / 最终合格 / 任务数
        self.warnings = []; self.log = []

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


_LLM_SEM = threading.BoundedSemaphore(max(1, int(os.environ.get("VR_LLM_CONCURRENCY", 3))))
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
MAX_TRANSIENT = int(os.environ.get("VR_LLM_RETRIES", 8))   # 实测 Mercury 免费档整本书会遇到上百次 429


class ContentFiltered(Exception):
    """提供方内容过滤拒答（同样内容重试没有意义）。"""


def _sleep_for(attempt, retry_after=None):
    try:
        if retry_after:
            return min(30.0, float(retry_after))
    except ValueError:
        pass
    return min(30.0, 2.0 * (2 ** attempt)) * (0.75 + 0.5 * (hash((attempt, time.time())) % 100) / 100)


def chat(messages, model, max_tokens, stats, provider="qwen", json_mode=True, extra_override=None, timeout=None):
    """一次 LLM 调用。限流（429）/ 5xx / 网络超时在这里退避重试，不消耗"格式不合格重试"的次数。
    json_mode=False：自由文本输出（hero 动画模块）；extra_override 只作用于本次调用。"""
    cfg = PROVIDERS[provider]
    body = {"model": model, "messages": messages, cfg.get("max_key", "max_tokens"): max_tokens + cfg["reasoning_headroom"],
            **cfg["extra"], **(extra_override or {})}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    key = os.environ.get(cfg["key_env"]) or "injected-by-gateway"
    data = json.dumps(body).encode()
    for attempt in range(MAX_TRANSIENT + 1):
        req = urllib.request.Request(cfg["endpoint"], data=data,
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        t = time.time()
        with _LLM_SEM:
            stats.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=timeout or cfg["timeout"]) as r:
                    d = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code in RETRY_STATUS and attempt < MAX_TRANSIENT:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    stats.transient += 1; stats.log.append({"model": model, "transient": f"HTTP {e.code}", "attempt": attempt + 1})
                    wait = _sleep_for(attempt, ra)
                else:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < MAX_TRANSIENT:
                    stats.transient += 1; stats.log.append({"model": model, "transient": type(e).__name__, "attempt": attempt + 1})
                    wait = _sleep_for(attempt)
                else:
                    raise
        time.sleep(wait)   # 退避时不占并发名额
    u = d.get("usage", {}) or {}
    rt = ((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    stats.tokens_in += u.get("prompt_tokens", 0); stats.tokens_out += u.get("completion_tokens", 0); stats.reasoning_tokens += rt
    stats.log.append({"model": model, "sec": round(time.time() - t, 1), "out_tokens": u.get("completion_tokens"),
                      "reasoning_tokens": rt, "finish": d["choices"][0].get("finish_reason")})
    return d["choices"][0]["message"]["content"], d["choices"][0].get("finish_reason")


def parse_json(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    i, j = s.find("{"), s.rfind("}")
    try:
        return json.loads(s[i:j + 1])
    except json.JSONDecodeError as e:
        if "Extra data" not in str(e):
            raise
    # 模型偶尔把一个对象拆成几个连续的 JSON 对象输出（{"paras":…}{"visuals":…}）→ 逐个解析后合并
    dec = json.JSONDecoder(); pos = i; merged = {}
    while 0 <= pos < len(s):
        o, end = dec.raw_decode(s, pos)
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, list) and isinstance(merged.get(k), list): merged[k] += v
                else: merged.setdefault(k, v)
        nxt = s.find("{", end)
        pos = nxt
    return merged


def normalize(o):
    """只做无歧义的类型修正（"81"→81、"0.5"→0.5），不猜内容。"""
    def num(x, f):
        if isinstance(x, str):
            try: return f(x.strip())
            except ValueError: return x
        return x
    if isinstance(o, dict) and isinstance(o.get("paras"), list):
        KEYS = (("i", "idx", "index"), ("sp", "speaker"), ("present", "pr"), ("intimacy", "in"), ("tension", "te"),
                ("blush", "bl"), ("blush_id", "bw", "blush_who"))
        def arr(d):  # {'i':44,'sp':'',...} → [44,'',...]（键名明确时才转换）
            out = []
            for ks in KEYS:
                k = next((k for k in ks if k in d), None)
                if k is None: return d
                out.append(d[k])
            return out
        o["paras"] = [arr(p) if isinstance(p, dict) else p for p in o["paras"]]
        for p in o["paras"]:
            if isinstance(p, list) and len(p) == 7:
                p[0] = num(p[0], int)
                for k in (3, 4, 5): p[k] = num(p[k], float)
                if p[1] is None: p[1] = ""
                if p[6] is None: p[6] = ""
        # 有的模型把对象写成数组：[i, q, sp] / [at, text]
        if isinstance(o.get("utterances"), list):
            # [364, {"i":364,"q":…,"sp":…}] 这种"段号 + 对象"的写法 → 取对象
            o["utterances"] = [u[1] if isinstance(u, list) and len(u) == 2 and isinstance(u[1], dict) else u for u in o["utterances"]]
            o["utterances"] = [dict(zip(("i", "q", "sp"), u)) if isinstance(u, list) and len(u) == 3 else u for u in o["utterances"]]
        if isinstance(o.get("quotes"), list):
            o["quotes"] = [dict(zip(("at", "text", "w"), u)) if isinstance(u, list) and len(u) in (2, 3) else u for u in o["quotes"]]
            # 超长摘句本来就会被渲染端丢掉，不值得为它让整批重试
            o["quotes"] = [q for q in o["quotes"] if not (isinstance(q, dict) and isinstance(q.get("text"), str) and len(q["text"]) > 60)]
        for key, f in (("visuals", "at"), ("quotes", "at"), ("utterances", "i")):
            for x in o.get(key, []) or []:
                if isinstance(x, dict) and f in x: x[f] = num(x[f], int)
    if isinstance(o, dict) and isinstance(o.get("scenes"), list):
        for x in o["scenes"]:
            if isinstance(x, dict):
                for f in ("start", "end"): x[f] = num(x.get(f), int)
    return o


def call_validated(messages, schema, semantic, model, max_tokens, stats, tag, provider="qwen"):
    """call → parse → schema → semantic；失败把错误回灌重试一次。返回 (obj, errors)。"""
    errs = []; obj = None; stats.tasks += 1
    for attempt in range(2):
        msgs = list(messages)
        if errs:
            stats.retries += 1
            msgs += [{"role": "user", "content": "上一次输出不合格，错误如下，请完整重新输出合格的 JSON：\n- " + "\n- ".join(errs[:15])}]
        try:
            raw, finish = chat(msgs, model, max_tokens, stats, provider)
            if finish == "length":
                raise ValueError("输出被截断(length)，请更紧凑地输出")
            obj = parse_json(raw)
        except urllib.error.HTTPError as e:
            try: detail = e.read().decode("utf-8", "replace")[:200]
            except Exception: detail = ""
            errs = [f"HTTP {e.code}"]; stats.log.append({"tag": tag, "error": errs[0], "detail": detail})
            if "content_filter" in detail and provider != "qwen":
                stats.content_filtered += 1
                if not llm_available("qwen"):
                    # 没有千问 key：同样内容重试没有意义，交给调用方拆批 / 规则降级
                    stats.tasks -= 1
                    raise ContentFiltered(tag)
                # Mercury 的内容过滤会误伤文学文本（如师生暧昧情节）→ 这一批改由千问规划
                stats.warnings.append(f"[{tag}] {provider} 内容过滤拒答，本批改用 qwen")
                provider, model = "qwen", PROVIDERS["qwen"]["model"]; stats.fallbacks = getattr(stats, "fallbacks", 0) + 1
            elif attempt == 0:
                model = PROVIDERS[provider]["fallback"]
            continue
        except Exception as e:  # timeout / bad json / truncation
            errs = [f"{type(e).__name__}: {e}"[:200]]; stats.log.append({"tag": tag, "error": errs[0]})
            continue
        obj = normalize(obj)
        v = jsonschema.Draft202012Validator(schema)
        errs = [f"{'/'.join(map(str, e.absolute_path))}: {e.message}"[:200] for e in v.iter_errors(obj)]
        if not errs:
            errs = semantic(obj)
        if not errs:
            stats.passed += 1; stats.first_pass += (attempt == 0)
            return obj, []
        stats.log.append({"tag": tag, "invalid": errs[:3]})
    stats.failed += 1
    stats.warnings.append(f"[{tag}] 两次均不合格，使用规则修复：{errs[:5]}")
    return obj, errs


# ------------------------------------------------------------------ A. outline
OUTLINE_SCHEMA = {
    "type": "object", "required": ["meta", "characters", "focal_pair", "scenes"],
    "properties": {
        "meta": {"type": "object", "required": ["title", "subtitle", "hero_motif", "hero_segments"],
                 "properties": {"title": {"type": "string", "maxLength": 30}, "author": {"type": "string"},
                                "work": {"type": "string"}, "subtitle": {"type": "string", "maxLength": 40},
                                "hero_motif": {"enum": HERO_MOTIFS},
                                "hero_segments": {"type": "array", "items": {"type": "integer"}, "maxItems": 2}}},
        "characters": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
            "type": "object", "required": ["id", "name", "color"],
            "properties": {"id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,15}$"}, "name": {"type": "string", "maxLength": 10},
                           "aliases": {"type": "array", "items": {"type": "string"}},
                           "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}}}},
        "focal_pair": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
        "scenes": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["start", "end", "name", "place", "tone"],
            "properties": {"start": {"type": "integer"}, "end": {"type": "integer"},
                           "name": {"type": "string", "maxLength": 10}, "time": {"type": "string", "maxLength": 12},
                           "place": {"type": "string", "maxLength": 12}, "tone": {"enum": TONES}}}},
    }}

OUTLINE_SYS = """你是小说的“分镜导演”。你只输出 JSON，绝不改写、续写或复述正文。
给你一份按段编号的原文。请输出：
{"meta":{"title":"作品或章节标题","author":"作者(不确定则空)","work":"书名(不确定则空)","subtitle":"首屏一句副标题，可列主要人物，如 人物甲 · 人物乙","hero_motif":"door|window|lamp|moon 选最贴合开篇的意象","hero_segments":[若开头有日期/地点小标题行，填其编号，最多2个，否则空]},
 "characters":[{"id":"小写字母短id","name":"常用名","aliases":["别称/代词指代的称呼"],"color":"#RRGGBB"}],
 "focal_pair":["核心关系的两个人物id；若只有一个核心人物则填他和最常与之对手戏的人"],
 "scenes":[{"start":起始段号,"end":结束段号(含),"name":"场景名≤8字","time":"时间≤10字","place":"地点≤10字","tone":"day|dim|night|memory"}]}
规则：
- 人物最多 8 个，只收有台词或推动情节的；“众人/酒客”这类群体也可以作为一个人物。颜色要彼此区分明显、在米白纸面上可读（偏深、饱和度中等），不要用接近 #FF7A59 的橙粉色（留给“脸红”）。
- 场景必须首尾相接、按顺序完整覆盖所有段号（第一个 start=0，最后一个 end=最大段号，前一个 end+1=后一个 start）。场景按“时间/地点/在场人物/情绪转折”切分，一般每 3–15 段一个场景（单段特别长的文本可以 1–3 段一个场景），不得超过 20 段；地点变化（进门、上车、出门）必须切场景；回忆/闪回哪怕只有一段也要单独成场景并标 tone=memory；夜晚、车内、昏暗处用 night；脚注段归入最后一个场景。场景名要具体（如“门铃”“窗外”“车里”），不要用“告别与车内”这类拼接名。
- 只输出 JSON。"""


def outline_semantic(seg_count, seg_hero_ok):
    def f(o):
        e = []
        ids = [c["id"] for c in o["characters"]]
        if len(set(ids)) != len(ids):
            e.append("characters.id 有重复")
        for x in o["focal_pair"]:
            if x not in ids:
                e.append(f"focal_pair 中的 {x} 不在 characters 里")
        sc = o["scenes"]
        if sc[0]["start"] != 0:
            e.append("第一个场景 start 必须是 0")
        if sc[-1]["end"] != seg_count - 1:
            e.append(f"最后一个场景 end 必须是 {seg_count - 1}")
        for a, b in zip(sc, sc[1:]):
            if b["start"] != a["end"] + 1:
                e.append(f"场景不连续：{a['name']} end={a['end']} 后接 {b['name']} start={b['start']}")
        for s in sc:
            if s["end"] - s["start"] + 1 > 20:
                e.append(f"场景 {s['name']} 有 {s['end'] - s['start'] + 1} 段，超过 20 段，请按情绪转折再切分")
            if s["end"] < s["start"]:
                e.append(f"场景 {s['name']} end<start")
        for h in o["meta"]["hero_segments"]:
            if not (0 <= h < seg_count) or h > 2:
                e.append(f"hero_segments {h} 只能是开头 0-2 号段")
        return e
    return f


def numbered(segs, budget=16000):
    total = sum(len(s["text"]) for s in segs)
    out = []
    for s in segs:
        t = s["text"]
        if total > budget and len(t) > 70:
            t = t[:70] + f"…(共{len(s['text'])}字)"
        tag = {"footnote": "[脚注] ", "heading": "[标题] "}.get(s["role"], "")
        out.append(f"[{s['i']}] {tag}{t}")
    return "\n".join(out)


# ------------------------------------------------------------------ B. detail
def detail_schema():
    vis_types = list(COMPONENTS)
    return {
        "type": "object", "required": ["paras", "visuals", "quotes"],
        "properties": {
            "paras": {"type": "array", "items": {"type": "array", "minItems": 7, "maxItems": 7,
                      "prefixItems": [{"type": "integer"}, {"type": "string"}, {"type": "array", "items": {"type": "string"}},
                                      {"type": "number", "minimum": 0, "maximum": 1}, {"type": "number", "minimum": 0, "maximum": 1},
                                      {"type": "number", "minimum": 0, "maximum": 1}, {"type": "string"}]}},
            "utterances": {"type": "array", "items": {"type": "object", "required": ["i", "q", "sp"],
                           "properties": {"i": {"type": "integer"}, "q": {"type": "string"}, "sp": {"type": "string"}}}},
            "visuals": {"type": "array", "items": {"type": "object", "required": ["at", "type", "params"],
                        "properties": {"at": {"type": "integer"}, "pos": {"enum": ["before", "after"]},
                                       "type": {"enum": vis_types}, "params": {"type": "object"}}}},
            "quotes": {"type": "array", "items": {"type": "object", "required": ["at", "text"],
                       "properties": {"at": {"type": "integer"}, "text": {"type": "string", "maxLength": 60}, "w": {"type": "number"}}}},
        }}


DETAIL_SYS = """你是小说的“分镜导演”。你只输出 JSON，绝不改写或复述正文（摘句与对话片段除外，它们必须是原文的逐字子串）。
人物表：{chars}
焦点关系：{focal}
可用视觉组件（type 与参数）：
{catalog}

对下面给出的每一段都输出一行 paras：[段号, 说话人id或"", [在场人物id…], 亲密度, 紧张/压力, 脸红/难堪, 脸红者id或""]
- 说话人：这一段主要是某人的台词时填其 id，包括没有引号的对话（如“…，他说。”“真的吗？”这类靠上下文判断的行）；纯叙述填 ""。第一人称叙述者（“我”）的叙述、回忆、感想不是台词，填 ""。
- 在场人物：此刻在场景里的人物 id。
- 亲密度：焦点关系两人此刻的心理距离（0 疏远/对立，1 极亲密）；紧张：压迫、尴尬、冲突强度；脸红：难堪、羞赧、脸发烫（文中出现脸红/耳根发烫/涨红了脸等要给高值并填脸红者）。数值保留两位小数，随情节平滑变化。
utterances：仅当一段里叙述和引号台词混在一起、或一段里有多人轮流说话时（整段就是一个人的台词时不要列），列出其中的台词片段 {{"i":段号,"q":"逐字子串(含引号可不含)","sp":"人物id"}}，每段最多 6 条，片段 4–60 字。
visuals：在情节节点插入组件 {{"at":段号,"pos":"before|after","type":"组件名","params":{{…}}}}。
- params 必须严格符合上面的类型：数组参数（labels/cards/entries/props）必须是 JSON 数组；sound.count 为 3–12 的整数。
- 每个场景 1–3 个，必须和该处情节直接对应（文中真的出现门铃才用 doorbell，出现车才用 car_night…）；不要相邻两段都放；caption ≤12 字，用中文短语。
- 场景开头交代空间优先用 interior/window；同一个房间的 interior 全文只用一次，同一物件的 object 最多两次；回忆场景用 stairs_memory；沉默/停顿用 silence。
quotes：挑 0–2 句最有分量的话做大字摘句 {{"at":段号,"text":"原文逐字子串，8–40字","w":分量1-5}}，优先选点题、意象鲜明、能脱离上下文成立的句子（w=5），普通台词 w≤2。
只输出 JSON：{{"paras":[…],"utterances":[…],"visuals":[…],"quotes":[…]}}"""


def detail_semantic(segs_by_i, idxs, char_ids):
    def f(o):
        e = []
        got = [p[0] for p in o["paras"]]
        miss = sorted(set(idxs) - set(got))
        if miss:
            e.append(f"paras 缺少段号 {miss[:20]}")
        extra = sorted(set(got) - set(idxs))
        if extra:
            e.append(f"paras 出现了不属于本批的段号 {extra[:10]}")
        # 人物表外的 id（整本书里的次要人物）不重试：assemble 会把它们当作"无"处理
        return e[:20]
    return f


def batches(scenes, segs, max_paras=28, max_chars=5000):
    out, cur, n, c = [], [], 0, 0
    for s in scenes:
        sn = s["end"] - s["start"] + 1
        sc = sum(len(segs[i]["text"]) for i in range(s["start"], s["end"] + 1))
        if cur and (n + sn > max_paras or c + sc > max_chars):
            out.append(cur); cur, n, c = [], 0, 0
        cur.append(s); n += sn; c += sc
    if cur:
        out.append(cur)
    # a single huge scene: split by paragraphs
    final = []
    for b in out:
        if len(b) == 1 and (b[0]["end"] - b[0]["start"] + 1 > max_paras * 1.5):
            s = b[0]
            for a in range(s["start"], s["end"] + 1, max_paras):
                final.append([dict(s, start=a, end=min(s["end"], a + max_paras - 1), _part=True)])
        else:
            final.append(b)
    return final


def repair_outline(o, segs):
    n = len(segs)
    o = o or {}
    o.setdefault("meta", {}); m = o["meta"]
    m.setdefault("title", ""); m.setdefault("subtitle", ""); m.setdefault("hero_segments", [])
    if m.get("hero_motif") not in HERO_MOTIFS:
        m["hero_motif"] = "door"
    m["hero_segments"] = [h for h in m["hero_segments"] if isinstance(h, int) and 0 <= h <= 2]
    chars = [c for c in o.get("characters", []) if isinstance(c, dict) and c.get("id")]
    seen = set(); o["characters"] = []
    for c in chars:
        if c["id"] not in seen:
            seen.add(c["id"]); o["characters"].append(c)
    o["focal_pair"] = [x for x in o.get("focal_pair", []) if x in seen][:2]
    sc = sorted([s for s in o.get("scenes", []) if isinstance(s, dict) and "start" in s], key=lambda s: s["start"])
    fixed, pos = [], 0
    for s in sc:
        if s["start"] > pos and fixed:
            fixed[-1]["end"] = s["start"] - 1
        s["start"] = pos if not fixed else fixed[-1]["end"] + 1
        if s["end"] < s["start"]:
            continue
        s["end"] = min(s["end"], n - 1)
        if s.get("tone") not in TONES:
            s["tone"] = "day"
        fixed.append(s); pos = s["end"] + 1
        if pos >= n:
            break
    if not fixed:
        fixed = [{"start": 0, "end": n - 1, "name": "全文", "place": "", "tone": "day"}]
    fixed[-1]["end"] = n - 1
    o["scenes"] = fixed
    return o


BOOK_MIN_CHARS = 15000
BOOK_MIN_SEGS = 200
PSEUDO_CHAPTER = 120   # 没有章节标题的超长文本：每 ~120 段当一章来规划


def is_book(seg):
    segs = seg["segments"]
    chars = sum(len(x["text"]) for x in segs)
    return (len(seg.get("chapters") or []) >= 2 and chars >= BOOK_MIN_CHARS) or len(segs) > BOOK_MIN_SEGS


def book_chapters(seg):
    chs = [dict(c) for c in (seg.get("chapters") or [])] or [{"start": 0, "end": len(seg["segments"]) - 1, "title": ""}]
    out = []
    for c in chs:   # 过长的章（或整篇无章名）再按 ~120 段切成"部分"
        n = c["end"] - c["start"] + 1
        k = max(1, math.ceil(n / (PSEUDO_CHAPTER * 1.5))) if n > PSEUDO_CHAPTER * 1.5 else 1
        size = math.ceil(n / k)
        for j in range(k):
            a = c["start"] + j * size; b = min(c["end"], a + size - 1)
            t = c["title"] if j == 0 else (f"{c['title']}（{j + 1}）" if c["title"] else "")
            out.append({"start": a, "end": b, "title": t, "_cont": j > 0})
    untitled = [c for c in out if not c["title"]]
    if len(untitled) == len(out) and len(out) > 1:
        for k, c in enumerate(out):
            c["title"] = f"第 {k + 1} 部分"
    return out


def _prep(model_spec, effort):
    provider, model = resolve_model(model_spec)
    if effort and provider == "mercury":
        PROVIDERS["mercury"]["extra"]["reasoning_effort"] = effort
    return provider, model


def _detail_runner(segs, o, provider, model, stats, progress):
    char_ids = [c["id"] for c in o["characters"]]
    chars_txt = "；".join(f"{c['id']}={c['name']}" + (f"(又称{'/'.join(c.get('aliases', []))})" if c.get("aliases") else "")
                         for c in o["characters"])
    focal = " 与 ".join(next(c["name"] for c in o["characters"] if c["id"] == x) for x in o["focal_pair"]) or "无"
    sysmsg = DETAIL_SYS.format(chars=chars_txt, focal=focal, catalog=prompt_catalog())
    segs_by_i = {s["i"]: s["text"] for s in segs}

    def run(b, depth=0, quiet=False):
        """返回 [(idxs, obj|None, errs)]；内容过滤拒答时对半拆开再试（最多两层），仍被拒的返回 None → 规则分镜。"""
        idxs = [i for s in b for i in range(s["start"], s["end"] + 1)]
        body = "\n".join(
            f"【场景 {s['name']}｜{s.get('time', '')}｜{s.get('place', '')}｜段 {s['start']}–{s['end']}】\n" +
            "\n".join(f"[{i}] {segs[i]['text']}" for i in range(s['start'], s['end'] + 1)) for s in b)
        m = [{"role": "system", "content": sysmsg}, {"role": "user", "content": body}]
        mt = min(8000, 600 + 90 * len(idxs))
        tag = f"detail {idxs[0]}-{idxs[-1]}"
        try:
            r, e = call_validated(m, detail_schema(), detail_semantic(segs_by_i, idxs, char_ids), model, mt, stats, tag, provider)
            hard = e and (r is None or any(k in x for x in e for k in ("截断", "JSONDecodeError", "HTTP", "缺少段号")))
            if hard and depth < 1 and len(idxs) > 8:
                # 截断 / 坏 JSON / 漏段：两次都失败时对半拆开再试一次，比整批规则修复保留更多分镜
                mid = idxs[len(idxs) // 2]
                stats.warnings.append(f"[{tag}] 两次不合格，拆成两半重试")
                left = [dict(s, end=min(s["end"], mid - 1)) for s in b if s["start"] < mid]
                right = [dict(s, start=max(s["start"], mid)) for s in b if s["end"] >= mid]
                keep = {"paras": r.get("paras", [])} if isinstance(r, dict) else None   # 失败批次只留可用的逐段数据，组件/摘句以拆分后的为准
                out = [(idxs, keep, e)] + run(left, depth + 1, True) + run(right, depth + 1, True)
            else:
                out = [(idxs, r, e)]
        except ContentFiltered:
            if depth < 2 and len(idxs) > 4:
                mid = idxs[len(idxs) // 2]
                left = [dict(s, end=min(s["end"], mid - 1)) for s in b if s["start"] < mid]
                right = [dict(s, start=max(s["start"], mid)) for s in b if s["end"] >= mid]
                stats.warnings.append(f"[{tag}] 内容过滤拒答，拆成两半重试")
                out = run(left, depth + 1, True) + run(right, depth + 1, True)
            else:
                stats.warnings.append(f"[{tag}] 内容过滤拒答，{len(idxs)} 段改用规则化默认分镜")
                out = [(idxs, None, ["content_filter"])]
        if not quiet:
            progress("detail+", f"段 {idxs[0]}–{idxs[-1]} 完成")
        return out
    return run


def plan(seg, progress=lambda *a: None, stats=None, model_spec=None, effort=None):
    if is_book(seg):
        return plan_book(seg, progress, stats, model_spec, effort)
    provider, model = _prep(model_spec, effort)
    stats = stats or Stats(provider, model)
    segs = seg["segments"]; n = len(segs)
    progress("outline", f"规划场景与人物（{n} 段，{provider}:{model}" + (f"/{PROVIDERS[provider]['extra'].get('reasoning_effort')}" if provider == "mercury" else "") + "）")
    msgs = [{"role": "system", "content": OUTLINE_SYS},
            {"role": "user", "content": f"共 {n} 段（编号 0–{n - 1}）：\n" + numbered(segs)}]
    try:
        o, errs = call_validated(msgs, OUTLINE_SCHEMA, outline_semantic(n, True), model, 3000, stats, "outline", provider)
    except ContentFiltered:
        stats.warnings.append("[outline] 内容过滤拒答，场景改用规则切分"); o = None
    if not o or not o.get("scenes"):
        o = dict(o or {}, scenes=rule_scenes(0, n - 1, ""))
    o = repair_outline(o, segs)
    if not o["characters"]:
        o["characters"] = [{"id": "narrator", "name": "叙述者", "color": "#2b4a6f"}]
    bs = batches(o["scenes"], segs)
    progress("detail", f"逐段分镜：{len(bs)} 批并行")
    run = _detail_runner(segs, o, provider, model, stats, progress)
    results = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for res in ex.map(run, bs):
            results += res
    return assemble(o, results, segs, stats), stats


# ------------------------------------------------------------------ whole book
CAST_SCHEMA = {
    "type": "object", "required": ["meta", "characters", "focal_pair"],
    "properties": {
        "meta": OUTLINE_SCHEMA["properties"]["meta"] | {"required": ["title", "subtitle", "hero_motif"]},
        "characters": dict(OUTLINE_SCHEMA["properties"]["characters"], maxItems=10),
        "focal_pair": OUTLINE_SCHEMA["properties"]["focal_pair"]}}

CAST_SYS = """你是长篇小说的“分镜导演”。你只输出 JSON，绝不改写、续写或复述正文。
给你一本书每一章开头的节选（章与章之间有时间跳跃）。请输出全书通用的：
{"meta":{"title":"书名或标题","author":"作者(不确定则空)","work":"书名(不确定则空)","subtitle":"首屏一句副标题，可列主要人物，如 人物甲 · 人物乙","hero_motif":"door|window|lamp|moon 选最贴合开篇的意象","hero_segments":[]},
 "characters":[{"id":"小写字母短id","name":"常用名","aliases":["别称/昵称/姓氏称呼"],"color":"#RRGGBB"}],
 "focal_pair":["全书核心关系的两个人物id"]}
规则：人物最多 10 个，按全书重要性排序，只收反复出现、有台词的；颜色彼此区分明显、在米白纸面上可读（偏深、饱和度中等），不要用接近 #FF7A59 的橙粉色。只输出 JSON。"""

CH_OUTLINE_SCHEMA = {"type": "object", "required": ["scenes"], "properties": {"scenes": OUTLINE_SCHEMA["properties"]["scenes"]}}

CH_OUTLINE_SYS = """你是小说的“分镜导演”。你只输出 JSON，绝不改写、续写或复述正文。
全书人物：{chars}
下面是其中一章（段落带全书编号 {a}–{b}）。只为这一章划分场景：
{{"scenes":[{{"start":起始段号,"end":结束段号(含),"name":"场景名≤8字","time":"时间≤10字","place":"地点≤10字","tone":"day|dim|night|memory"}}]}}
规则：
- 场景必须首尾相接、按顺序完整覆盖本章所有段号（第一个 start={a}，最后一个 end={b}，前一个 end+1=后一个 start）。
- 按“时间/地点/在场人物/情绪转折”切分，一般每 3–15 段一个场景，不得超过 20 段；地点变化必须切；回忆/闪回单独成场景并标 tone=memory；夜晚、车内、昏暗处用 night。
- 场景名要具体（如“门铃”“车里”“厨房”），不要拼接名。只输出 JSON。"""


def chapter_outline_semantic(a, b):
    def f(o):
        e = []
        sc = o["scenes"]
        if sc[0]["start"] != a:
            e.append(f"第一个场景 start 必须是 {a}")
        if sc[-1]["end"] != b:
            e.append(f"最后一个场景 end 必须是 {b}")
        for x, y in zip(sc, sc[1:]):
            if y["start"] != x["end"] + 1:
                e.append(f"场景不连续：{x['name']} end={x['end']} 后接 {y['name']} start={y['start']}")
        for x in sc:   # 超过 20 段的场景由 clamp_scenes 机械切开，不为此重试
            if x["end"] < x["start"] or x["start"] < a or x["end"] > b:
                e.append(f"场景 {x['name']} 段号越界或 end<start")
        return e[:15]
    return f


def rule_scenes(a, b, title, size=14):
    """规则切场景：每 ≤size 段一个，名字用章名（或“第 k 节”）。"""
    n = b - a + 1; k = max(1, math.ceil(n / size)); step = math.ceil(n / k); out = []
    base = (title or "").strip()[:6]
    for j in range(k):
        s0 = a + j * step; s1 = min(b, s0 + step - 1)
        if s0 > b: break
        out.append({"start": s0, "end": s1, "name": (f"{base}·{j + 1}" if base and k > 1 else base) or f"第{j + 1}节",
                    "time": "", "place": "", "tone": "day", "_rule": True})
    return out


def clamp_scenes(scenes, a, b, title):
    """把一章的场景修成恰好覆盖 [a, b]、首尾相接；修不了就规则切。"""
    sc = sorted([x for x in scenes or [] if isinstance(x, dict) and isinstance(x.get("start"), int) and isinstance(x.get("end"), int)],
                key=lambda x: x["start"])
    out, pos = [], a
    for x in sc:
        x = dict(x); x["start"] = pos; x["end"] = min(max(x["end"], pos), b)
        if x.get("tone") not in TONES: x["tone"] = "day"
        x["name"] = str(x.get("name") or title or "场景")[:10]
        for k in ("time", "place"): x[k] = str(x.get(k) or "")[:12]
        out.append(x); pos = x["end"] + 1
        if pos > b: break
    if not out:
        return rule_scenes(a, b, title)
    out[-1]["end"] = b
    final = []
    for x in out:   # 仍然超长的场景按 ≤18 段切开
        n = x["end"] - x["start"] + 1
        if n > 20:
            for j, y in enumerate(rule_scenes(x["start"], x["end"], x["name"], 18)):
                final.append(dict(x, start=y["start"], end=y["end"], name=(x["name"][:7] + (f"·{j + 1}" if j else ""))))
        else:
            final.append(x)
    return final


def cast_sample(segs, chapters, budget=18000):
    per = max(300, budget // max(1, len(chapters)))
    parts = []
    for c in chapters:
        if c.get("_cont"):
            continue
        txt, n = [], 0
        for i in range(c["start"], c["end"] + 1):
            t = segs[i]["text"]
            if segs[i]["role"] == "heading": continue
            txt.append(t); n += len(t)
            if n >= per: break
        body = "\n".join(txt)
        parts.append(f"【{c['title'] or '（无章名）'}】\n{body[:per]}")
    return "\n\n".join(parts)


def plan_book(seg, progress=lambda *a: None, stats=None, model_spec=None, effort=None):
    provider, model = _prep(model_spec, effort)
    stats = stats or Stats(provider, model)
    segs = seg["segments"]; n = len(segs)
    chs = book_chapters(seg); N = len(chs)
    conc = int(os.environ.get("VR_LLM_CONCURRENCY", 3))
    eff = f"/{PROVIDERS[provider]['extra'].get('reasoning_effort')}" if provider == "mercury" else ""
    progress("outline", f"整本书模式：{N} 章 · {n} 段 · {provider}:{model}{eff} · 并发 {conc}")
    # 0. cast
    msgs = [{"role": "system", "content": CAST_SYS}, {"role": "user", "content": cast_sample(segs, chs)}]
    try:
        cast, errs = call_validated(msgs, CAST_SCHEMA, outline_semantic_cast, model, 2500, stats, "cast", provider)
    except ContentFiltered:
        stats.warnings.append("[cast] 内容过滤拒答，改用第一章节选重试")
        try:
            msgs[1]["content"] = cast_sample(segs, chs[:1], 6000)
            cast, errs = call_validated(msgs, CAST_SCHEMA, outline_semantic_cast, model, 2500, stats, "cast-retry", provider)
        except ContentFiltered:
            cast = None
    o = repair_outline(dict(cast or {}, scenes=[{"start": 0, "end": n - 1, "name": "全书", "tone": "day"}]), segs)
    if not o["characters"]:
        o["characters"] = [{"id": "narrator", "name": "叙述者", "color": "#2b4a6f"}]
        stats.warnings.append("人物表规划失败，使用占位人物")
    o["meta"]["hero_segments"] = []
    chars_txt = "；".join(f"{c['id']}={c['name']}" + (f"(又称{'/'.join(c.get('aliases', []))})" if c.get("aliases") else "")
                         for c in o["characters"])
    progress("cast", f"人物 {len(o['characters'])} 个：" + "、".join(c["name"] for c in o["characters"]))
    run = _detail_runner(segs, o, provider, model, stats, lambda *a: None)
    done = [0]; lock = threading.Lock()

    def do_chapter(k):
        c = chs[k]; a, b = c["start"], c["end"]; label = f"第 {k + 1}/{N} 章「{c['title'] or '无题'}」"
        degraded = []
        try:
            sub = segs[a:b + 1]
            m = [{"role": "system", "content": CH_OUTLINE_SYS.format(chars=chars_txt, a=a, b=b)},
                 {"role": "user", "content": f"本章共 {b - a + 1} 段（编号 {a}–{b}）：\n" + numbered(sub)}]
            try:
                co, e = call_validated(m, CH_OUTLINE_SCHEMA, chapter_outline_semantic(a, b), model, 2500, stats, f"outline ch{k + 1}", provider)
                if e: degraded.append("场景已规则修复")
            except ContentFiltered:
                co = None; degraded.append("场景规则切分（内容过滤）")
            scenes = clamp_scenes((co or {}).get("scenes"), a, b, c["title"])
            if all(x.get("_rule") for x in scenes):
                stats.rule_scenes_chapters += 1
            results = []
            for bt in batches(scenes, segs):
                results += run(bt)
            nf = sum(len(ix) for ix, r, e in results if r is None)
            if nf: degraded.append(f"{nf} 段规则分镜")
        except Exception as ex:   # 单章失败不拖垮整本书
            stats.warnings.append(f"[ch{k + 1}] 规划异常，整章降级为规则分镜：{type(ex).__name__}: {str(ex)[:120]}")
            scenes = rule_scenes(a, b, c["title"]); results = [(list(range(a, b + 1)), None, ["exception"])]
            stats.rule_scenes_chapters += 1; degraded.append("整章降级")
        with lock:
            done[0] += 1
            progress("chapter+", f"{label} 完成 · {len(scenes)} 场景（{done[0]}/{N}）" + (f" · {'；'.join(degraded)}" if degraded else ""))
        return scenes, results

    progress("chapters", f"{N} 章并发规划")
    all_scenes, all_results = [], []
    with cf.ThreadPoolExecutor(max_workers=min(N, max(2, conc * 2))) as ex:
        for scenes, results in ex.map(do_chapter, range(N)):
            all_scenes += scenes; all_results += results
    for x in all_scenes:
        x.pop("_rule", None)
    o["scenes"] = all_scenes
    o["chapters"] = [{"start": c["start"], "end": c["end"], "title": c["title"]} for c in chs]
    return assemble(o, all_results, segs, stats), stats


def outline_semantic_cast(o):
    e = []
    ids = [c["id"] for c in o["characters"]]
    if len(set(ids)) != len(ids):
        e.append("characters.id 有重复")
    for x in o["focal_pair"]:
        if x not in ids:
            e.append(f"focal_pair 中的 {x} 不在 characters 里")
    return e


# ------------------------------------------------------------------ rule-based defaults
SAY = "说|问|道|答|喊|叫|嘀咕|低语|回答|补充|笑道|反问"
BLUSH = re.compile(r"脸红|红了脸|脸.{0,2}发烫|涨红|耳根|满脸通红|脸颊.{0,3}红")


def rule_paras(o, segs, idxs, stats):
    """内容过滤 / 规划失败时的默认分镜：只用能从文字直接看出的信息，不猜情绪。"""
    chars = o["characters"]
    names = {c["id"]: [x for x in [c.get("name")] + list(c.get("aliases") or []) if isinstance(x, str) and len(x) >= 1] for c in chars}
    scene_of = {}
    for s in o["scenes"]:
        for i in range(s["start"], s["end"] + 1):
            scene_of[i] = (s["start"], s["end"])
    present_cache = {}
    out = {}
    for i in idxs:
        t = segs[i]["text"]
        rng = scene_of.get(i, (i, i))
        if rng not in present_cache:
            blob = "".join(segs[j]["text"] for j in range(rng[0], rng[1] + 1))
            present_cache[rng] = [cid for cid, ns in names.items() if any(len(x) >= 2 and x in blob for x in ns)]
        sp = ""
        if len(t) <= 160:
            for cid, ns in names.items():
                if any(len(x) >= 2 and re.search(re.escape(x) + r"[^，。！？”]{0,4}(?:" + SAY + r")", t) for x in ns):
                    sp = cid; break
        bl = 0.6 if BLUSH.search(t) else 0.0
        bw = ""
        if bl:
            bw = sp or next((cid for cid, ns in names.items() if any(len(x) >= 2 and x in t for x in ns)), "")
        out[i] = {"sp": sp, "pr": present_cache[rng], "in": 0.3, "te": 0.2, "bl": bl, "bw": bw}
        stats.rule_paras += 1
    return out


def assemble(o, results, segs, stats):
    n = len(segs); ids = {c["id"] for c in o["characters"]}
    paras = [None] * n; utt, vis, quotes = [], [], []
    for idxs, r, e in results:
        r = r if isinstance(r, dict) else {}
        for p in r.get("paras", []):
            try:
                i = int(p[0])
                if i in idxs:
                    paras[i] = {"sp": p[1] if p[1] in ids else "", "pr": [x for x in p[2] if x in ids],
                                "in": float(p[3]), "te": float(p[4]), "bl": float(p[5]),
                                "bw": p[6] if p[6] in ids else ""}
            except Exception:
                pass
        def relocate(at, txt):
            if at in idxs and txt in segs[at]["text"]:
                return at
            hit = [i for i in idxs if txt in segs[i]["text"]]
            return hit[0] if len(hit) == 1 else None
        for u in r.get("utterances", []):
            if isinstance(u, dict) and isinstance(u.get("q"), str) and len(u["q"]) >= 2 and u.get("sp") in ids:
                at = relocate(u.get("i"), u["q"])
                if at is not None:
                    utt.append(dict(u, i=at))
        for v in r.get("visuals", []):
            if isinstance(v, dict) and v.get("at") in idxs and v.get("type") in COMPONENTS:
                spec = {"type": "object", "properties": COMPONENTS[v["type"]]["params"]}
                if not isinstance(v.get("params"), dict):
                    v["params"] = {}
                bad = {e.absolute_path[0] for e in jsonschema.Draft202012Validator(spec).iter_errors(v["params"]) if e.absolute_path}
                for k in bad:  # 去掉不合法的参数键，让组件用默认值，而不是整个丢掉
                    v["params"].pop(k, None)
                if bad:
                    stats.warnings.append(f"组件 {v['type']}@{v['at']} 参数 {sorted(bad)} 不合法，已改用默认值")
                if not list(jsonschema.Draft202012Validator(spec).iter_errors(v.get("params", {}))):
                    vis.append(v)
                else:
                    stats.warnings.append(f"丢弃参数不合法的组件 {v['type']}@{v.get('at')}")
        for q in r.get("quotes", []):
            if isinstance(q, dict) and isinstance(q.get("text"), str) and len(q["text"]) >= 6:
                at = relocate(q.get("at"), q["text"])
                if at is not None:
                    quotes.append(dict(q, at=at))
                else:
                    stats.warnings.append(f"丢弃非原文子串的摘句：{q['text'][:16]}")
    # 缺分镜的段落：规则化默认值（不复制上一段——上一段的说话人/脸红会被错误地带过来）
    miss = [i for i in range(n) if paras[i] is None]
    if miss:
        fill = rule_paras(o, segs, miss, stats)
        for i in miss:
            paras[i] = fill[i]
        stats.warnings.append(f"{len(miss)} 段缺少分镜，已用规则化默认分镜：" +
                              ", ".join(map(str, miss[:12])) + ("…" if len(miss) > 12 else ""))
    o.update({"paras": paras, "utterances": utt, "visuals": vis, "quotes": quotes})
    return o


# ------------------------------------------------------------------ external plan (--plan)
def llm_available(model_spec=None):
    """是否能调用 LLM：环境变量里有该 provider 的 key，或运行在凭证自动注入的网关环境（VIBEREADING_GATEWAY=1 或列出 provider）。"""
    provider, _ = resolve_model(model_spec)
    gw = os.environ.get("VIBEREADING_GATEWAY", "")   # "1" = 所有 provider 都由网关注入凭证；也可写 "mercury" / "mercury,qwen"
    return bool(os.environ.get(PROVIDERS[provider]["key_env"]) or gw == "1" or provider in [x.strip() for x in gw.split(",")])


def validate_plan(plan, seg):
    """校验并修复外部（人工 / 其他程序 / 其他 agent）写的分镜 JSON。
    接受两种 paras 写法：对象 {"sp","pr","in","te","bl","bw"} 或紧凑数组 [i, sp, pr, in, te, bl, bw]。
    返回 (plan, warnings)。结构性致命错误（不是对象、没有 scenes）抛 ValueError。"""
    if not isinstance(plan, dict):
        raise ValueError("plan 必须是 JSON 对象")
    if not isinstance(plan.get("scenes"), list) or not plan["scenes"]:
        raise ValueError("plan 缺少 scenes（场景列表）")
    segs = seg["segments"]; n = len(segs)
    st = Stats("external", "plan.json")
    plan = normalize(plan)
    o = repair_outline({k: plan.get(k) for k in ("meta", "characters", "focal_pair", "scenes") if k in plan}, segs)
    if not o["characters"]:
        o["characters"] = [{"id": "narrator", "name": "叙述者", "color": "#2b4a6f"}]
        st.warnings.append("plan 没有 characters，补了一个占位人物")
    paras = plan.get("paras") or []
    arr = []
    for k, p in enumerate(paras):
        if isinstance(p, dict):
            p = [p.get("i", k), p.get("sp", ""), p.get("pr", []), p.get("in", 0.2), p.get("te", 0.1), p.get("bl", 0), p.get("bw", "")]
        if isinstance(p, list) and len(p) == 7:
            p = normalize({"paras": [list(p)]})["paras"][0]
            try:
                p[3:6] = [min(1.0, max(0.0, float(x))) for x in p[3:6]]
            except (TypeError, ValueError):
                p[3:6] = [0.2, 0.1, 0.0]
            arr.append(p)
    if len(arr) != n:
        st.warnings.append(f"plan.paras 有 {len(arr)} 条，原文 {n} 段：缺的用规则化默认分镜，多的忽略")
    r = {"paras": arr, "utterances": plan.get("utterances", []), "visuals": plan.get("visuals", []), "quotes": plan.get("quotes", [])}
    out = assemble(o, [(list(range(n)), r, [])], segs, st)
    chs = [c for c in plan.get("chapters") or [] if isinstance(c, dict) and isinstance(c.get("start"), int)
           and isinstance(c.get("end"), int) and 0 <= c["start"] <= c["end"] < n]
    if chs:
        out["chapters"] = [{"start": c["start"], "end": c["end"], "title": str(c.get("title") or "")[:40]}
                           for c in sorted(chs, key=lambda c: c["start"])]
    return out, st.warnings
