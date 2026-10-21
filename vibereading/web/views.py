"""服务端渲染的页面（无模板引擎依赖：html.escape + f-string）。所有用户数据都经 esc()。"""
import datetime, json, time
from html import escape

from .jobs import STEPS

ASSET_V = str(int(time.time()))


def esc(s):
    return escape("" if s is None else str(s), quote=True)


def fmt_num(n):
    return f"{int(n or 0):,}"


def fmt_time(ts, tz=8):
    if not ts:
        return "—"
    return datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=tz))).strftime("%Y.%m.%d %H:%M")


def issue_no():
    """刊号：自 2026-10-01 起的天数，三位数。"""
    d = (datetime.date.today() - datetime.date(2026, 10, 1)).days
    return f"No.{max(d, 0):03d}"


def layout(title, body, user=None, current="", dark=False, desc="把任意小说 txt 变成一页滚动驱动的代码图文阅读器。原文逐字保留，AI 只负责分镜。"):
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y.%m.%d")
    def cur(k): return ' aria-current="page"' if current == k else ""
    if user:
        nav = (f'<a href="/#sample"{cur("sample")} class="hide-s">示例</a><a href="/shelf"{cur("shelf")}>书架</a>'
               f'<a href="/#upload" class="hide-s">新建</a><span class="who" title="{esc(user["username"])}">@{esc(user["username"])}</span>'
               f'<button type="button" data-logout>退出</button>')
    else:
        nav = (f'<a href="/sample/kongyiji"{cur("sample")}>示例</a><a href="/login"{cur("login")}>登录</a>'
               f'<a href="/register" class="cta"{cur("register")}>注册</a>')
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="{'dark' if dark else 'light'}">
<meta name="description" content="{esc(desc)}"><title>{esc(title)}</title>
<link rel="stylesheet" href="/static/app.css?v={ASSET_V}"><link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' fill='%231a1a1a'/%3E%3Cpath d='M8 9l8 15 8-15' stroke='%23c41e3a' stroke-width='3' fill='none'/%3E%3C/svg%3E">
</head><body class="{'darkroom' if dark else ''}">
<header class="mast"><div class="wrap line"><span>VibeReading · {issue_no()}<span class="hide-s"> · {today}</span></span><span class="hide-s">代码图文阅读器 · Text in, story out</span><span class="tc" data-clock>--:--:--</span></div>
<div class="wrap bar"><a class="brand" href="/"><b>Vibe<i>·</i>Reading</b><span>纸与墨</span></a><nav class="nav">{nav}</nav></div></header>
<main>{body}</main>
<footer class="foot"><div class="wrap"><span>MIT © 2026 Q1f730 · <a href="https://github.com/q1f0730/vibe-reading">github.com/q1f0730/vibe-reading</a></span><span>视觉借鉴 storytelling-web · Paper &amp; Ink · 0 外部请求</span></div></footer>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script src="/static/app.js?v={ASSET_V}"></script></body></html>"""


# ------------------------------------------------------------------ home
def home(user, sample, llm, labels):
    any_llm = any(llm.values())
    modes = ""
    for k, lab in labels.items():
        name, sub = lab
        ok = llm.get(k)
        modes += (f'<label class="mode"><input type="radio" name="mode" value="{k}" {"" if ok else "disabled"}>'
                  f'<span class="dot"></span><span><b>{esc(name)}</b><small>{esc(sub)}</small></span>'
                  f'<span class="tag">{"KEY OK" if ok else "未配置 KEY"}</span></label>')
    modes += ('<label class="mode"><input type="radio" name="mode" value="plan"><span class="dot"></span>'
              '<span><b>上传 plan.json</b><small>跳过 AI，直接按现成分镜渲染（人工 / 其他 agent 写的）</small></span><span class="tag">0 次调用</span></label>')
    notice = "" if any_llm else ('<div class="notice"><b>这台服务器没有配置模型 key。</b>当前为 plan.json 模式：上传原文 + 分镜 JSON 即可生成。'
                                 '配置方法：在服务端 <code>~/.config/vibereading.env</code> 写入 <code>INCEPTION_API_KEY</code> 或 <code>DASHSCOPE_API_KEY</code> 后重启。</div>')
    lock = "" if user else """<div class="lock"><div class="card"><div class="kicker"><b>Members</b> 需要账号</div>
<h3>生成需要登录。</h3><p>每次生成都会调用服务器上的模型额度，所以只对注册用户开放。你生成的书只属于你，下次登录还在书架上。</p>
<div class="acts"><a class="btn" href="/register?next=/%23upload">注册 <span class="arr">→</span></a><a class="btn ghost" href="/login?next=/%23upload">已有账号，登录</a></div></div></div>"""
    s = sample
    first = esc(s["first"]); rest = esc(s["rest"])
    try_btn = ('<button class="link" type="button" data-try-sample style="background:none;border:0;border-bottom:1px solid var(--cinnabar);cursor:pointer">用它跑一遍生成 →</button>'
               if user else '')
    return f"""
<section class="hero wrap">
 <div class="kicker"><b>01</b> 产品 / Product</div>
 <div class="hero-grid">
  <div>
   <h1><span class="l">把一篇小说，</span><span class="l">排成会<span class="red">呼吸</span>的长文。</span><span class="en">Drop in a .txt — get a scroll-driven, ink-drawn reading page.</span></h1>
   <p class="dek">上传 txt，AI 只写<b>分镜</b>：哪里换场、谁在说话、谁脸红、在哪插一幅墨线插画。正文由脚本<b>逐字搬运</b>，最后用无头浏览器逐段比对原文。</p>
   <div class="acts"><a class="btn" href="{'#upload' if user else '/register'}">{'开始生成' if user else '注册并开始'} <span class="arr">→</span></a><a class="btn ghost" href="/sample/kongyiji">先读示例《孔乙己》</a></div>
  </div>
  <dl class="ledger">
   <div><dt>100<small>%</small></dt><dd>原文逐字一致，生成后逐段校验<span>verbatim · {s['segments']}/{s['segments']} segments</span></dd></div>
   <div><dt>0</dt><dd>张图片、0 个外部请求、0 个 CDN<span>css · svg · canvas only</span></dd></div>
   <div><dt>1</dt><dd>个 HTML 文件，离线可读，可转发<span>single file · {s['kb']} KB sample</span></dd></div>
   <div><dt>15</dt><dd>种参数化插画组件：门、窗雨、粉板、声音字…<span>component library</span></dd></div>
  </dl>
 </div>
</section>

<section class="sec wrap" id="sample">
 <div class="kicker"><b>02</b> 示例 / Specimen</div>
 <div class="sec-h"><h2>先读一篇<em>Read before you sign up</em></h2><p>公版样张，无需登录。滚动驱动场景、人物关系侧栏、说话人标记、粉板可擦除——全部来自同一份 {fmt_num(s['plan_kb'])} KB 的分镜 JSON。</p></div>
 <article class="specimen">
  <div class="spec-page"><div class="fol"><span>¶ 001 / {s['segments']:03d}</span><span>鲁镇 · 咸亨酒店</span></div>
   <p class="first">{first}</p><p class="rest">{rest}</p>
   <svg class="counter" viewBox="0 0 180 96" aria-hidden="true"><path d="M6 88 H174 M18 88 V40 H120 V88 M120 54 H170 V88"/><path class="r" d="M34 40 v-12 h10 v12 M60 40 v-16 h12 v16"/><path d="M134 54 q8 -14 16 0"/></svg>
  </div>
  <div class="spec-meta">
   <h3>孔乙己<small>鲁迅 · 1919 · 公有领域</small></h3>
   <div class="facts"><div><b>{fmt_num(s['chars'])}</b><span>字</span></div><div><b>{s['segments']}</b><span>段</span></div>
    <div><b>{s['scenes']}</b><span>场景</span></div><div><b>{s['visuals']}</b><span>幅插画</span></div></div>
   <div class="acts"><a class="btn" href="/sample/kongyiji">打开阅读页 <span class="arr">→</span></a>{try_btn}</div>
  </div>
 </article>
</section>

<section class="sec wrap" id="upload">
 <div class="kicker"><b>03</b> 上传 / Upload</div>
 <div class="sec-h"><h2>交一篇稿<em>Submit a manuscript</em></h2><p>支持 UTF-8 / GB18030 的 .txt，单次建议 ≤ 20 万字。文本会发送给所选模型做分镜规划；模型只返回 JSON，正文一字不改。</p></div>
 <div class="desk{'' if user else ' locked'}">
  {lock}
  <div class="col">
   {notice}
   <div class="field"><span class="lbl"><i>A</i>原文 · .txt</span>
    <label class="drop" id="drop"><input type="file" id="f" accept=".txt,text/plain" {'' if user else 'tabindex="-1"'}><span class="big" id="fname">拖入或点击选择 txt</span><span class="sm" id="fmeta">自动识别编码 · 只在提交时上传</span></label></div>
   <div class="field row2"><div><label for="title"><i>B</i>标题</label><input class="inp" id="title" maxlength="80" placeholder="留空则用文件名"></div>
    <div><label for="author"><i>C</i>作者</label><input class="inp" id="author" maxlength="60" placeholder="可选"></div></div>
  </div>
  <div class="col">
   <div class="field"><span class="lbl"><i>D</i>分镜来源</span><div class="modes" id="modes">{modes}</div></div>
   <div class="field hide" id="planf"><label class="drop small" id="pdrop"><input type="file" id="pf" accept=".json,application/json"><span class="big" id="pname">选择 plan.json</span><span class="sm">格式见 docs/plan-format.md</span></label></div>
   <div class="field"><span class="lbl"><i>E</i>选项</span><div class="opts">
    <label><input type="checkbox" id="verify" checked> 生成后逐段校验</label>
    <label><input type="checkbox" id="night"> 默认夜间主题</label>
    <label title="每章由 Mercury 挑关键场景，写一段沙箱里的滚动动画；自动校验，不合格回退预制组件"><input type="checkbox" id="hero"> 关键场景动画</label>
    <label class="hero-n">每章 <select id="heroMax" aria-label="每章关键场景动画上限"><option>1</option><option selected>2</option><option>3</option></select> 个</label></div></div>
   <div class="go-row"><button class="btn" id="go" disabled>开始显影 <span class="arr">→</span></button><span class="est" id="est"></span></div>
   <div class="err" id="err" role="alert"></div>
  </div>
 </div>
</section>

<section class="room" id="how">
 <div class="wrap">
  <div class="kicker"><b>04</b> 流程 / Pipeline</div>
  <div class="sec-h"><h2>四格底片<em>Four frames, one print</em></h2><p>每一步都有独立的自检。规划失败会把错误回灌重试一次，再不行用规则修复；渲染后用 Chromium 在 1280 与 390 两种宽度下检查。</p></div>
  <div class="strip">
   <div class="frame"><div class="fno">01 <span>00:00:00:00</span></div><h4>切分<small>Segment</small></h4><p>一个非空行一段，识别章节、日期行与脚注；拼回去必须与原文<b>逐字相同</b>。</p></div>
   <div class="frame"><div class="fno">02 <span>00:00:01:12</span></div><h4>规划<small>Plan</small></h4><p>LLM 只写分镜 JSON：人物、场景、逐段说话人、亲密度 / 紧张 / 脸红 <b>0–1</b> 数值。</p></div>
   <div class="frame"><div class="fno">03 <span>00:00:31:04</span></div><h4>渲染<small>Render</small></h4><p>组件库 + 阅读器运行时，按优先级去重、限密度，输出<b>单个 HTML</b>。</p></div>
   <div class="frame"><div class="fno">04 <span>00:00:33:20</span></div><h4>校验<small>Verify</small></h4><p>逐段比对原文、外链 <b>0</b>、JS 报错 <b>0</b>、两种宽度无横向溢出。</p></div>
  </div>
 </div>
</section>"""


# ------------------------------------------------------------------ auth
def auth(mode, user=None, nxt="/shelf"):
    reg = mode == "register"
    def cur(m): return ' aria-current="page"' if m == mode else ""
    q = f"?next={esc(nxt)}" if nxt and nxt != "/shelf" else ""
    fields = ('<div class="field"><label for="username">用户名</label><input class="inp" id="username" name="username" autocomplete="username" required maxlength="24" autofocus>'
              '<div class="hint">2–24 位：字母、数字、下划线或汉字。会显示在书架上。</div></div>'
              '<div class="field"><label for="email">邮箱 <span style="color:var(--ink3)">· 可选</span></label><input class="inp" id="email" name="email" type="email" autocomplete="email" maxlength="200"></div>'
              '<div class="field"><label for="password">密码</label><input class="inp" id="password" name="password" type="password" autocomplete="new-password" required minlength="8" maxlength="256">'
              '<div class="hint">至少 8 位。密码用 scrypt 加盐哈希保存，任何人（包括管理员）都看不到明文。</div></div>') if reg else (
              '<div class="field"><label for="account">用户名或邮箱</label><input class="inp" id="account" name="account" autocomplete="username" required maxlength="200" autofocus></div>'
              '<div class="field"><label for="password">密码</label><input class="inp" id="password" name="password" type="password" autocomplete="current-password" required maxlength="256"></div>')
    return f"""
<section class="auth">
 <div class="quote">
  <div class="tc">TC 00:04:51:16 <span>REEL 01 · 孔乙己</span><span>¶ 008 / 014</span></div>
  <div><blockquote><span class="q">『</span>多乎哉？<br>不多也。<span class="q">』</span></blockquote>
   <p class="attrib">——鲁迅《孔乙己》，第 8 段。<br>生成需要登录：每本书调用 <b>1–12</b> 次模型，额度有限，所以先认个门。</p></div>
 </div>
 <div class="panel">
  <div class="tabs"><a href="/login{q}"{cur('login')}>登录<small>Sign in</small></a><a href="/register{q}"{cur('register')}>注册<small>Join</small></a></div>
  <form id="authform" data-mode="{mode}" data-next="{esc(nxt)}" novalidate>
   {fields}
   <button class="btn" type="submit">{'创建账号' if reg else '登录'} <span class="arr">→</span></button>
   <div class="err" id="err" role="alert"></div>
  </form>
  <p class="alt">{'已经有账号？<a class="link" href="/login' + q + '">去登录</a>' if reg else '还没有账号？<a class="link" href="/register' + q + '">注册一个</a>，30 秒。'}</p>
 </div>
</section>"""


# ------------------------------------------------------------------ shelf
STATUS_LABEL = {"done": "已完成", "running": "显影中", "queued": "排队中", "failed": "失败"}


def book_card(b, i, job_id=None, llm_any=False):
    v = json.loads(b["verify_json"]) if b["verify_json"] else None
    vtxt = (f"{v.get('matched')}/{v.get('segments')}" if v and "matched" in v else ("未校验" if b["status"] == "done" else "—"))
    secs = f"{b['seconds']:.1f}" if b["seconds"] is not None else "—"
    model = b["model"] or "—"
    vis = "链接可见" if b["visibility"] == "unlisted" else "仅自己"
    busy = b["status"] in ("queued", "running")
    open_a = (f'<a href="/b/{esc(b["id"])}" target="_blank" rel="noopener">打开</a>' if b["status"] == "done" or b["html_path"] else
              (f'<a href="/jobs/{esc(job_id)}">进度</a>' if job_id else '<a aria-disabled="true">打开</a>'))
    if busy and job_id:
        open_a = f'<a href="/jobs/{esc(job_id)}">看进度</a>'
    err = f'<div class="err-line">{esc(b["error"])}</div>' if b["status"] == "failed" and b["error"] else ""
    return f"""<article class="book" data-book="{esc(b['id'])}" data-vis="{esc(b['visibility'])}" data-hasplan="{1 if b['plan_json'] else 0}">
 <div class="top"><span>No.{i:02d} · {fmt_time(b['created_at'])}</span><span class="stamp {esc(b['status'])}">{STATUS_LABEL.get(b['status'], b['status'])}</span></div>
 <h3><a href="{'/b/' + esc(b['id']) if b['html_path'] else ('/jobs/' + esc(job_id) if job_id else '#')}">{esc(b['title'])}</a></h3>
 <div class="by">{esc(b['author'] or '')}</div>
 <div class="nums"><div><b>{fmt_num(b['char_count'])}</b><span>字</span></div><div><b>{b['segments'] or '—'}</b><span>段</span></div><div><b>{vtxt}</b><span>校验一致</span></div></div>
 <div class="meta"><span>{esc(model)} · {secs}s</span><span class="vis {esc(b['visibility'])}">{vis}</span></div>
 {err}
 <div class="acts">{open_a}<button type="button" data-act="rebuild" {'aria-disabled="true"' if busy else ''}>重新生成</button><button type="button" data-act="share">分享</button><button type="button" class="del" data-act="delete" {'aria-disabled="true"' if busy else ''}>删除</button></div>
</article>"""


def shelf(user, books, jobs_by_book, llm_any):
    total = sum(b["char_count"] for b in books)
    done = sum(1 for b in books if b["status"] == "done")
    last = fmt_time(max((b["created_at"] for b in books), default=None))
    cards = "".join(book_card(b, len(books) - k, jobs_by_book.get(b["id"]), llm_any) for k, b in enumerate(books))
    body = (f'<div class="books" id="books">{cards}</div>' if books else
            """<div class="empty"><div class="zero">0</div><div><h3>书架还是空的。</h3><p>从一篇短篇开始：3,000 字以内的文本，plan.json 模式 2 秒、模型模式约 30 秒。</p>
<div class="acts"><a class="btn" href="/#upload">交第一篇稿 <span class="arr">→</span></a><button class="link" type="button" data-try-sample style="background:none;border:0;border-bottom:1px solid var(--cinnabar);cursor:pointer">用《孔乙己》试一次</button></div></div></div>""")
    return f"""
<section class="shelf-head wrap">
 <div class="kicker"><b>@{esc(user['username'])}</b> 我的书架 / My shelf</div>
 <h1>书架<em>{len(books)} volume{'s' if len(books) != 1 else ''}</em></h1>
 <div class="tally"><div><b>{len(books)}</b><span>本书</span></div><div><b>{fmt_num(total)}</b><span>字原文</span></div><div><b>{done}</b><span>本已完成</span></div><div><b class="mono" style="font-size:20px;line-height:1.9">{last}</b><span>最近生成</span></div></div>
 {body}
 <details class="account"><summary>账号 · 注销 / Delete account</summary>
  <form id="delacct"><input class="inp" type="password" id="delpw" autocomplete="current-password" placeholder="输入密码确认：删除账号与全部书" required>
  <button class="btn sm danger" type="submit">永久删除账号</button></form><div class="err" id="delerr"></div></details>
</section>"""


# ------------------------------------------------------------------ job progress (darkroom)
def job_page(user, job, book):
    frames = "".join(
        f'<div class="frame" data-step="{k}"><div class="fno">{i:02d} <span class="state">等待</span></div><h4>{zh}<small>{en}</small></h4><p data-note></p></div>'
        for i, (k, zh, en) in enumerate(STEPS, 1))
    return f"""
<section class="dev wrap" id="dev" data-job="{esc(job['id'])}" data-book="{esc(book['id'])}" data-created="{job['created_at']}">
 <div class="kicker"><b>暗房</b> 生成进度 / Developing</div>
 <div class="dev-head"><h1><em>Now developing</em>《{esc(book['title'])}》</h1>
  <div class="timecode" id="tc">00:00:00:00<small>TC · 24 FPS · {fmt_num(book['char_count'])} 字</small></div></div>
 <div class="meter"><i id="meter"></i><span id="pct">0%</span></div>
 <div class="strip steps" id="steps">{frames}</div>
 <div class="dev-body">
  <div><div class="kicker" style="margin-bottom:14px"><b>LOG</b> 日志</div><div class="console" id="console" aria-live="polite"><span class="cursor"></span></div></div>
  <div class="result" id="result"><h3>显影中…<small>Developing — keep this page open or come back later</small></h3>
   <p class="wait">可以离开这一页：任务在服务器上运行，进度与日志写在数据库里，回到<a class="link" href="/shelf" style="color:var(--room-text)">书架</a>随时能看。</p></div>
 </div>
</section>"""


def message(title, big, text, user=None, link=("/", "回首页"), code=""):
    return layout(title, f"""<section class="plain wrap"><div class="kicker"><b>{esc(code)}</b> {esc(title)}</div>
<h1>{esc(big)}<small>{esc(code)}</small></h1><p>{text}</p><a class="btn" href="{esc(link[0])}">{esc(link[1])} <span class="arr">→</span></a></section>""", user)
