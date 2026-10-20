"""Stage 1 · 切分：把原文切成编号段落。纯规则，不经过 LLM。

规则
- 一个非空行 = 一个段落（去掉首尾空白，包括全角空格缩进）。原文其它字符一个不动。
- 若文本是"硬换行 + 空行分段"（每块平均 >1.6 行且行长较齐），则一块 = 一段，块内换行按 CJK 规则直接拼接
  （西文行之间补一个空格）。这是唯一的"拼接"，会写进 segments.json 的 mode 字段。
- 脚注：文末形如 "(1) …" "（1）…" "[1] …" "① …" "注1：…" 的段落标记为 footnote，并在正文里找到第一处
  相同标记作为引用位置（只加链接，不改文字）。
- 章节标题：短行且匹配 第X章/回/节、Chapter N、序/楔子/尾声 等 → role=heading。
  另外，若全文有 ≥3 个"空行之后的独立短行"（≤30 字、不以句读结尾，如「2011年1月」「三周后（2011年2月）」），
  它们也视为章节标题（整本书常见的章名格式）。
- 硬换行判定：只有当块内行（除末行外）大多数不以句读结尾时才进入 block 模式；
  "每行一段 + 空行分章"的整本书不会被误判成"一章 = 一段"。
- chapters：按章节标题划分的 [{start, end, title}]（标题段之前若有正文，算作无标题的第 0 章）。
校验不变量：所有段落去空白后按顺序拼接 == 原文去空白（verify_lossless）。
"""
import re, json, hashlib

WS = re.compile(r"[\s\u3000\u200b\ufeff]+")
HEAD = re.compile(r"^(第[0-9一二三四五六七八九十百千零两]+[章回节卷部篇]|chapter\s+\w+|序章?|楔子|引子|尾声|后记|番外|[一二三四五六七八九十]+、|[IVX]+\.?)(\s|$|[:：·.、])", re.I)
FN_LINE = re.compile(r"^\s*([（(]\s*\d{1,3}\s*[)）]|\[\d{1,3}\]|[①-⑳]|注\s*\d{1,3}\s*[:：])")
DATELINE = re.compile(r"^\d{3,4}\s*年(\s*\d{1,2}\s*月)?(\s*\d{1,2}\s*日)?$")


def _strip(s):
    return s.strip(" \t\r\n\u3000\u200b\ufeff")


def _is_cjk(ch):
    return "\u2e80" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef" or "\u3000" <= ch <= "\u303f"


TERMINAL = set("。！？!?…；;”」』\"’）)】—～~.")


def _hard_wrapped(blocks):
    """块内（除每块末行）以句读结尾的行占比 < 45% → 硬换行排版。"""
    inner = [_strip(l) for b in blocks if len(b) > 1 for l in b[:-1] if _strip(l)]
    if len(inner) < 4:
        return False
    ended = sum(1 for l in inner if l[-1] in TERMINAL)
    return ended / len(inner) < 0.45


def _chapter_title_like(t):
    return 0 < len(t) <= 30 and t[-1] not in TERMINAL - set("）)】") and not t.endswith("：") and "，" not in t and "。" not in t


def split(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = text.split("\n")
    blocks, cur = [], []
    for ln in raw_lines:
        if _strip(ln):
            cur.append(ln)
        elif cur:
            blocks.append(cur); cur = []
    if cur:
        blocks.append(cur)
    multi = [b for b in blocks if len(b) > 1]
    mode = "line"
    if blocks and sum(len(b) for b in blocks) / len(blocks) > 1.6 and len(multi) >= max(2, len(blocks) * 0.4) and _hard_wrapped(blocks):
        mode = "block"
    paras = []; block_first = set()
    if mode == "line":
        for b in blocks:
            block_first.add(len(paras))
            paras += [_strip(l) for l in b]
    else:
        for b in blocks:
            s = ""
            for l in b:
                l = _strip(l)
                if s and not (_is_cjk(s[-1]) or _is_cjk(l[0])):
                    s += " "
                s += l
            paras.append(s)

    # 空行之后的独立短行 = 章名（仅当这种行 ≥3 个、且它们后面都跟着正文时才采用，避免短文本误判）
    cand = [i for i in sorted(block_first) if _chapter_title_like(paras[i]) and i + 1 < len(paras) and i + 1 not in block_first]
    block_heads = set(cand) if len(cand) >= 3 else set()
    segs = []
    for i, t in enumerate(paras):
        role = "text"
        if (len(t) <= 24 and HEAD.match(t)) or i in block_heads:
            role = "heading"
        elif len(t) <= 16 and DATELINE.match(t):
            role = "dateline"
        segs.append({"i": i, "text": t, "role": role})

    # footnotes: only consider the tail of the document (after the last non-footnote paragraph)
    k = len(segs)
    while k > 0 and FN_LINE.match(segs[k - 1]["text"]):
        k -= 1
    for s in segs[k:]:
        s["role"] = "footnote"
        mark = FN_LINE.match(s["text"]).group(1).strip()
        mark = re.sub(r"[:：]$", "", mark)
        s["mark"] = mark
        # find the first body occurrence (same marker string, ASCII/fullwidth brackets both tried)
        num = re.sub(r"\D", "", mark)
        cands = [mark]
        if num:
            cands += [f"({num})", f"（{num}）", f"[{num}]"]
        for b in segs[:k]:
            hit = next((c for c in cands if c in b["text"]), None)
            if hit:
                b.setdefault("refs", []).append({"mark": hit, "fn": s["i"]})
                s["ref_from"] = b["i"]
                break
    return {"mode": mode, "count": len(segs), "segments": segs, "chapters": chapters(segs),
            "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]}


def chapters(segs):
    """按 heading 段划分章节：[{start, end, title}]。没有标题时整篇是一章（title 为空）。"""
    heads = [s["i"] for s in segs if s["role"] == "heading"]
    out = []
    if not heads or heads[0] > 0:
        out.append({"start": 0, "end": (heads[0] - 1) if heads else len(segs) - 1, "title": ""})
    for k, h in enumerate(heads):
        end = heads[k + 1] - 1 if k + 1 < len(heads) else len(segs) - 1
        out.append({"start": h, "end": end, "title": segs[h]["text"]})
    # 只有一个空的前言段落时并入下一章
    if len(out) > 1 and not out[0]["title"] and out[0]["end"] - out[0]["start"] < 0:
        out.pop(0)
    return out


def verify_lossless(text, seg):
    a = WS.sub("", text)
    b = WS.sub("", "".join(s["text"] for s in seg["segments"]))
    return a == b


if __name__ == "__main__":
    import sys
    t = open(sys.argv[1], encoding="utf-8").read()
    r = split(t)
    print(json.dumps({k: v for k, v in r.items() if k != "segments"}, ensure_ascii=False),
          "lossless:", verify_lossless(t, r))
    for s in r["segments"]:
        if s["role"] != "text" or s.get("refs"):
            print(s)
