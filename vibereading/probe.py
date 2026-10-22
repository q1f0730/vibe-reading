"""能力探针：保存模型配置时跑一组小探测（约 11 次调用），得到能力档案并分档（A/B/C）。

探测项：连通 + 延迟、JSON 模式、结构化输出（json_schema / Anthropic tool / Gemini responseJsonSchema）、纯提示词 JSON、
工具调用、最大输出（参数接受度 + 一次真实长结构化输出）、上下文（大海捞针）、是否推理模型、中文长文本处理
（《孔乙己》逐段 JSON + 逐字摘句）、内容过滤倾向（公有领域、无害但含情感/冲突的文字：《祝福》《孔乙己》节选）。
所有报错都经 redact()，档案里不含密钥。
"""
import concurrent.futures as cf, json, pathlib, re, statistics, time

from . import llm

ROOT = pathlib.Path(__file__).resolve().parent.parent
KONG = ROOT / "examples" / "kongyiji" / "kongyiji.txt"

# 公有领域（鲁迅，1936 年逝世；作品 1919/1924 年发表）、无害但含情感与冲突的节选，用来看提供方是否误伤文学文本
FILTER_TEXTS = [
    "祥林嫂可是异乎寻常，他们说她一路只是嚎，骂，抬到贺家坳，喉咙已经全哑了。拉出轿来，两个男人和她的小叔子使劲的捺住她也还拜不成天地。"
    "他们一不小心，一松手，阿呀，阿弥陀佛，她就一头撞在香案角上，头上碰了一个大窟窿，鲜血直流，用了两把香灰，包上两块红布还止不住血呢。",
    "“他总仍旧是偷。这一回，是自己发昏，竟偷到丁举人家里去了。他家的东西，偷得的么？”“后来怎么样？”“怎么样？先写服辩，后来是打，"
    "打了大半夜，再打折了腿。”“后来呢？”“后来打折了腿了。”“打折了怎样呢？”“怎样？……谁晓得？许是死了。”",
]


class _S:
    def __init__(self):
        self.calls = 0; self.transient = 0; self.tokens_in = 0; self.tokens_out = 0; self.reasoning_tokens = 0
        self.content_filtered = 0; self.warnings = []; self.log = []


def _paras():
    try:
        t = KONG.read_text(encoding="utf-8")
    except OSError:
        t = "\n".join(FILTER_TEXTS)
    return [x.strip() for x in t.splitlines() if x.strip()]


def _parse(text):
    from .planner import parse_json
    return parse_json(text or "")


def run(client, progress=lambda *a: None, quick=False):
    """→ 能力档案 dict（可直接 JSON 存库）。quick=True：只跑连通性（设置页的「测试」按钮）。"""
    S = _S(); t0 = time.time()
    prof = {"version": 1, "at": round(time.time()), "kind": client.cfg.kind, "model": client.cfg.model, "base_url": client.cfg.base_url,
            "errors": [], "probes": {}}

    def rec(name, fn):
        t = time.time()
        try:
            v = fn()
            prof["probes"][name] = {"ok": bool(v.get("ok")) if isinstance(v, dict) else bool(v), "sec": round(time.time() - t, 2),
                                    **(v if isinstance(v, dict) else {})}
        except llm.ContentFiltered as e:
            prof["probes"][name] = {"ok": False, "filtered": True, "sec": round(time.time() - t, 2), "error": client.redact(str(e))[:160]}
        except Exception as e:
            msg = client.redact(f"{type(e).__name__}: {e}")[:240]
            prof["probes"][name] = {"ok": False, "sec": round(time.time() - t, 2), "error": msg}
            prof["errors"].append(f"{name}: {msg}")
        progress(name, prof["probes"][name])
        return prof["probes"][name]

    def ping():
        lat = []; reason = False; text = ""
        for _ in range(1 if quick else 2):
            r = client.complete([{"role": "user", "content": "只回复两个字母：OK"}], 64, json_mode=False, stats=S)
            lat.append(r.seconds); reason = reason or r.reasoning_tokens > 0 or r.raw_reasoning; text = r.text
        return {"ok": bool(text.strip()), "latency": round(statistics.median(lat), 2), "reasoning": reason, "reply": text.strip()[:20]}

    p = rec("ping", ping)
    prof["ok"] = p["ok"]; prof["latency"] = p.get("latency")
    if quick or not p["ok"]:
        prof.update(tier="C" if not p["ok"] else None, seconds=round(time.time() - t0, 1), calls=S.calls)
        if not p["ok"]:
            prof["tier_reasons"] = ["连不上模型：" + (p.get("error") or "无回复")]
        return prof

    want = {"n": 3, "words": ["门", "窗", "灯"]}
    schema = {"type": "object", "required": ["n", "words"], "properties": {"n": {"type": "integer"}, "words": {"type": "array", "items": {"type": "string"}}}}
    ask = [{"role": "system", "content": "你只输出 JSON。"}, {"role": "user", "content": '输出 JSON：键 n 为整数 3，键 words 为数组 ["门","窗","灯"]。'}]

    def _jcheck(r):
        try:
            o = _parse(r.text)
        except Exception:
            return False
        return isinstance(o, dict) and o.get("n") == 3 and o.get("words") == want["words"]

    def json_mode():
        c = llm.Client(client.cfg)   # 独立实例：不让 400 自适应影响其它探针的判定
        c.no_schema = True
        r = c.complete(ask, 200, json_mode=True, stats=S)
        return {"ok": _jcheck(r) and not c.no_json_mode, "native": not c.no_json_mode}

    def json_schema():
        c = llm.Client(client.cfg)
        r = c.complete(ask, 200, json_mode=True, schema=schema, stats=S)
        return {"ok": _jcheck(r) and not c.no_schema, "via_tool": r.tool_used}

    def json_prompt():
        c = llm.Client(client.cfg); c.no_schema = True; c.no_json_mode = True
        r = c.complete(ask, 200, json_mode=False, stats=S)
        return {"ok": _jcheck(r)}

    def tools():
        return _tool_probe(client, S)

    def max_out():
        c = llm.Client(client.cfg); c.no_schema = True
        try:
            c.complete([{"role": "user", "content": "只回复：OK"}], 32000, json_mode=False, stats=S)
            accepted = min(32000, c.max_cap) if c.max_cap else 32000
        except llm.LLMError:
            accepted = c.max_cap or 4096
        n = 160
        r = client.complete([{"role": "system", "content": "你只输出 JSON。"},
                             {"role": "user", "content": f'输出 JSON {{"items":[...]}}，items 恰好 {n} 个元素，第 k 个是 {{"i":k,"w":"一个四字中文词语"}}，k 从 0 到 {n - 1}，不要省略。'}],
                            6000, json_mode=True, stats=S)
        try:
            items = _parse(r.text).get("items") or []
        except Exception:
            items = []
        complete = r.finish != "length" and len(items) >= n * 0.95
        return {"ok": complete, "accepted": accepted, "produced_tokens": r.tokens_out, "items": len(items),
                "max_output_ok": accepted if complete else max(256, int(r.tokens_out * 0.9))}

    def context():
        paras = _paras(); base = "\n".join(paras)
        for reps in (8, 2):
            blocks = [base] * reps
            blocks.insert(reps // 2, "【备注】本文的暗号是“青鱼七号”。")
            doc = "\n\n".join(blocks)
            try:
                r = client.complete([{"role": "user", "content": doc + "\n\n问题：上文【备注】里的暗号是什么？只回答暗号本身。"}], 64, json_mode=False, stats=S, timeout=120)
            except llm.LLMError as e:
                if e.code in (400, 413, 422):
                    continue
                raise
            if "青鱼七号" in r.text:
                return {"ok": True, "chars": len(doc), "context_ok": int(len(doc) * 1.3)}
        return {"ok": False, "context_ok": 2000}

    def zh_long():
        paras = _paras()[:14]
        body = "\n".join(f"[{i}] {t}" for i, t in enumerate(paras))
        r = client.complete([{"role": "system", "content": "你是小说分镜导演，只输出 JSON，不复述正文。"},
                             {"role": "user", "content": body + f'\n\n输出 JSON：{{"paras":[[段号, 说话人姓名或"", 紧张度0到1], …]（恰好 {len(paras)} 行，段号 0–{len(paras) - 1}）, '
                                                         '"quote":"从原文逐字复制一句 8–30 字的话"}'}],
                            2500, json_mode=True, stats=S)
        try:
            o = _parse(r.text)
        except Exception:
            return {"ok": False, "score": 0.0}
        rows = [x for x in (o.get("paras") or []) if isinstance(x, list) and len(x) >= 3]
        idx = {x[0] for x in rows if isinstance(x[0], int)}
        cover = len(idx & set(range(len(paras)))) / len(paras)
        nums_ok = all(isinstance(x[2], (int, float)) and 0 <= x[2] <= 1 for x in rows) if rows else False
        q = o.get("quote") or ""
        verb = isinstance(q, str) and len(q) >= 6 and q in "".join(paras)
        score = round(0.2 + 0.5 * cover + 0.15 * nums_ok + 0.15 * verb, 3)
        return {"ok": score >= 0.9, "score": score, "coverage": round(cover, 3), "verbatim_quote": verb}

    def filt():
        blocked = 0
        for t in FILTER_TEXTS:
            try:
                r = client.complete([{"role": "system", "content": "你是文学编辑，只输出 JSON。"},
                                     {"role": "user", "content": "下面是公有领域的经典小说节选。评估这段的情绪张力，输出 JSON {\"tension\":0到1,\"emotion\":\"两字\"}。\n\n" + t}],
                                    300, json_mode=True, stats=S)
                if llm.looks_refusal(r.text):
                    blocked += 1
            except llm.ContentFiltered:
                blocked += 1
        return {"ok": blocked == 0, "blocked": blocked, "of": len(FILTER_TEXTS)}

    jobs = [("json_mode", json_mode), ("json_schema", json_schema), ("json_prompt", json_prompt), ("tools", tools),
            ("max_output", max_out), ("context", context), ("zh_long", zh_long), ("filter", filt)]
    with cf.ThreadPoolExecutor(max_workers=min(3, client.cfg.concurrency)) as ex:
        list(ex.map(lambda j: rec(*j), jobs))
    P = prof["probes"]
    prof.update(
        json_mode=bool(P["json_mode"].get("ok")), json_schema=bool(P["json_schema"].get("ok")), json_prompt=bool(P["json_prompt"].get("ok")),
        tools=bool(P["tools"].get("ok")), max_output_ok=int(P["max_output"].get("max_output_ok") or 0),
        context_ok=int(P["context"].get("context_ok") or 0), zh_long=float(P["zh_long"].get("score") or 0.0),
        filter_blocked=int(P["filter"].get("blocked", 0) if "blocked" in P["filter"] else (len(FILTER_TEXTS) if P["filter"].get("filtered") else 0)),
        reasoning=bool(P["ping"].get("reasoning")) or S.reasoning_tokens > 0,
        reasoning_tokens=S.reasoning_tokens, adaptations=list(client.events),
    )
    tier, why = classify(prof)
    prof.update(tier=tier, tier_reasons=why, code_mode_ok=tier == "A" and prof["max_output_ok"] >= 12000,
                seconds=round(time.time() - t0, 1), calls=S.calls, tokens_out=S.tokens_out)
    return prof


def _tool_probe(client, S):
    """工具调用：各家原生格式各发一次，看是否按要求调用。"""
    k = client.cfg.kind
    c = llm.Client(client.cfg)
    fn = {"name": "record_tension", "description": "记录一段文字的紧张度", "parameters": {"type": "object", "required": ["level"],
          "properties": {"level": {"type": "number"}}}}
    msgs = [{"role": "user", "content": "“打折了腿了。”——请调用 record_tension 记录这句话的紧张度（0 到 1）。"}]
    if k == "openai":
        url, body, h = c._openai(msgs, 200, False, None, False)
        body["tools"] = [{"type": "function", "function": fn}]; body["tool_choice"] = "auto"
    elif k == "anthropic":
        url, body, h = c._anthropic(msgs, 200, False, None, False)
        body["tools"] = [{"name": fn["name"], "description": fn["description"], "input_schema": fn["parameters"]}]
        body["thinking"] = {"type": "disabled"}
    else:
        url, body, h = c._gemini(msgs, 200, False, None, False)
        body["tools"] = [{"functionDeclarations": [fn]}]
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), headers=h)
    S.calls += 1
    try:
        with urllib.request.urlopen(req, timeout=c.cfg.timeout) as r:
            d = json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": c.redact(f"{type(e).__name__}: {getattr(e, 'code', '')}")[:120]}
    if k == "openai":
        tc = ((d.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
        ok = bool(tc) and tc[0].get("function", {}).get("name") == "record_tension"
    elif k == "anthropic":
        ok = any(b.get("type") == "tool_use" and b.get("name") == "record_tension" for b in d.get("content") or [])
    else:
        parts = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        ok = any((p.get("functionCall") or {}).get("name") == "record_tension" for p in parts)
    return {"ok": ok}


def classify(p):
    """档位规则（写进 README）：
    C：连不上；或三种 JSON 方式都不行；或中文长文本得分 < 0.6；或可靠输出 < 2000 token；或上下文 < 8k。
    A：有原生结构化输出（schema 或 JSON 模式）且中文长文本 ≥ 0.9、可靠输出 ≥ 6000、上下文 ≥ 16k、内容过滤 0 误伤。
    其余 B。"""
    why = []
    if not p.get("ok"):
        return "C", ["连不上模型"]
    native = p.get("json_schema") or p.get("json_mode")
    anyjson = native or p.get("json_prompt")
    zh = p.get("zh_long", 0); mo = p.get("max_output_ok", 0); ctx = p.get("context_ok", 0); fb = p.get("filter_blocked", 0)
    c_why = []
    if not anyjson: c_why.append("JSON 模式 / 结构化输出 / 提示词 JSON 都不合格")
    if zh < 0.6: c_why.append(f"中文长文本处理得分 {zh:.2f} < 0.6")
    if mo and mo < 2000: c_why.append(f"可靠输出 {mo} token < 2000")
    if ctx and ctx < 8000: c_why.append(f"上下文 < 8k（实测 {ctx}）")
    if c_why:
        return "C", c_why
    a_fail = []
    if not native: a_fail.append("没有原生 JSON 模式 / 结构化输出")
    if zh < 0.9: a_fail.append(f"中文长文本 {zh:.2f} < 0.9")
    if mo < 6000: a_fail.append(f"可靠输出 {mo} < 6000")
    if ctx < 16000: a_fail.append(f"上下文 {ctx} < 16k")
    if fb: a_fail.append(f"内容过滤误伤 {fb} 次")
    if not a_fail:
        return "A", ["全部 A 档条件满足"]
    return "B", a_fail
