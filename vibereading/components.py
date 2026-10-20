"""组件库：每个函数 (params, ctx) -> HTML 字符串。全部 aria-hidden，纯 CSS/SVG/Canvas，零图片零外链。"""
import html, hashlib, random

E = html.escape


def cap(p, default=""):
    c = (p.get("caption") or default).strip()
    return f"<figcaption>{E(c)}</figcaption>" if c else ""


def fig(cls, inner, p, extra_attr="", draw=False, default_cap=""):
    d = " draw" if draw else ""
    return f'<figure class="fig {cls} reveal{d}" aria-hidden="true"{extra_attr}>{inner}{cap(p, default_cap)}</figure>'


# ---------------------------------------------------------------- doorbell / door / knock
def doorbell(p, ctx):
    return fig("c-doorbell", '<div class="bell"><div class="bell-plate"><div class="bell-btn"></div></div>'
               '<i class="ring r1"></i><i class="ring r2"></i><i class="ring r3"></i></div>', p, default_cap="叮——")


def door(p, ctx):
    a = p.get("action", "open")
    return fig("c-door", '<div class="frame"><div class="leaf"></div></div>', p, f' data-a="{E(a)}"')


def knock(p, ctx):
    col = ctx["color_of"](p.get("who"))
    st = f' style="--kc:{col}"' if col else ""
    return fig("c-knock", '<div class="frame"><i class="k k1"></i><i class="k k2"></i></div>', p, st, default_cap="叩")


# ---------------------------------------------------------------- interior line drawings
ROOM_SVG = {
    "kitchen": {
        "window": '<rect x="70" y="24" width="210" height="96"/><line x1="175" y1="24" x2="175" y2="120"/><line x1="70" y1="72" x2="280" y2="72"/>',
        "counter": '<line x1="20" y1="160" x2="440" y2="160" class="thick"/><rect x="30" y="162" width="400" height="92"/><line x1="160" y1="162" x2="160" y2="254"/>',
        "sink": '<line x1="300" y1="162" x2="300" y2="254"/><path d="M250 160 q0 14 14 14 h72 q14 0 14 -14"/><path d="M300 160 v-26 q0 -8 10 -8 h8"/>',
        "jar": '<rect x="110" y="122" width="34" height="38" rx="6"/><rect x="114" y="134" width="26" height="14" class="fill-dark"/><line x1="127" y1="128" x2="140" y2="96"/><ellipse cx="141" cy="93" rx="4" ry="6" transform="rotate(22 141 93)"/>',
        "fridge": '<rect x="480" y="40" width="96" height="214" rx="4"/><line x1="480" y1="116" x2="576" y2="116"/><line x1="566" y1="62" x2="566" y2="98" class="thick"/><line x1="566" y1="132" x2="566" y2="176" class="thick"/>',
        "bowl": '<path d="M360 150 q20 14 40 0 z"/><line x1="354" y1="150" x2="406" y2="150"/>',
        "_base": '<line x1="0" y1="254" x2="600" y2="254"/>',
    },
    "tavern": {
        "counter": '<path d="M40 150 H380 V254" class="thick"/><path d="M40 150 V254"/><line x1="40" y1="200" x2="380" y2="200" class="soft"/><path d="M380 150 L470 120 V230 L380 254" />',
        "wine_jar": '<path d="M90 150 q-14 -30 4 -50 h28 q18 20 4 50 z" class="fillc"/><line x1="96" y1="100" x2="120" y2="100"/><path d="M150 150 q-14 -30 4 -50 h28 q18 20 4 50 z" class="fillc"/><line x1="156" y1="100" x2="180" y2="100"/>',
        "bowl": '<path d="M240 150 q14 12 28 0 z"/><line x1="236" y1="150" x2="272" y2="150"/><path d="M290 150 q14 12 28 0 z"/><line x1="286" y1="150" x2="322" y2="150"/>',
        "stove": '<rect x="200" y="210" width="70" height="44"/><path d="M215 210 q20 -18 40 0"/>',
        "sign": '<line x1="520" y1="20" x2="520" y2="120"/><path d="M520 26 h50 v70 l-25 -12 l-25 12 z" class="fillc"/><text x="533" y="66">酒</text>',
        "door": '<rect x="500" y="130" width="80" height="124"/><line x1="540" y1="130" x2="540" y2="254" class="soft"/>',
        "_base": '<line x1="0" y1="254" x2="600" y2="254"/><line x1="0" y1="20" x2="480" y2="20" class="soft"/>',
    },
    "living": {
        "window": '<rect x="380" y="30" width="160" height="110"/><line x1="460" y1="30" x2="460" y2="140"/>',
        "chair": '<path d="M60 160 h200 v-40 q0 -14 -14 -14 h-172 q-14 0 -14 14 z"/><rect x="50" y="160" width="220" height="40" rx="6"/><line x1="70" y1="200" x2="70" y2="222"/><line x1="250" y1="200" x2="250" y2="222"/>',
        "lamp": '<line x1="320" y1="90" x2="320" y2="254"/><path d="M296 90 h48 l-10 -30 h-28 z" class="fillc"/>',
        "table": '<ellipse cx="160" cy="236" rx="80" ry="10"/><line x1="160" y1="236" x2="160" y2="254"/>',
        "bookshelf": '<rect x="480" y="150" width="90" height="104"/><line x1="480" y1="185" x2="570" y2="185"/><line x1="480" y1="220" x2="570" y2="220"/>',
        "_base": '<line x1="0" y1="254" x2="600" y2="254"/>',
    },
    "bedroom": {
        "window": '<rect x="60" y="30" width="150" height="110"/><line x1="135" y1="30" x2="135" y2="140"/>',
        "bed": '<rect x="230" y="170" width="300" height="56" rx="4"/><rect x="230" y="120" width="20" height="134"/><rect x="250" y="156" width="60" height="18" rx="8"/><line x1="530" y1="226" x2="530" y2="254"/>',
        "lamp": '<line x1="160" y1="200" x2="160" y2="254"/><path d="M140 200 h40 l-8 -26 h-24 z" class="fillc"/>',
        "desk": '<line x1="40" y1="200" x2="200" y2="200" class="thick"/><line x1="50" y1="200" x2="50" y2="254"/><line x1="190" y1="200" x2="190" y2="254"/>',
        "_base": '<line x1="0" y1="254" x2="600" y2="254"/>',
    },
    "classroom": {
        "blackboard": '<rect x="120" y="30" width="360" height="120" class="fillc"/><line x1="140" y1="70" x2="300" y2="70" class="soft"/><line x1="140" y1="100" x2="260" y2="100" class="soft"/>',
        "desk": '<g><line x1="60" y1="210" x2="200" y2="210" class="thick"/><line x1="70" y1="210" x2="70" y2="254"/><line x1="190" y1="210" x2="190" y2="254"/></g><g><line x1="240" y1="210" x2="380" y2="210" class="thick"/><line x1="250" y1="210" x2="250" y2="254"/><line x1="370" y1="210" x2="370" y2="254"/></g><g><line x1="420" y1="210" x2="560" y2="210" class="thick"/><line x1="430" y1="210" x2="430" y2="254"/><line x1="550" y1="210" x2="550" y2="254"/></g>',
        "window": '<rect x="520" y="30" width="60" height="120"/>',
        "_base": '<line x1="0" y1="254" x2="600" y2="254"/>',
    },
    "office": {
        "desk": '<line x1="120" y1="190" x2="440" y2="190" class="thick"/><rect x="340" y="190" width="100" height="64"/><line x1="130" y1="190" x2="130" y2="254"/>',
        "lamp": '<path d="M180 190 v-50 l30 -30"/><path d="M200 100 l30 10 l-12 22 z" class="fillc"/>',
        "bookshelf": '<rect x="480" y="40" width="96" height="214"/><line x1="480" y1="90" x2="576" y2="90"/><line x1="480" y1="140" x2="576" y2="140"/><line x1="480" y1="190" x2="576" y2="190"/>',
        "window": '<rect x="60" y="30" width="180" height="100"/><line x1="150" y1="30" x2="150" y2="130"/>',
        "_base": '<line x1="0" y1="254" x2="600" y2="254"/>',
    },
    "hall": {
        "door": '<rect x="60" y="40" width="110" height="214"/><circle cx="150" cy="150" r="4"/>',
        "lamp": '<line x1="300" y1="0" x2="300" y2="50"/><path d="M280 50 h40 l-6 22 h-28 z" class="fillc"/>',
        "chair": '<path d="M420 254 v-60 h50 v60 M420 220 h50"/>',
        "_stairs": '<path d="M600 254 h-40 v-24 h-40 v-24 h-40 v-24 h-40 v-24 h-40 v-24"/>',
        "_base": '<line x1="0" y1="254" x2="600" y2="254"/>',
    },
}


def interior(p, ctx):
    room = ROOM_SVG.get(p.get("room"), ROOM_SVG["kitchen"])
    want = set(p.get("props") or [])
    keys = [k for k in room if not k.startswith("_")]
    use = [k for k in keys if k in want] or keys
    if len(use) < 2:  # too sparse to read as a room
        use = keys
    parts = [room[k] for k in use] + [v for k, v in room.items() if k.startswith("_")]
    svg = f'<svg viewBox="0 0 600 270" class="ink">{"".join(parts)}</svg>'
    return fig("c-interior", svg, p, draw=True)


# ---------------------------------------------------------------- window with weather
VIEW_SVG = {
    "garden": '<path d="M120 230 L210 120 L400 120 L490 230 Z" class="view"/><line x1="165" y1="175" x2="445" y2="175" class="view"/><line x1="305" y1="120" x2="305" y2="230" class="view"/><rect x="508" y="186" width="34" height="44"/><path d="M525 186 q-12 -40 -4 -82 q4 -10 8 0 q8 42 -4 82"/><circle cx="525" cy="94" r="8"/>',
    "street": '<path d="M0 200 L600 170" class="view"/><rect x="40" y="70" width="90" height="130" class="view"/><rect x="160" y="100" width="70" height="96" class="view"/><rect x="420" y="60" width="110" height="114" class="view"/><line x1="320" y1="90" x2="320" y2="186" class="view"/><circle cx="320" cy="86" r="6" class="view"/>',
    "field": '<path d="M0 170 q150 -40 300 -10 t300 -20" class="view"/><path d="M0 210 q200 -30 600 -20" class="view"/><circle cx="470" cy="70" r="22" class="view"/>',
    "sea": '<line x1="0" y1="140" x2="600" y2="140" class="view"/><path d="M0 170 q30 -8 60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0" class="view"/><path d="M0 205 q30 -8 60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0" class="view"/>',
    "city": '<path d="M0 230 V150 h50 V100 h40 V160 h60 V60 h50 V140 h70 V90 h60 V170 h50 V110 h60 V150 h60 V80 h50 V230" class="view"/>',
}


def window(p, ctx):
    w = p.get("weather", "clear"); v = VIEW_SVG.get(p.get("view"), VIEW_SVG["garden"])
    frame = ('<rect x="4" y="4" width="592" height="252" class="thick"/><line x1="200" y1="4" x2="200" y2="256"/>'
             '<line x1="400" y1="4" x2="400" y2="256"/><line x1="4" y1="130" x2="596" y2="130"/>')
    canvas = f'<canvas data-weather="{E(w)}"></canvas>' if w in ("rain", "snow", "wind", "night") else ""
    breath = '<i class="breath"></i>' if w in ("clear", "fog") else ""
    inner = f'<div class="glass"><svg viewBox="0 0 600 260" class="ink">{v}{frame}</svg>{canvas}{breath}</div>'
    return fig("c-window", inner, p, f' data-w="{E(w)}"', draw=True)


# ---------------------------------------------------------------- car / stairs
def car_night(p, ctx):
    dusk = p.get("time") == "dusk"
    sky = ('<stop offset="0" stop-color="#5a4a5e"/><stop offset="1" stop-color="#c9906a"/>' if dusk
           else '<stop offset="0" stop-color="#232a33"/><stop offset="1" stop-color="#3a3f45"/>')
    uid = ctx["uid"]()
    svg = f'''<svg viewBox="0 0 600 300" class="car"><defs><linearGradient id="sky{uid}" x1="0" y1="0" x2="0" y2="1">{sky}</linearGradient>
<clipPath id="ws{uid}"><path d="M70 40 Q300 18 530 40 L570 210 Q300 196 30 210 Z"/></clipPath></defs>
<g clip-path="url(#ws{uid})"><rect x="0" y="0" width="600" height="300" fill="url(#sky{uid})"/>
<path d="M300 112 L60 230 M300 112 L540 230" class="road"/><line x1="300" y1="112" x2="300" y2="240" class="dashl"/>
<line x1="0" y1="112" x2="600" y2="112" class="horizon"/><rect class="sweep" x="-200" y="0" width="120" height="300"/></g>
<path d="M70 40 Q300 18 530 40 L570 210 Q300 196 30 210 Z" class="frame"/><rect x="262" y="34" width="76" height="22" rx="5" class="mirror"/>
<line x1="300" y1="22" x2="300" y2="34" class="frame"/><path d="M0 300 L0 222 Q300 196 600 222 L600 300 Z" class="board"/>
<circle cx="160" cy="292" r="62" class="wheel"/><circle cx="160" cy="292" r="12" class="wheel"/></svg>'''
    return fig("c-car", svg, p)


def stairs_memory(p, ctx):
    svg = ('<svg viewBox="0 0 160 200" class="ink"><path d="M10 190 h28 v-26 h28 v-26 h28 v-26 h28 v-26 h28 v-26"/>'
           '<g class="robe"><circle cx="94" cy="70" r="7"/><path d="M86 80 q8 -4 16 0 l4 32 h-24 z"/></g></svg>')
    return fig("c-stairs", svg, p)


# ---------------------------------------------------------------- cards / ledger / timeline
def stamp_cards(p, ctx):
    cards = "".join(f'<div class="card"><small>{E(c.get("label", ""))}</small><b class="stamp">{E(c.get("value", ""))}</b></div>'
                    for c in (p.get("cards") or [])[:3])
    return fig("c-stamp", cards, p)


def ledger(p, ctx):
    rows = "".join(f'<div class="row {"erased" if e.get("state") == "erased" else ""}">{E(e.get("text", ""))}</div>'
                   for e in (p.get("entries") or [])[:4])
    return fig("c-ledger", f'<div class="board">{rows}</div>', p)


def timeline(p, ctx):
    labels = [l for l in (p.get("labels") or []) if isinstance(l, str)][:6]
    lis = "".join(f'<li style="--n:{i}">{E(l)}</li>' for i, l in enumerate(labels))
    return fig("c-timeline", f"<ol>{lis}</ol>", p)


# ---------------------------------------------------------------- blur chart / silence / sound
def blur_chart(p, ctx):
    k = p.get("kind", "bars")
    if k == "lines":
        body = '<polyline points="30,110 80,80 130,95 180,50 230,70 280,30" /><polyline points="30,120 80,115 130,100 180,105 230,85 280,90" class="soft"/>'
    elif k == "page":
        body = "".join(f'<line x1="40" y1="{20 + i * 14}" x2="{260 - (i * 37) % 70}" y2="{20 + i * 14}"/>' for i in range(8))
    else:
        body = '<rect x="40" y="70" width="28" height="60"/><rect x="85" y="40" width="28" height="90"/><rect x="130" y="88" width="28" height="42"/><rect x="175" y="55" width="28" height="75"/><rect x="220" y="30" width="28" height="100"/><rect x="265" y="78" width="28" height="52"/>'
    svg = f'<svg viewBox="0 0 320 150" class="blurc"><line x1="20" y1="130" x2="310" y2="130"/><line x1="20" y1="10" x2="20" y2="130"/>{body}</svg>'
    return fig("c-blur", svg, p)


def silence(p, ctx):
    return f'<div class="c-silence reveal" aria-hidden="true" data-l="{E(p.get("length", "short"))}"><i></i></div>'


def sound(p, ctx):
    g = (p.get("glyph") or "哈")[:2]
    n = max(3, min(12, int(p.get("count") or 7)))
    rnd = random.Random(hashlib.md5((g + str(ctx["seq"])).encode()).hexdigest())
    spans = []
    for i in range(n):
        x, y = rnd.uniform(4, 88), rnd.uniform(5, 70); s = rnd.uniform(1.1, 2.6); d = rnd.uniform(0, 2.6)
        spans.append(f'<span class="g" style="left:{x:.1f}%;top:{y:.1f}%;font-size:{s:.2f}rem;--d:{d:.2f}s">{E(g)}</span>')
    return fig("c-sound", f'<div class="field">{"".join(spans)}</div>', p)


# ---------------------------------------------------------------- object close-ups
THING_SVG = {
    "jar_spoon": '<ellipse cx="60" cy="48" rx="30" ry="8"/><path d="M30 48 v42 q0 12 12 12 h36 q12 0 12 -12 v-42"/><path d="M34 70 h52" style="stroke:#5b3a26;stroke-width:6"/><g class="{a}"><line x1="60" y1="60" x2="60" y2="12"/><ellipse cx="60" cy="10" rx="4" ry="6"/></g>',
    "bowl": '<path d="M24 66 q36 44 72 0 z" class="fillc"/><line x1="18" y1="66" x2="102" y2="66"/><line x1="48" y1="94" x2="72" y2="94"/>',
    "wine_cup": '<path d="M36 50 h48 l-6 40 q-18 8 -36 0 z" class="fillc"/><line x1="44" y1="62" x2="76" y2="62" class="soft"/><ellipse cx="60" cy="50" rx="24" ry="5"/>',
    "bean_dish": '<ellipse cx="60" cy="80" rx="44" ry="12" class="fillc"/><ellipse cx="60" cy="76" rx="44" ry="12"/>' + "".join(
        f'<ellipse cx="{x}" cy="{y}" rx="5" ry="3.4" style="fill:#7b6a3a;stroke:none"/>' for x, y in [(44, 74), (56, 71), (68, 75), (78, 72), (52, 79), (64, 80)]),
    "coins": "".join(f'<g class="{{a}}" style="animation-delay:{i * .25}s"><ellipse cx="60" cy="{92 - i * 9}" rx="22" ry="7" class="fillc"/><rect x="56" y="{89 - i * 9}" width="8" height="5"/></g>' for i in range(4)),
    "book": '<path d="M20 40 q20 -8 40 4 q20 -12 40 -4 v52 q-20 -8 -40 4 q-20 -12 -40 -4 z" class="fillc"/><line x1="60" y1="44" x2="60" y2="96"/>',
    "letter": '<rect x="18" y="36" width="84" height="54" class="fillc"/><path d="M18 36 l42 30 l42 -30"/>',
    "phone": '<rect x="38" y="16" width="44" height="88" rx="8" class="fillc"/><line x1="52" y1="96" x2="68" y2="96"/>',
    "cigarette": '<rect x="20" y="78" width="70" height="8" class="fillc"/><rect x="90" y="78" width="12" height="8" style="fill:#c98a4a;stroke:none"/>',
    "key": '<circle cx="36" cy="60" r="14"/><path d="M50 60 h52 v10 M86 60 v8"/>',
    "candle": '<rect x="48" y="50" width="24" height="52" class="fillc"/><line x1="60" y1="50" x2="60" y2="42"/><path class="flame" d="M60 20 q10 14 0 22 q-10 -8 0 -22z"/>',
    "glass": '<path d="M38 24 h44 l-6 78 h-32 z" class="fillc"/><line x1="42" y1="56" x2="78" y2="56" class="soft"/>',
}


def obj(p, ctx):
    t = p.get("thing", "bowl"); a = p.get("action", "none")
    body = THING_SVG.get(t, THING_SVG["bowl"]).replace("{a}", "spin" if a == "spin" else ("drop" if a == "drop" else ""))
    if a == "spin" and "{a}" not in THING_SVG.get(t, "") and t != "jar_spoon":
        body = f'<g class="spin">{body}</g>'
    if a == "drop" and t != "coins":
        body = f'<g class="drop">{body}</g>'
    steam = ('<g class="steam"><path d="M48 40 q-6 -8 0 -16 t0 -16"/><path d="M60 38 q-6 -8 0 -16 t0 -16"/><path d="M72 40 q-6 -8 0 -16 t0 -16"/></g>'
             if a == "steam" else "")
    return fig("c-object", f'<svg viewBox="0 0 120 120" class="ink">{steam}{body}</svg>', p)


def pullquote(text, icon="door"):
    ic = {"door": '<div class="door-mini"><div class="leaf"></div></div>', "mark": '<div class="qmark">“</div>'}.get(icon, "")
    cls = "pull reveal" + ("" if ic else " noicon")
    return (f'<aside class="{cls}" aria-hidden="true">{ic}<blockquote>{E(text)}</blockquote>'
            f'<span class="pull-src">摘自上文</span></aside>')


RENDER = {"doorbell": doorbell, "door": door, "interior": interior, "window": window, "car_night": car_night,
          "stairs_memory": stairs_memory, "stamp_cards": stamp_cards, "blur_chart": blur_chart, "knock": knock,
          "silence": silence, "object": obj, "timeline": timeline, "ledger": ledger, "sound": sound}

HERO_ART = {
    "door": '<div class="hero-art m-door" aria-hidden="true"><div class="light"></div><div class="leaf"></div></div>',
    "window": '<div class="hero-art m-window" aria-hidden="true"><canvas data-weather="rain"></canvas></div>',
    "lamp": '<div class="hero-art m-lamp" aria-hidden="true"><div class="cord"><div class="shade"></div><div class="cone"></div></div></div>',
    "moon": '<div class="hero-art m-moon" aria-hidden="true"></div>',
}
