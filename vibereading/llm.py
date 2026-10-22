"""模型无关的 Provider 层：OpenAI 兼容（含 Mercury / 千问 / DeepSeek / Gemini 兼容端点…）、Anthropic 原生、Gemini 原生。

一个 ModelConfig = (kind, base_url, model, api_key, 选项)；Client.complete() 统一返回 Result(text, finish, usage)。
在这里统一处理：
  - 超时（单请求 timeout；网关有总时长上限时 max_total_seconds 截断）、429/5xx/网络错误指数退避（尊重 Retry-After）、
    每个配置一个并发闸（cfg.concurrency）；
  - 流式 / 非流式（SSE；流式时 timeout 是“空闲超时”，再加总时长截止）；
  - 结构化输出：json_schema（OpenAI response_format / Anthropic 强制 tool / Gemini responseJsonSchema）、
    json_object、纯提示词三档；提供方拒绝参数（400）时自动降一档并记住；
  - 推理 token 预算：推理吃光输出（finish=length 且正文空 / 推理占大头）时自动降档 → 关推理，并记住；
  - 内容过滤：各家的拒答信号统一成 ContentFiltered；
  - 密钥：只在请求头里出现；repr / 报错 / 日志一律 redact()。
"""
import json, os, re, threading, time, urllib.request, urllib.error
from dataclasses import dataclass, field, asdict

KINDS = ("openai", "anthropic", "gemini")
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
MAX_TRANSIENT = int(os.environ.get("VR_LLM_RETRIES", 8))
REASON_LADDER = ["high", "medium", "low", "minimal", "off"]
DEFAULT_BASE = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com",
                "gemini": "https://generativelanguage.googleapis.com/v1beta"}


# ------------------------------------------------------------------ errors & redaction
class LLMError(Exception):
    def __init__(self, msg, code=None, body="", retry_after=None):
        super().__init__(msg); self.code = code; self.body = body; self.retry_after = retry_after


class ContentFiltered(LLMError):
    """提供方内容过滤拒答（同样内容重试没有意义）。"""


_SECRET_RX = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]{6,}"),
    re.compile(r'(?i)((?:api[_-]?key|x-api-key|x-goog-api-key|authorization|access[_-]?token|secret)["\']?\s*[:=]\s*["\']?)[^"\'\s,&}]{6,}'),
    re.compile(r"\b(?:sk|ak|pk|rk)-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
]


def redact(s, *secrets):
    """去掉字符串里的任何密钥：已知密钥逐字替换 + 常见密钥形态正则。"""
    s = "" if s is None else str(s)
    for k in secrets:
        if k and len(k) >= 6:
            s = s.replace(k, "***")
    for rx in _SECRET_RX:
        s = rx.sub(lambda m: (m.group(1) if m.groups() and m.group(1) else "") + "***", s)
    return s


def mask_key(k):
    if not k:
        return ""
    return ("…" + k[-4:]) if len(k) >= 12 else "***"


# ------------------------------------------------------------------ config
@dataclass
class ModelConfig:
    kind: str = "openai"
    base_url: str = ""
    model: str = ""
    api_key: str = field(default=None, repr=False)      # 永远不进 repr / to_public
    label: str = ""
    timeout: float = 90.0              # 单请求（流式时为空闲）超时
    max_total_seconds: float = 0.0     # 网关总时长上限（如千问网关 120s）；0 = 不限
    concurrency: int = 3
    stream: bool = False
    reasoning: str = "auto"            # auto | off | minimal | low | medium | high | instant(mercury)
    temperature: float = None
    max_output: int = 0                # 已知最大输出（探针写入）；0 = 未知
    extra: dict = field(default_factory=dict)       # 透传给请求体的额外字段
    simulate: dict = field(default_factory=dict)    # 仅评测：{"no_json_mode":1,"no_schema":1,"max_output":1200,"strip_reasoning_param":1}

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"未知 provider 类型：{self.kind}（可用 {', '.join(KINDS)}）")
        self.base_url = (self.base_url or DEFAULT_BASE[self.kind]).strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+", self.base_url):
            raise ValueError("base_url 必须是 http(s):// 开头的地址")
        self.model = (self.model or "").strip()
        if not self.model:
            raise ValueError("需要填写模型名")
        self.concurrency = max(1, min(16, int(self.concurrency or 3)))
        self.timeout = max(5.0, min(600.0, float(self.timeout or 90)))

    def to_public(self):
        d = asdict(self); d.pop("api_key", None)
        d["key"] = mask_key(self.api_key)
        return d

    @classmethod
    def from_dict(cls, d, api_key=None):
        allowed = {k for k in cls.__dataclass_fields__}
        kw = {k: v for k, v in (d or {}).items() if k in allowed and k != "api_key"}
        return cls(api_key=api_key, **kw)


def dialect(cfg):
    """同为 OpenAI 兼容，不同家的推理开关 / max_tokens 字段名不一样。"""
    u = cfg.base_url.lower(); m = cfg.model.lower()
    if "inceptionlabs" in u or m.startswith("mercury"):
        return "mercury"
    if any(x in u for x in ("qianwen", "dashscope", "aliyuncs")) or m.startswith("qwen"):
        return "qwen"
    if "deepseek" in u or m.startswith("deepseek"):
        return "deepseek"
    if "api.openai.com" in u or re.match(r"^(o[1-9]|gpt-5)", m):
        return "openai-reasoning" if re.match(r"^(o[1-9]|gpt-5)", m) else "openai"
    return "generic"


@dataclass
class Result:
    text: str
    finish: str
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens: int = 0
    seconds: float = 0.0
    tool_used: bool = False
    raw_reasoning: bool = False      # 响应里出现了 reasoning/thinking 内容


def _sleep_for(attempt, retry_after=None):
    try:
        if retry_after:
            return min(30.0, float(retry_after))
    except (TypeError, ValueError):
        pass
    return min(30.0, 2.0 * (2 ** attempt)) * (0.75 + 0.5 * ((time.time() * 1000) % 100) / 100)


def _bump(stats, attr, n=1):
    if stats is not None and hasattr(stats, attr):
        setattr(stats, attr, getattr(stats, attr) + n)


def _log(stats, entry):
    if stats is not None and hasattr(stats, "log"):
        stats.log.append(entry)


_FILTER_RX = re.compile(r"content[_ ]?filter|data_inspection_failed|inappropriate[_ ]content|content_policy|"
                        r"safety|prohibited_content|blocked by|DataInspectionFailed|sensitive", re.I)
_REFUSAL_RX = re.compile(r"^\s*(?:抱歉|对不起|很抱歉|我无法|我不能|I(?:'m| am) sorry|I can(?:'|no)t (?:help|assist|comply))", re.I)


def looks_refusal(text):
    return bool(text) and len(text) < 400 and bool(_REFUSAL_RX.search(text)) and "{" not in text


# ------------------------------------------------------------------ client
class Client:
    """一个模型配置的调用端。线程安全；降级状态（关推理、去 schema…）在实例内记住。"""

    def __init__(self, cfg: ModelConfig, sem=None):
        self.cfg = cfg
        self.dialect = dialect(cfg) if cfg.kind == "openai" else cfg.kind
        self._sem = sem or threading.BoundedSemaphore(cfg.concurrency)
        self._lock = threading.Lock()
        self.reason_level = self._initial_reason()
        self.no_schema = bool(cfg.simulate.get("no_schema"))
        self.no_json_mode = bool(cfg.simulate.get("no_json_mode"))
        self.no_reason_param = bool(cfg.simulate.get("strip_reasoning_param"))
        self.max_cap = int(cfg.simulate.get("max_output") or 0) or int(cfg.max_output or 0) or 0
        self.events = []       # 降级事件（进质量报告）

    def __repr__(self):
        return f"Client({self.cfg.kind}:{self.cfg.model}@{self.cfg.base_url})"

    @property
    def label(self):
        return self.cfg.label or f"{self.cfg.kind}:{self.cfg.model}"

    def _initial_reason(self):
        r = (self.cfg.reasoning or "auto").lower()
        if r != "auto":
            return r
        return {"mercury": "instant", "qwen": "off", "openai-reasoning": "low", "anthropic": "off"}.get(self.dialect, "auto")

    def _note(self, stats, msg):
        with self._lock:
            if msg not in self.events:
                self.events.append(msg)
        if stats is not None and hasattr(stats, "warnings") and msg not in stats.warnings:
            stats.warnings.append(msg)

    def redact(self, s):
        return redact(s, self.cfg.api_key)

    # ---------------- reasoning headroom / downgrade
    def headroom(self):
        """推理模型的推理 token 也算进输出上限 → 给正文之外留余量。"""
        lv = self.reason_level
        if lv in ("off", "instant", "minimal"):
            return 0 if lv != "minimal" else 1024
        if lv == "auto":
            return 0
        return {"low": 4000, "medium": 8000, "high": 12000}.get(lv, 4000)

    def downgrade_reasoning(self, stats=None):
        with self._lock:
            lv = self.reason_level
            if self.dialect == "mercury":
                nxt = "instant" if lv != "instant" else None
            elif lv in ("auto",):
                nxt = "low" if self.dialect in ("openai-reasoning", "generic", "openai", "deepseek") else "off"
                if self.dialect == "deepseek":
                    nxt = None
            elif lv in REASON_LADDER:
                i = REASON_LADDER.index(lv)
                nxt = REASON_LADDER[i + 2] if i + 2 < len(REASON_LADDER) else ("off" if lv != "off" else None)
            else:
                nxt = "off" if lv != "off" else None
            if nxt is None or nxt == lv:
                return False
            self.reason_level = nxt
        self._note(stats, f"[{self.label}] 推理占满输出上限，推理强度 {lv} → {nxt}")
        return True

    # ---------------- request builders
    def _openai(self, messages, max_tokens, json_mode, schema, stream):
        u = self.cfg.base_url
        url = u if u.endswith("/chat/completions") else u + "/chat/completions"
        mk = "max_completion_tokens" if self.dialect in ("mercury", "openai-reasoning") else "max_tokens"
        body = {"model": self.cfg.model, "messages": messages, mk: max_tokens}
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        elif self.dialect == "mercury":
            body["temperature"] = 0.5
        elif self.dialect == "qwen":
            body["temperature"] = 0.4
        if schema is not None and not self.no_schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": False}}
        elif json_mode and not self.no_json_mode:
            body["response_format"] = {"type": "json_object"}
        lv = self.reason_level
        if not self.no_reason_param:
            if self.dialect == "mercury" and lv in ("instant", "low", "medium", "high"):
                body["reasoning_effort"] = lv
            elif self.dialect == "qwen" and lv != "auto":
                body["enable_thinking"] = lv not in ("off", "minimal")
                if lv in ("low", "medium", "high"):
                    body["thinking_budget"] = {"low": 2000, "medium": 6000, "high": 12000}[lv]
            elif self.dialect in ("openai-reasoning", "generic", "openai") and lv in ("minimal", "low", "medium", "high"):
                body["reasoning_effort"] = lv
            elif self.dialect == "openai-reasoning" and lv == "off":
                body["reasoning_effort"] = "minimal"
        if stream:
            body["stream"] = True; body["stream_options"] = {"include_usage": True}
        body.update(self.cfg.extra or {})
        h = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = "Bearer " + self.cfg.api_key
        return url, body, h

    def _anthropic(self, messages, max_tokens, json_mode, schema, stream):
        u = self.cfg.base_url
        url = u if u.endswith("/messages") else (u + "/messages" if u.endswith("/v1") else u + "/v1/messages")
        sysmsg = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        # Anthropic 要求 user/assistant 交替：合并相邻同角色
        merged = []
        for m in msgs:
            if merged and merged[-1]["role"] == m["role"]:
                merged[-1]["content"] += "\n\n" + m["content"]
            else:
                merged.append(dict(m))
        if json_mode and schema is None:
            sysmsg += "\n\n只输出一个 JSON 对象，不要任何解释或代码块标记。"
        body = {"model": self.cfg.model, "max_tokens": max_tokens, "messages": merged}
        if sysmsg:
            body["system"] = sysmsg
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        use_tool = schema is not None and not self.no_schema
        lv = self.reason_level
        if use_tool:
            body["tools"] = [{"name": "emit", "description": "输出结果", "input_schema": schema}]
            body["tool_choice"] = {"type": "tool", "name": "emit"}
            if not self.no_reason_param:
                body["thinking"] = {"type": "disabled"}     # 强制 tool 与 thinking 不兼容
        elif not self.no_reason_param:
            if lv in ("low", "medium", "high"):
                b = {"low": 1024, "medium": 4096, "high": 12000}[lv]
                body["thinking"] = {"type": "enabled", "budget_tokens": b}; body["max_tokens"] = max_tokens + b
                body.pop("temperature", None)
            elif lv in ("off", "minimal"):
                body["thinking"] = {"type": "disabled"}
        if stream:
            body["stream"] = True
        body.update(self.cfg.extra or {})
        h = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.cfg.api_key:
            h["x-api-key"] = self.cfg.api_key
        return url, body, h

    def _gemini(self, messages, max_tokens, json_mode, schema, stream):
        u = self.cfg.base_url
        m = self.cfg.model if self.cfg.model.startswith("models/") else "models/" + self.cfg.model
        url = f"{u}/{m}:" + ("streamGenerateContent?alt=sse" if stream else "generateContent")
        sysmsg = "\n\n".join(x["content"] for x in messages if x["role"] == "system")
        contents = [{"role": "model" if x["role"] == "assistant" else "user", "parts": [{"text": x["content"]}]}
                    for x in messages if x["role"] in ("user", "assistant")]
        gc = {"maxOutputTokens": max_tokens}
        if self.cfg.temperature is not None:
            gc["temperature"] = self.cfg.temperature
        if (json_mode or schema is not None) and not self.no_json_mode:
            gc["responseMimeType"] = "application/json"
            if schema is not None and not self.no_schema:
                gc["responseJsonSchema"] = schema
        lv = self.reason_level
        if not self.no_reason_param and lv != "auto":
            gc["thinkingConfig"] = {"thinkingBudget": {"off": 0, "minimal": 128, "low": 1024, "medium": 4096, "high": 12000}.get(lv, 0)}
        body = {"contents": contents, "generationConfig": gc}
        if sysmsg:
            body["systemInstruction"] = {"parts": [{"text": sysmsg}]}
        body.update(self.cfg.extra or {})
        h = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            h["x-goog-api-key"] = self.cfg.api_key
        return url, body, h

    # ---------------- response parsers
    def _parse(self, d):
        k = self.cfg.kind
        if k == "openai":
            if "choices" not in d:
                raise LLMError("响应里没有 choices：" + self.redact(json.dumps(d, ensure_ascii=False)[:200]))
            ch = d["choices"][0]; msg = ch.get("message") or {}
            u = d.get("usage") or {}
            rt = ((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            text = msg.get("content") or ""
            tool = False
            if not text and msg.get("tool_calls"):
                text = msg["tool_calls"][0].get("function", {}).get("arguments", ""); tool = True
            fin = ch.get("finish_reason") or "stop"
            if fin == "content_filter":
                raise ContentFiltered("finish_reason=content_filter")
            return Result(text, "length" if fin == "length" else ("tool" if fin == "tool_calls" else fin),
                          u.get("prompt_tokens", 0) or 0, u.get("completion_tokens", 0) or 0, rt, tool_used=tool,
                          raw_reasoning=bool(msg.get("reasoning_content") or msg.get("reasoning")))
        if k == "anthropic":
            if d.get("type") == "error":
                raise LLMError(self.redact(json.dumps(d.get("error"), ensure_ascii=False)[:300]))
            text, tool, think = "", False, ""
            for b in d.get("content") or []:
                if b.get("type") == "text":
                    text += b.get("text", "")
                elif b.get("type") == "tool_use":
                    text = json.dumps(b.get("input"), ensure_ascii=False); tool = True
                elif b.get("type") in ("thinking", "redacted_thinking"):
                    think += b.get("thinking", "") or "x" * 400
            sr = d.get("stop_reason") or "end_turn"
            if sr == "refusal":
                raise ContentFiltered("stop_reason=refusal")
            u = d.get("usage") or {}
            out = u.get("output_tokens", 0) or 0
            rt = min(out, int(len(think) / 1.6)) if think else 0     # Anthropic 不单列推理 token：按字数估
            return Result(text, "length" if sr == "max_tokens" else "stop", u.get("input_tokens", 0) or 0, out, rt,
                          tool_used=tool, raw_reasoning=bool(think))
        # gemini
        pf = d.get("promptFeedback") or {}
        if pf.get("blockReason"):
            raise ContentFiltered("promptFeedback.blockReason=" + str(pf["blockReason"]))
        cands = d.get("candidates") or []
        if not cands:
            raise LLMError("响应里没有 candidates")
        c = cands[0]; fr = c.get("finishReason") or "STOP"
        if fr in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
            raise ContentFiltered("finishReason=" + fr)
        parts = (c.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        tool = False
        fc = next((p.get("functionCall") for p in parts if p.get("functionCall")), None)
        if not text and fc:
            text = json.dumps(fc.get("args") or {}, ensure_ascii=False); tool = True
        um = d.get("usageMetadata") or {}
        rt = um.get("thoughtsTokenCount", 0) or 0
        return Result(text, "length" if fr == "MAX_TOKENS" else "stop", um.get("promptTokenCount", 0) or 0,
                      (um.get("candidatesTokenCount", 0) or 0) + rt, rt, tool_used=tool, raw_reasoning=rt > 0)

    def _read_stream(self, resp, deadline):
        """SSE → 拼成与非流式相同的 dict，交给 _parse。"""
        k = self.cfg.kind
        text, think, fin, usage, tool_args = [], [], None, {}, []
        ant = {"content": [], "usage": {}, "stop_reason": None}
        gem_last = {}
        for raw in resp:
            if deadline and time.time() > deadline:
                raise TimeoutError("流式输出超过总时长上限")
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            if k == "openai":
                if ev.get("usage"):
                    usage = ev["usage"]
                for ch in ev.get("choices") or []:
                    dl = ch.get("delta") or {}
                    if dl.get("content"):
                        text.append(dl["content"])
                    if dl.get("reasoning_content") or dl.get("reasoning"):
                        think.append(dl.get("reasoning_content") or dl.get("reasoning"))
                    for tc in dl.get("tool_calls") or []:
                        tool_args.append((tc.get("function") or {}).get("arguments") or "")
                    if ch.get("finish_reason"):
                        fin = ch["finish_reason"]
            elif k == "anthropic":
                t = ev.get("type")
                if t == "message_start":
                    ant["usage"].update((ev.get("message") or {}).get("usage") or {})
                elif t == "content_block_delta":
                    dl = ev.get("delta") or {}
                    if dl.get("type") == "text_delta":
                        text.append(dl.get("text", ""))
                    elif dl.get("type") == "thinking_delta":
                        think.append(dl.get("thinking", ""))
                    elif dl.get("type") == "input_json_delta":
                        tool_args.append(dl.get("partial_json", ""))
                elif t == "message_delta":
                    ant["stop_reason"] = (ev.get("delta") or {}).get("stop_reason")
                    ant["usage"].update(ev.get("usage") or {})
                elif t == "error":
                    raise LLMError(self.redact(json.dumps(ev.get("error"), ensure_ascii=False)[:300]))
            else:
                if ev.get("promptFeedback", {}).get("blockReason"):
                    return ev
                for c in ev.get("candidates") or []:
                    for p in (c.get("content") or {}).get("parts") or []:
                        if p.get("thought"):
                            think.append(p.get("text", ""))
                        elif p.get("text"):
                            text.append(p["text"])
                    if c.get("finishReason"):
                        gem_last["finishReason"] = c["finishReason"]
                if ev.get("usageMetadata"):
                    gem_last["usageMetadata"] = ev["usageMetadata"]
        if k == "openai":
            msg = {"content": "".join(text)}
            if not msg["content"] and tool_args:
                msg["tool_calls"] = [{"function": {"arguments": "".join(tool_args)}}]
            if think:
                msg["reasoning_content"] = "".join(think)
            return {"choices": [{"message": msg, "finish_reason": fin or "stop"}], "usage": usage}
        if k == "anthropic":
            blocks = []
            if think:
                blocks.append({"type": "thinking", "thinking": "".join(think)})
            if tool_args and not text:
                try:
                    blocks.append({"type": "tool_use", "input": json.loads("".join(tool_args))})
                except json.JSONDecodeError:
                    blocks.append({"type": "text", "text": "".join(tool_args)})
            else:
                blocks.append({"type": "text", "text": "".join(text)})
            return {"content": blocks, "stop_reason": ant["stop_reason"], "usage": ant["usage"]}
        parts = ([{"text": "".join(think), "thought": True}] if think else []) + [{"text": "".join(text)}]
        return {"candidates": [{"content": {"parts": parts}, "finishReason": gem_last.get("finishReason", "STOP")}],
                "usageMetadata": gem_last.get("usageMetadata", {})}

    # ---------------- one HTTP exchange with transient retries
    def _exchange(self, messages, max_tokens, json_mode, schema, timeout, stats, stream):
        builder = {"openai": self._openai, "anthropic": self._anthropic, "gemini": self._gemini}[self.cfg.kind]
        url, body, headers = builder(messages, max_tokens, json_mode, schema, stream)
        data = json.dumps(body, ensure_ascii=False).encode()
        to = float(timeout or self.cfg.timeout)
        if self.cfg.max_total_seconds:
            to = min(to, max(5.0, self.cfg.max_total_seconds - 5))
        wait = 0
        for attempt in range(MAX_TRANSIENT + 1):
            req = urllib.request.Request(url, data=data, headers=headers)
            t = time.time()
            with self._sem:
                _bump(stats, "calls")
                try:
                    with urllib.request.urlopen(req, timeout=to) as r:
                        if stream:
                            deadline = t + self.cfg.max_total_seconds - 3 if self.cfg.max_total_seconds else 0
                            d = self._read_stream(r, deadline)
                        else:
                            d = json.loads(r.read())
                    res = self._parse(d); res.seconds = round(time.time() - t, 2)
                    return res
                except urllib.error.HTTPError as e:
                    try:
                        detail = e.read().decode("utf-8", "replace")[:600]
                    except Exception:
                        detail = ""
                    detail = self.redact(detail)
                    if e.code in RETRY_STATUS and attempt < MAX_TRANSIENT:
                        ra = e.headers.get("Retry-After") if e.headers else None
                        _bump(stats, "transient"); _log(stats, {"model": self.cfg.model, "transient": f"HTTP {e.code}", "attempt": attempt + 1})
                        wait = _sleep_for(attempt, ra)
                    elif _FILTER_RX.search(detail) and e.code in (400, 403, 422, 451):
                        raise ContentFiltered(f"HTTP {e.code} 内容过滤", e.code, detail)
                    else:
                        raise LLMError(f"HTTP {e.code}: {detail[:240]}", e.code, detail)
                except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                    if isinstance(e, urllib.error.HTTPError):
                        raise
                    if attempt < MAX_TRANSIENT:
                        _bump(stats, "transient"); _log(stats, {"model": self.cfg.model, "transient": type(e).__name__, "attempt": attempt + 1})
                        wait = _sleep_for(attempt)
                    else:
                        raise LLMError(f"网络错误：{type(e).__name__}: {self.redact(str(e))[:160]}")
            time.sleep(wait)   # 退避时不占并发名额
        raise LLMError("重试次数用尽")

    # ---------------- public
    def complete(self, messages, max_tokens=2000, json_mode=True, schema=None, timeout=None, stats=None, stream=None):
        """一次逻辑调用。返回 Result。参数被拒 / 推理吃光输出 时在这里自适应降级（最多 4 次）。"""
        stream = self.cfg.stream if stream is None else stream
        want = int(max_tokens)
        for _ in range(5):
            mt = want + self.headroom()
            if self.max_cap:
                mt = min(mt, self.max_cap)
            snap = (self.no_schema, self.no_json_mode, self.no_reason_param, self.max_cap, self.reason_level)
            try:
                res = self._exchange(messages, mt, json_mode, schema, timeout, stats, stream)
            except ContentFiltered:
                _bump(stats, "content_filtered")
                raise
            except LLMError as e:
                if e.code not in (400, 422):
                    raise
                if snap != (self.no_schema, self.no_json_mode, self.no_reason_param, self.max_cap, self.reason_level):
                    continue          # 别的线程刚刚做过自适应：按新状态重发一次
                if not self._adapt_to_400(e.body or str(e), stats, mt):
                    raise
                continue
            _bump(stats, "tokens_in", res.tokens_in); _bump(stats, "tokens_out", res.tokens_out)
            _bump(stats, "reasoning_tokens", res.reasoning_tokens)
            _log(stats, {"model": self.cfg.model, "sec": res.seconds, "out_tokens": res.tokens_out,
                         "reasoning_tokens": res.reasoning_tokens, "finish": res.finish})
            starved = res.finish == "length" and res.reasoning_tokens and (
                len(res.text.strip()) < 20 or res.reasoning_tokens >= 0.5 * max(1, res.tokens_out))
            if starved and self.downgrade_reasoning(stats):
                continue
            if res.finish != "length" and looks_refusal(res.text) and json_mode:
                raise ContentFiltered("模型以文字拒答")
            return res
        return res

    def _adapt_to_400(self, body, stats, mt):
        b = body.lower()
        if "response_format" in b or "json_schema" in b or "schema" in b or "responsejsonschema" in b or "tool" in b:
            if not self.no_schema:
                self.no_schema = True; self._note(stats, f"[{self.label}] 不支持 json_schema，改用 JSON 模式"); return True
            if not self.no_json_mode and ("response_format" in b or "json" in b or "mime" in b):
                self.no_json_mode = True; self._note(stats, f"[{self.label}] 不支持 JSON 模式，改用提示词约束"); return True
        if any(x in b for x in ("reasoning", "enable_thinking", "thinking", "thinkingconfig")) and not self.no_reason_param:
            self.no_reason_param = True; self._note(stats, f"[{self.label}] 不接受推理参数，已去掉"); return True
        if any(x in b for x in ("max_tokens", "max_completion_tokens", "maxoutputtokens", "max_output")):
            nums = [int(x) for x in re.findall(r"\b(\d{3,7})\b", b) if 256 <= int(x) < mt]
            cap = max(nums) if nums else max(256, mt // 2)
            if cap < mt:
                self.max_cap = cap; self._note(stats, f"[{self.label}] 输出上限被限制为 {cap}"); return True
        if ("temperature" in b) and self.cfg.temperature is not None:
            self.cfg.temperature = None; return True
        return False


# ------------------------------------------------------------------ platform defaults
PLATFORM = {
    # 平台默认（用户没配置模型时兜底）：凭证只来自服务器环境变量 / 网关注入
    "mercury": {"kind": "openai", "base_url": "https://api.inceptionlabs.ai/v1", "model": "mercury-3", "label": "平台 · Mercury mercury-3",
                "key_env": "INCEPTION_API_KEY", "reasoning": "instant", "timeout": 90, "max_output": 131072},
    "qwen": {"kind": "openai", "base_url": "https://maas.qianwenaiapi.com/compatible-mode/v1", "model": "qwen3.8-max", "label": "平台 · 千问 qwen3.8-max",
             "key_env": "DASHSCOPE_API_KEY", "reasoning": "off", "timeout": 115, "max_total_seconds": 120},
}
_PLATFORM_SEM = threading.BoundedSemaphore(max(1, int(os.environ.get("VR_LLM_CONCURRENCY", 3))))
_platform_clients = {}
_pc_lock = threading.Lock()


def platform_available(name):
    p = PLATFORM.get(name)
    if not p:
        return False
    gw = os.environ.get("VIBEREADING_GATEWAY", "")
    return bool(os.environ.get(p["key_env"]) or gw == "1" or name in [x.strip() for x in gw.split(",")])


def platform_config(name, model=None, reasoning=None):
    p = dict(PLATFORM[name]); env = p.pop("key_env")
    if model:
        p["model"] = model; p["label"] = f"平台 · {model}"
    if reasoning:
        p["reasoning"] = reasoning
    return ModelConfig(api_key=os.environ.get(env) or "injected-by-gateway", concurrency=int(os.environ.get("VR_LLM_CONCURRENCY", 3)), **p)


def platform_client(name, model=None, reasoning=None):
    """平台模型共用一个全局并发闸（VR_LLM_CONCURRENCY）。同一 (name, model, reasoning) 复用实例（保留降级状态）。"""
    key = (name, model or PLATFORM[name]["model"], reasoning or PLATFORM[name].get("reasoning"), os.environ.get(PLATFORM[name]["key_env"], ""))
    with _pc_lock:
        c = _platform_clients.get(key)
        if c is None:
            c = Client(platform_config(name, model, reasoning), sem=_PLATFORM_SEM)
            _platform_clients[key] = c
        return c
