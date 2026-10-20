"""Stage 1 · 切分：把原文切成编号段落。纯规则，不经过 LLM。

规则
- 一个非空行 = 一个段落（去掉首尾空白，包括全角空格缩进）。原文其它字符一个不动。
- 若文本是"硬换行 + 空行分段"（每块平均 >1.6 行且行长较齐），则一块 = 一段，块内换行按 CJK 规则直接拼接
  （西文行之间补一个空格）。这是唯一的"拼接"，会写进 segments.json 的 mode 字段。
- 脚注：文末形如 "(1) …" "（1）…" "[1] …" "① …" "注1：…" 的段落标记为 footnote，并在正文里找到第一处
  相同标记作为引用位置（只加链接，不改文字）。
- 章节标题：短行且匹配 第X章/回/节、Chapter N、序/楔子/尾声 等 → role=heading。
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
    if blocks and sum(len(b) for b in blocks) / len(blocks) > 1.6 and len(multi) >= max(2, len(blocks) * 0.4):
        mode = "block"
    paras = []
    if mode == "line":
        paras = [_strip(l) for b in blocks for l in b]
    else:
        for b in blocks:
            s = ""
            for l in b:
                l = _strip(l)
                if s and not (_is_cjk(s[-1]) or _is_cjk(l[0])):
                    s += " "
                s += l
            paras.append(s)

    segs = []
    for i, t in enumerate(paras):
        role = "text"
        if len(t) <= 24 and HEAD.match(t):
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
    return {"mode": mode, "count": len(segs), "segments": segs,
            "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]}


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
