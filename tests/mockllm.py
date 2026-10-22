"""本地假模型服务器（OpenAI 兼容 / Anthropic / Gemini 三种协议，流式 + 非流式），给 provider / 探针 / 分档 / 护栏 / Web 测试用。
不联网、不需要任何 key。行为由 MockLLM.state 控制：
  rate_limit_n   前 n 次请求返回 429（Retry-After: 0）
  filter_words   用户消息含这些词时按协议返回内容过滤
  weak           True：不支持 response_format（400）、输出带解释文字和代码块、偶尔漏段
  max_output     输出 token 上限（超过就截断并 finish=length）
  starve_reasoning  请求里带推理参数（reasoning_effort≠minimal/instant、enable_thinking=true、thinking enabled、thinkingBudget>0）时，
                 推理吃光输出（正文为空，finish=length）
  echo_key_on_500  对第一个请求返回 500，错误正文里回显 Authorization 头（测试 redact）
  bad_visuals    detail 输出里故意放一个与原文不符的 car_night
"""
import json, re, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHARS = [{"id": "xl", "name": "祥林嫂", "aliases": [], "color": "#2b4a6f"}, {"id": "wo", "name": "我", "aliases": [], "color": "#6b3e26"},
         {"id": "sishu", "name": "四叔", "aliases": ["鲁四老爷"], "color": "#3d5a3a"}]


def brain(sysm, user, state):
    if "只回复两个字母" in user or "只回复：OK" in user:
        return "OK"
    if "键 n 为整数 3" in user:
        return {"n": 3, "words": ["门", "窗", "灯"]}
    m = re.search(r"items 恰好 (\d+)", user)
    if m:
        return {"items": [{"i": k, "w": "风和日丽"} for k in range(int(m.group(1)))]}
    if "暗号" in user:
        return "青鱼七号"
    if "紧张度0到1" in user:
        idx = [int(x) for x in re.findall(r"^\[(\d+)\]", user, re.M)]
        first = re.search(r"^\[0\] (.{12})", user, re.M)
        return {"paras": [[i, "", 0.3] for i in idx], "quote": first.group(1) if first else ""}
    if "情绪张力" in user:
        return {"tension": 0.7, "emotion": "悲痛"}
    if "你是图文长篇" in sysm:
        return {"heroes": []}
    if "分镜导演" in sysm and "只为这一章划分场景" in sysm:
        a, b = map(int, re.search(r"编号 (\d+)–(\d+)", user).groups())
        return {"scenes": _scenes(a, b)}
    if "全书通用" in sysm:
        return {"meta": {"title": "测试", "subtitle": "甲 · 乙", "hero_motif": "door", "hero_segments": []}, "characters": CHARS, "focal_pair": ["xl", "wo"]}
    if "分镜导演" in sysm and "scenes" in sysm and "共 " in user and "逐字子串" not in sysm:
        n = int(re.search(r"共 (\d+) 段", user).group(1))
        return {"meta": {"title": "测试", "subtitle": "甲 · 乙", "hero_motif": "door", "hero_segments": []}, "characters": CHARS,
                "focal_pair": ["xl", "wo"], "scenes": _scenes(0, n - 1)}
    if "逐字子串" in sysm:   # detail
        idx = [int(x) for x in re.findall(r"^\[(\d+)\]", user, re.M)]
        state.setdefault("detail_batches", []).append(idx)
        if state.get("weak") and state.get("drop_first_detail") and len(state["detail_batches"]) == 1:
            idx = idx[:-2]            # 弱模型：第一批漏两段 → 触发修复
        vis = []
        if state.get("bad_visuals") and idx:
            vis.append({"at": idx[0], "pos": "after", "type": "car_night", "params": {"time": "night", "caption": "夜路"}})
        return {"paras": [[i, "", ["xl"], 0.4, 0.3, 0.0, ""] for i in idx], "utterances": [], "visuals": vis, "quotes": []}
    return {"ok": True}


def _scenes(a, b, size=6):
    out, s = [], a
    while s <= b:
        e = min(b, s + size - 1); out.append({"start": s, "end": e, "name": f"场景{len(out) + 1}", "time": "", "place": "", "tone": "day"}); s = e + 1
    return out


def _wants_reasoning(body, kind):
    if kind == "openai":
        if body.get("enable_thinking") is True:
            return True
        return body.get("reasoning_effort") not in (None, "minimal", "instant")
    if kind == "anthropic":
        return (body.get("thinking") or {}).get("type") == "enabled"
    return ((body.get("generationConfig") or {}).get("thinkingConfig") or {}).get("thinkingBudget", 0) > 0


class MockLLM:
    def __init__(self):
        self.state = {}; self.requests = []; self.lock = threading.Lock(); self.inflight = 0; self.max_inflight = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                with outer.lock:
                    outer.requests.append({"path": self.path, "body": body, "headers": dict(self.headers)})
                    outer.inflight += 1; outer.max_inflight = max(outer.max_inflight, outer.inflight)
                try:
                    outer.handle(self, body)
                finally:
                    with outer.lock:
                        outer.inflight -= 1

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def url(self, kind="openai"):
        return {"openai": f"http://127.0.0.1:{self.port}/v1", "anthropic": f"http://127.0.0.1:{self.port}",
                "gemini": f"http://127.0.0.1:{self.port}/v1beta"}[kind]

    def close(self):
        self.srv.shutdown(); self.srv.server_close()

    def _send(self, h, code, obj, headers=None):
        b = json.dumps(obj, ensure_ascii=False).encode() if not isinstance(obj, bytes) else obj
        h.send_response(code); h.send_header("Content-Type", "application/json"); h.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers(); h.wfile.write(b)

    def handle(self, h, body):
        st = self.state
        kind = "anthropic" if h.path.endswith("/messages") else ("gemini" if ":generateContent" in h.path or ":streamGenerateContent" in h.path else "openai")
        if st.get("delay"):
            time.sleep(st["delay"])
        if st.get("rate_limit_n", 0) > 0:
            st["rate_limit_n"] -= 1
            return self._send(h, 429, {"error": {"type": "rate_limit"}}, {"Retry-After": "0"})
        if st.get("echo_key_on_500"):
            st["echo_key_on_500"] = False
            auth = h.headers.get("Authorization") or h.headers.get("x-api-key") or h.headers.get("x-goog-api-key") or ""
            return self._send(h, 400, {"error": {"message": f"Incorrect API key provided: {auth}"}})
        # messages
        if kind == "openai":
            msgs = body.get("messages") or []
            sysm = "\n".join(m["content"] for m in msgs if m["role"] == "system"); user = "\n".join(m["content"] for m in msgs if m["role"] == "user")
            mt = body.get("max_tokens") or body.get("max_completion_tokens") or 4000
            if st.get("weak") and body.get("response_format"):
                return self._send(h, 400, {"error": {"message": "response_format is not supported for this model"}})
        elif kind == "anthropic":
            sysm = body.get("system") or ""; user = "\n".join(m["content"] for m in body.get("messages") or [] if m["role"] == "user")
            mt = body.get("max_tokens") or 4000
        else:
            sysm = "".join(p["text"] for p in (body.get("systemInstruction") or {}).get("parts") or [])
            user = "\n".join(p["text"] for c in body.get("contents") or [] if c["role"] == "user" for p in c["parts"])
            mt = (body.get("generationConfig") or {}).get("maxOutputTokens") or 4000
        if any(w in user for w in st.get("filter_words", [])):
            if kind == "openai":
                return self._send(h, 400, {"error": {"code": "content_filter", "message": "blocked"}})
            if kind == "anthropic":
                return self._send(h, 200, {"type": "message", "content": [], "stop_reason": "refusal", "usage": {"input_tokens": 5, "output_tokens": 0}})
            return self._send(h, 200, {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]})
        tools = body.get("tools")
        out = brain(sysm, user, st)
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        if st.get("weak") and not isinstance(out, str):
            text = "好的，下面是结果：\n```json\n" + text + "\n```"
        reasoning = 0; finish = "stop"
        if st.get("starve_reasoning") and _wants_reasoning(body, kind):
            reasoning = mt; text = ""; finish = "length"
        cap = min(mt, st.get("max_output") or 10 ** 9)
        toks = max(1, len(text) // 2)
        if toks > cap:
            text = text[: cap * 2]; finish = "length"; toks = cap
        stream = bool(body.get("stream")) or ":streamGenerateContent" in h.path
        if kind == "openai":
            msg = {"role": "assistant", "content": text}
            if tools and not st.get("weak"):
                msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "t1", "type": "function", "function": {"name": tools[0]["function"]["name"], "arguments": "{\"level\":0.8}"}}]}
                finish = "tool_calls"
            usage = {"prompt_tokens": len(user) // 2, "completion_tokens": toks + reasoning, "completion_tokens_details": {"reasoning_tokens": reasoning}}
            if stream:
                return self._sse(h, [{"choices": [{"delta": {"content": text[i:i + 50]}}]} for i in range(0, len(text), 50)] +
                                 [{"choices": [{"delta": {}, "finish_reason": finish}]}, {"choices": [], "usage": usage}], done=True)
            return self._send(h, 200, {"choices": [{"message": msg, "finish_reason": finish}], "usage": usage})
        if kind == "anthropic":
            blocks = []
            if reasoning:
                blocks.append({"type": "thinking", "thinking": "想" * reasoning})
            if tools:
                if body.get("tool_choice", {}).get("name") == "emit":
                    blocks.append({"type": "tool_use", "name": "emit", "input": json.loads(text) if text.strip().startswith("{") else {}})
                else:
                    blocks.append({"type": "tool_use", "name": tools[0]["name"], "input": {"level": 0.8}})
            else:
                blocks.append({"type": "text", "text": text})
            sr = "max_tokens" if finish == "length" else "end_turn"
            usage = {"input_tokens": len(user) // 2, "output_tokens": toks + reasoning}
            if stream:
                evs = [{"type": "message_start", "message": {"usage": {"input_tokens": usage["input_tokens"]}}}]
                if reasoning:
                    evs.append({"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "想" * 10}})
                evs += [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": text[i:i + 50]}} for i in range(0, len(text), 50)]
                evs.append({"type": "message_delta", "delta": {"stop_reason": sr}, "usage": {"output_tokens": usage["output_tokens"]}})
                return self._sse(h, evs)
            return self._send(h, 200, {"type": "message", "content": blocks, "stop_reason": sr, "usage": usage})
        fr = "MAX_TOKENS" if finish == "length" else "STOP"
        parts = [{"text": text}]
        if tools:
            parts = [{"functionCall": {"name": tools[0]["functionDeclarations"][0]["name"], "args": {"level": 0.8}}}]
        um = {"promptTokenCount": len(user) // 2, "candidatesTokenCount": toks, "thoughtsTokenCount": reasoning}
        if stream:
            return self._sse(h, [{"candidates": [{"content": {"parts": [{"text": text[i:i + 50]}]}}]} for i in range(0, len(text), 50)] +
                             [{"candidates": [{"content": {"parts": []}, "finishReason": fr}], "usageMetadata": um}])
        return self._send(h, 200, {"candidates": [{"content": {"parts": parts}, "finishReason": fr}], "usageMetadata": um})

    def _sse(self, h, events, done=False):
        h.send_response(200); h.send_header("Content-Type", "text/event-stream"); h.end_headers()
        for e in events:
            h.wfile.write(b"data: " + json.dumps(e, ensure_ascii=False).encode() + b"\n\n")
        if done:
            h.wfile.write(b"data: [DONE]\n\n")
        h.wfile.flush()
