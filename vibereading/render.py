"""Stage 3 · 渲染：分镜 JSON + 原文段落 → 单文件 HTML。
原文只以 html.escape 后的形式进入 <p data-src="i">，可加的只有包裹 span/a（textContent 不变）。"""
import html, json, colorsys, pathlib, re
from . import components as C
from . import hero_prims as HP

E = html.escape
TPL = (pathlib.Path(__file__).parent / "template.html").read_text(encoding="utf-8")
PALETTE = ["#2b4a6f", "#a3253c", "#8a5f1a", "#3f6b4f", "#6b4c8a", "#2f6f78", "#7a3b5c", "#5a5a2a", "#8a3a1e", "#3d5a8a"]
CHAPTER_MODE_CHARS = 20000   # ≥2 章且超过这么多字 → 分章阅读：只有当前章进 DOM，其余章在 <template> 里惰性展开
HEAT_HUE = 14 / 360
PRIORITY = {"car_night": 3, "doorbell": 3, "stairs_memory": 3, "window": 2, "stamp_cards": 2, "interior": 2, "ledger": 2,
            "timeline": 2, "knock": 1, "blur_chart": 1, "door": 1, "sound": 1, "object": 1, "silence": 0}


# ---------------------------------------------------------------- colours
def _rgb(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def _lum(rgb):
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = map(f, rgb); return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = _lum(_rgb(a)), _lum(_rgb(b)); return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def fix_colors(chars):
    used = []
    for k, c in enumerate(chars):
        col = c.get("color") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(c.get("color", ""))) else None
        def bad(x):
            h, l, s = colorsys.rgb_to_hls(*_rgb(x))
            near_heat = min(abs(h - HEAT_HUE), 1 - abs(h - HEAT_HUE)) < 0.05 and s > 0.45 and l > 0.35
            def close(u):
                hu, lu, su = colorsys.rgb_to_hls(*_rgb(u))
                if s < 0.22 and su < 0.22:
                    return abs(l - lu) < 0.18
                return min(abs(h - hu), 1 - abs(h - hu)) < 0.04 and abs(l - lu) < 0.12
            near_used = any(close(u) for u in used)
            return near_heat or near_used
        if not col or bad(col):
            col = next((p for p in PALETTE if p not in used and not bad(p)), PALETTE[k % len(PALETTE)])
        # darken until readable on paper (>= 3.2:1)
        h, l, s = colorsys.rgb_to_hls(*_rgb(col))
        while contrast(_hex(colorsys.hls_to_rgb(h, l, s)), "#faf9f7") < 3.2 and l > 0.05:
            l -= 0.03
        col = _hex(colorsys.hls_to_rgb(h, l, s))
        ln = l
        while contrast(_hex(colorsys.hls_to_rgb(h, ln, s)), "#15171b") < 4.5 and ln < 0.95:
            ln += 0.03
        c["color"] = col; c["night"] = _hex(colorsys.hls_to_rgb(h, ln, min(s, 0.7)))
        used.append(col)
    return chars


# ---------------------------------------------------------------- paragraph html
def para_html(seg, st, chars, utt, extra_cls=""):
    t = seg["text"]; spans = []
    for r in seg.get("refs", []):
        k = t.find(r["mark"])
        if k >= 0:
            spans.append((k, k + len(r["mark"]), "fn", r))
    cmap = {c["id"]: c for c in chars}
    spk = {u["sp"] for u in utt}
    use_utt = utt and not (len(spk) == 1 and st["sp"] in spk and len(t) < 120)
    if use_utt:
        for u in utt:
            k = t.find(u["q"])
            if k >= 0 and not any(a < k + len(u["q"]) and k < b for a, b, *_ in spans):
                spans.append((k, k + len(u["q"]), "utt", u))
    spans.sort()
    out, pos = [], 0
    for a, b, kind, d in spans:
        out.append(E(t[pos:a]))
        if kind == "fn":
            out.append(f'<a class="fn-ref" href="#fn-{d["fn"]}" id="fnref-{d["fn"]}">{E(t[a:b])}</a>')
        else:
            out.append(f'<span class="utt" data-sp="{d["sp"]}" title="{E(cmap[d["sp"]]["name"])}">{E(t[a:b])}</span>')
        pos = b
    out.append(E(t[pos:]))
    inner = "".join(out)
    cls = ["line"] + ([extra_cls] if extra_cls else [])
    attrs = ""
    multi = use_utt and (len(spk) > 1 or len(t) > 150)
    # 保护：长段落里没有任何引号又被标成"台词"，多半是第一人称叙述被误判 → 不画说话人标记
    quoted = any(ch in t for ch in "“”「」『』\"")
    if st["sp"] and not multi and seg["role"] == "text" and (quoted or len(t) <= 120):
        cls.append("say"); attrs = f' data-sp="{st["sp"]}" data-spn="{E(cmap[st["sp"]]["name"])}"'
    tag = "h2" if seg["role"] == "heading" else "p"
    if seg["role"] == "text" or seg["role"] == "heading":
        cls.append("reveal")
    return f'<{tag} class="{" ".join(cls)}" data-src="{seg["i"]}"{attrs}>{inner}</{tag}>'


# ---------------------------------------------------------------- visual curation
def curate(plan, segs, warnings):
    scene_of = {}
    for k, s in enumerate(plan["scenes"]):
        for i in range(s["start"], s["end"] + 1):
            scene_of[i] = k
    vis = sorted(plan["visuals"], key=lambda v: (v["at"], 0 if v.get("pos", "before") == "before" else 1))
    rooms, things, per_scene, last_slot, kept = set(), {}, {}, -99, []
    sil = 0
    for v in vis:
        p, t = v["params"], v["type"]
        slot = v["at"] * 2 + (0 if v.get("pos", "before") == "before" else 1)
        sc = scene_of.get(v["at"], 0)
        why = None
        if t == "interior":
            if p.get("room") in rooms: why = "同一房间的室内图已出现"
            else: rooms.add(p.get("room"))
        if t == "object":
            things[p.get("thing")] = things.get(p.get("thing"), 0) + 1
            if things[p.get("thing")] > 2: why = "同一物件超过 2 次"
        if t == "silence":
            sil += 1
            if sil > 3: why = "留白超过 3 处"
        if not why and per_scene.get(sc, 0) >= 3: why = "该场景已有 3 个组件"
        gap_chars = sum(len(segs[j]["text"]) for j in range(last_slot // 2 + (last_slot % 2), slot // 2 + (slot % 2))) if kept else 9999
        if not why and kept and slot - last_slot < 4 and gap_chars < 120:
            prev = kept[-1]
            if PRIORITY.get(t, 1) > PRIORITY.get(prev["type"], 1):
                warnings.append(f"跳过组件 {prev['type']}@{prev['at']}：被更重要的 {t}@{v['at']} 挤掉")
                kept.pop(); per_scene[scene_of.get(prev["at"], 0)] -= 1
            else:
                why = "与上一个组件太近"
        if why:
            warnings.append(f"跳过组件 {t}@{v['at']}：{why}"); continue
        per_scene[sc] = per_scene.get(sc, 0) + 1; last_slot = slot; kept.append(v)
    # quotes: cap & spread, prefer emotional peaks
    P = plan["paras"]
    qs = [q for q in plan["quotes"] if 6 <= len(q["text"]) <= 60]
    for q in list(qs):  # a pull quote that is (almost) the whole paragraph just repeats what was read a second ago
        if len(q["text"]) >= 0.7 * len(segs[q["at"]]["text"]):
            qs.remove(q); warnings.append(f"摘句与所在段落几乎相同，不重复展示：{q['text'][:14]}")
    cap = max(1, round(len(plan["scenes"]) / 2.5))
    score = lambda q: q.get("w", 3) + 0.5 * (P[q["at"]]["in"] + P[q["at"]]["te"]) + (0.5 if 10 <= len(q["text"]) <= 36 else 0)
    chosen = []
    for q in sorted(qs, key=score, reverse=True):
        if len(chosen) >= cap: break
        if any(abs(q["at"] - c["at"]) < 8 for c in chosen): continue
        chosen.append(q)
    for q in qs:
        if q not in chosen:
            warnings.append(f"摘句未采用（数量上限 {cap} / 距离太近）：{q['text'][:14]}")
    return kept, sorted(chosen, key=lambda q: q["at"])


# ---------------------------------------------------------------- page
def pick_chapters(seg, plan):
    """章节：优先用切分得到的真实章名（≥2 章），否则用规划时的伪章。返回 (chapters, chapter_mode)。"""
    chs = seg.get("chapters") or []
    if len(chs) < 2:
        chs = plan.get("chapters") or []
    total = sum(len(x["text"]) for x in seg["segments"])
    return chs, len(chs) >= 2 and total >= CHAPTER_MODE_CHARS


def split_scenes_at_chapters(scenes, chs):
    """分章模式下场景不能跨章：在章首处把场景切开（外部 plan.json 可能只有一个大场景）。"""
    starts = sorted({c["start"] for c in chs})
    out = []
    for s in scenes:
        cuts = [x for x in starts if s["start"] < x <= s["end"]]
        a = s["start"]
        for x in cuts + [s["end"] + 1]:
            out.append(dict(s, start=a, end=x - 1)); a = x
    return out


def hero_block(h, chars):
    """关键场景动画：sticky 舞台 + 沙箱 iframe（父页面滚动时用 postMessage 传进度）。模块源码放在惰性的 JSON script 里，
    接近视口时才创建 iframe。装饰层（aria-hidden），不含原文。"""
    from . import hero as H
    if h.get("comp"):   # 原语编排：只放编排 JSON，原语库在页面里共用一份（#hs-lib）
        src = json.dumps(h["comp"], ensure_ascii=False).replace("<", "\\u003c")
        kind = "hs-comp"
    else:
        doc = H.wrap(h["module"], chars)
        src = json.dumps(doc, ensure_ascii=False).replace("<", "\\u003c")   # 杜绝 </script> / <!-- 提前结束脚本块
        kind = "hs-src"
    secs = float(h.get("seconds") or 12)
    length = round(1 + secs * 0.12, 2)      # 8s → 1.96 屏高，20s → 3.4 屏高（舞台 sticky，滚动距离 ≈ 高度 − 舞台高）
    title = E(h.get("title") or ""); cap_ = E(h.get("caption") or "")
    return (f'<figure class="hs" aria-hidden="true" data-hs="{E(h["id"])}" style="--len:{length}">'
            f'<div class="hs-stage"><div class="hs-frame"><span class="hs-ph"></span></div>'
            f'<figcaption><b>{title}</b>{(" · " + cap_) if cap_ and cap_ != title else ""}</figcaption></div>'
            f'<script type="application/json" class="{kind}">{src}</script></figure>')


def render(seg, plan, title=None, author=None, work=None, theme="paper"):
    segs = seg["segments"]; n = len(segs); warnings = []
    chs, chapter_mode = pick_chapters(seg, plan)
    if chapter_mode:
        plan = dict(plan, scenes=split_scenes_at_chapters(plan["scenes"], chs))
    chars = fix_colors(plan["characters"])
    cmap = {c["id"]: c for c in chars}
    meta = plan["meta"]
    title = title or meta.get("title") or "未命名"
    author = author if author is not None else meta.get("author", "")
    work = work if work is not None else meta.get("work", "")
    kicker = " · ".join(x for x in [author, f"《{work}》" if work and work != title else ""] if x)
    utt_by = {}
    for u in plan.get("utterances", []):
        utt_by.setdefault(u["i"], []).append(u)
    # 关键场景动画（hero scenes）：合格的模块放沙箱 iframe；失败的回退为预制组件（插在同一位置）
    from . import hero as H
    heroes = H.sanitize_heroes(plan.get("heroes"), n, {c["id"] for c in plan["characters"]}, "".join(x["text"] for x in segs))
    # 首屏段 / 日期行 / 脚注 / 章名不渲染在场景流里：落在这些段上的 hero 挪到最近的正文段（同场景优先）
    hidden = set()
    for k in sorted(meta.get("hero_segments", [])):
        if k == len(hidden) and segs[k]["role"] in ("text", "dateline") and len(segs[k]["text"]) <= 30:
            hidden.add(k)
    if not hidden and segs and segs[0]["role"] == "dateline":
        hidden = {0}
    for h in heroes:
        if segs[h["at"]]["role"] != "text" or h["at"] in hidden:
            sc = next((s for s in plan["scenes"] if s["start"] <= h["at"] <= s["end"]), None)
            new = H.snap_text(seg, h["at"], sc["start"] if sc else 0, sc["end"] if sc else n - 1, hidden)
            if segs[new]["role"] != "text" or new in hidden:
                new = H.snap_text(seg, h["at"], 0, n - 1, hidden)
            if new != h["at"]:
                warnings.append(f"关键场景动画 {h.get('id')} 从段 {h['at']}（{segs[h['at']]['role']}）挪到正文段 {new}")
                h["at"] = new
    hero_at = {}
    for h in heroes:
        if h.get("module") or h.get("comp"):
            hero_at.setdefault((h["at"], h.get("pos", "after")), []).append(("hero", h))
            near = [v for v in plan["visuals"] if abs(v["at"] - h["at"]) <= 1]
            if near:   # 动画旁边的预制组件会显得重复
                for v in near:
                    warnings.append(f"跳过组件 {v['type']}@{v['at']}：与关键场景动画 {h['id']} 相邻")
                plan = dict(plan, visuals=[v for v in plan["visuals"] if v not in near])
        elif h.get("fallback"):
            hero_at.setdefault((h["at"], h.get("pos", "after")), []).append(("fallback", h))
    visuals, quotes = curate(plan, segs, warnings)
    for (i, pos), lst in hero_at.items():   # 回退组件：若同位置已有同类型组件就不重复
        for kind, h in list(lst):
            if kind == "fallback" and any(v["at"] == i and v["type"] == h["fallback"]["type"] for v in visuals):
                lst.remove((kind, h))
    at = {}
    for v in visuals:
        at.setdefault((v["at"], v.get("pos", "before")), []).append(v)
    qat = {q["at"]: q for q in quotes}
    seq = [0]
    def uid():
        seq[0] += 1; return seq[0]
    ctx = {"uid": uid, "seq": 0, "color_of": lambda cid: cmap[cid]["color"] if cid in cmap else None}

    def vhtml(v):
        ctx["seq"] += 1
        return C.RENDER[v["type"]](v["params"], ctx)

    def heroes_html(i, pos):
        out = []
        for kind, h in hero_at.get((i, pos), []):
            if kind == "hero":
                out.append(hero_block(h, chars))
            else:
                out.append(vhtml({"at": i, "type": h["fallback"]["type"], "params": dict(h["fallback"]["params"])}))
        return out

    # hero: leading dateline-ish paragraphs (must be a prefix)
    hero_ids = []
    for h in sorted(meta.get("hero_segments", [])):
        if h == len(hero_ids) and segs[h]["role"] in ("text", "dateline") and len(segs[h]["text"]) <= 30:
            hero_ids.append(h)
    if not hero_ids and segs and segs[0]["role"] == "dateline":
        hero_ids = [0]
    P = plan["paras"]
    art = C.HERO_ART.get(meta.get("hero_motif"), C.HERO_ART["door"])
    hero_lines = "".join(f'<p class="line dateline" data-src="{i}">{E(segs[i]["text"])}</p>' for i in hero_ids)
    sub = meta.get("subtitle") or " · ".join(c["name"] for c in chars[:3])
    body = [f'''<section class="hero" id="top"><canvas id="dust" aria-hidden="true"></canvas><div class="hero-inner">{art}
<div class="hero-text">{f'<p class="kicker" aria-hidden="true">{E(kicker)}</p>' if kicker else ''}<h1 class="hero-title">{E(title)}</h1>{hero_lines}
<p class="hero-sub" aria-hidden="true">{E(sub)}</p></div></div>
<div class="scroll-cue" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 5v14M19 12l-7 7-7-7"/></svg></div></section>''']

    ch_of_scene = []
    for s in plan["scenes"]:
        ch_of_scene.append(max([k for k, c in enumerate(chs) if c["start"] <= s["start"]] or [0]) if chapter_mode else 0)

    toc, foot = [], []
    dropcap_done = False
    ch_parts = {}
    for k, s in enumerate(plan["scenes"]):
        tone = s.get("tone", "day")
        sub_txt = " · ".join(x for x in [s.get("time", ""), s.get("place", "")] if x)
        ck = ch_of_scene[k]
        if chapter_mode and (k == 0 or ch_of_scene[k - 1] != ck):
            toc.append(f'<li class="chap" data-c="{ck}"><a href="#c{ck}">{E(chs[ck]["title"] or f"第 {ck + 1} 章")}</a></li>')
        parts = [f'<div class="marker reveal" aria-hidden="true"><span>{k + 1:02d}</span>{E(s["name"])}'
                 f'{f"<small>{E(sub_txt)}</small>" if sub_txt else ""}</div>']
        band_open = False
        for i in range(s["start"], s["end"] + 1):
            sg = segs[i]
            if i in hero_ids:
                continue
            if sg["role"] == "footnote":
                foot.append(i); continue
            if sg["role"] == "heading" and not chapter_mode:
                toc.append(f'<li class="chap" aria-hidden="true">{E(sg["text"])}</li>')
            mem_open = False
            for v in at.get((i, "before"), []):
                if v["type"] == "car_night" and tone != "night" and not band_open:
                    parts.append('<div class="night-band">'); band_open = True
                if v["type"] == "stairs_memory" and tone != "memory" and not mem_open:
                    parts.append('<div class="memory-block reveal">'); mem_open = True
                parts.append(vhtml(v))
            parts += heroes_html(i, "before")
            extra = ""
            if not dropcap_done and sg["role"] == "text" and len(sg["text"]) > 30 and not sg["text"][0].isascii():
                extra = "dropcap"; dropcap_done = True
            parts.append(para_html(sg, P[i], chars, utt_by.get(i, []), extra))
            if mem_open:
                parts.append("</div>")
            if i in qat:
                parts.append(C.pullquote(qat[i]["text"], "door" if "门" in qat[i]["text"] else "mark"))
            for v in at.get((i, "after"), []):
                if v["type"] == "car_night" and tone != "night" and not band_open:
                    parts.append('<div class="night-band">'); band_open = True
                parts.append(vhtml(v))
            parts += heroes_html(i, "after")
        if k == len(plan["scenes"]) - 1:
            parts.append('<div class="fin reveal" aria-hidden="true">· · ·</div>')
        if band_open:
            parts.append("</div>")
        html_scene = f'<section class="scene tone-{tone}" id="s{k + 1}"><div class="col">' + "\n".join(parts) + "</div></section>"
        if chapter_mode:
            ch_parts.setdefault(ck, []).append(html_scene)
        else:
            body.append(html_scene)
        toc.append(f'<li data-s="{k}"><a href="#s{k + 1}"><em>{k + 1:02d}</em>{E(s["name"])}'
                   f'{f"<small>{E(sub_txt)}</small>" if sub_txt else ""}</a></li>')
    if chapter_mode:
        title_of = lambda j: chs[j]["title"] or f"第 {j + 1} 章"
        for ck in range(len(chs)):
            inner = "\n".join(ch_parts.get(ck, []))
            nav = ['<nav class="chnav" aria-label="章节切换">']
            nav.append(f'<a class="prev" href="#c{ck - 1}" data-go="{ck - 1}"><small>上一章</small>{E(title_of(ck - 1))}</a>' if ck > 0 else '<span></span>')
            nav.append(f'<a class="next" href="#c{ck + 1}" data-go="{ck + 1}"><small>下一章 · {ck + 2}/{len(chs)}</small>{E(title_of(ck + 1))}</a>' if ck + 1 < len(chs) else '<span class="end">全书完</span>')
            nav.append("</nav>")
            inner += "".join(nav)
            if ck == 0:   # 第一章直接进 DOM（首屏 + 无 JS 可读），其余章惰性展开
                body.append(f'<section class="chapter" id="c{ck}" data-ch="{ck}" data-live="1">{inner}</section>')
            else:
                body.append(f'<section class="chapter" id="c{ck}" data-ch="{ck}" hidden><template>{inner}</template></section>')
    if any(h.get("comp") for h in heroes):
        body.append('<script type="application/json" id="hs-lib">' + json.dumps(HP.prefix_doc(chars), ensure_ascii=False).replace("<", "\\u003c") + '</script>')
    if foot:
        fl = []
        for i in foot:
            sg = segs[i]
            back = f'<a class="fn-back" href="#fnref-{i}" aria-label="返回正文">↩</a>' if "ref_from" in sg else ""
            fl.append(f'<div class="fnwrap"><p class="line fn" id="fn-{i}" data-src="{i}" style="display:inline">{E(sg["text"])}</p>{back}</div>')
        body.append('<footer class="notes" id="notes"><div class="col">' + "".join(fl) + "</div></footer>")

    scene_of = [0] * n
    for k, s in enumerate(plan["scenes"]):
        for i in range(s["start"], s["end"] + 1):
            scene_of[i] = k
    state = {
        "key": seg["sha1"], "defaultTheme": theme,
        "scenes": [{"name": s["name"], "time": s.get("time", ""), "place": s.get("place", ""), "c": ch_of_scene[k]}
                   for k, s in enumerate(plan["scenes"])],
        "chapters": [{"title": c["title"] or f"第 {k + 1} 章", "start": c["start"], "end": c["end"],
                      "chars": sum(len(segs[i]["text"]) for i in range(c["start"], c["end"] + 1))}
                     for k, c in enumerate(chs)] if chapter_mode else None,
        "chars": [{"id": c["id"], "name": c["name"], "color": c["color"], "night": c["night"]} for c in chars],
        "focal": plan.get("focal_pair", []),
        "paras": [{"s": scene_of[i], "sp": P[i]["sp"], "pr": P[i]["pr"], "in": round(P[i]["in"], 2),
                   "te": round(P[i]["te"], 2), "bl": round(P[i]["bl"], 2), "bw": P[i]["bw"]} for i in range(n)],
    }
    char_css = "\n".join(
        f'[data-sp="{c["id"]}"]{{--sp:{c["color"]}}} .theme-night [data-sp="{c["id"]}"],.tone-night [data-sp="{c["id"]}"],.night-band [data-sp="{c["id"]}"]{{--sp:{c["night"]}}}'
        for c in chars)
    page_title = " · ".join(x for x in [work if work and work != title else "", title] if x)
    out = (TPL.replace("{{TITLE}}", E(page_title)).replace("{{CHAR_CSS}}", char_css)
           .replace("{{TOC}}", "".join(toc)).replace("{{BODY}}", "\n".join(body))
           .replace("{{STATE}}", json.dumps(state, ensure_ascii=False).replace("</", "<\\/")))
    return out, warnings
