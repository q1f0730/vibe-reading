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
               f'<a href="/#upload" class="hide-s">新建</a><a href="/settings"{cur("settings")}>模型</a><span class="who" title="{esc(user["username"])}">@{esc(user["username"])}</span>'
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
 {_quality_line(b)}
 {err}
 <div class="acts">{open_a}<button type="button" data-act="rebuild" {'aria-disabled="true"' if busy else ''}>重新生成</button><button type="button" data-act="share">分享</button><button type="button" class="del" data-act="delete" {'aria-disabled="true"' if busy else ''}>删除</button></div>
</article>"""


def _quality_line(b):
    try:
        q = json.loads(b["quality_json"]) if b["quality_json"] else None
    except Exception:
        q = None
    if not q:
        return ""
    return (f'<div class="qline"><a href="/b/{esc(b["id"])}/quality">质量 {esc(q.get("overall"))} · {esc(q.get("tier") or "—")} 档 · '
            f'降级 {round(100 * (q.get("degraded_paragraph_ratio") or 0), 1)}% · 下限{"✓" if q.get("floor_ok") else "✕"}</a></div>')


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


# ------------------------------------------------------------------ model settings
KIND_LABEL = {"openai": "OpenAI 兼容（含 DeepSeek / 千问 / Mercury / OpenRouter / Gemini 兼容端点…）", "anthropic": "Anthropic 原生（/v1/messages）",
              "gemini": "Gemini 原生（generateContent）"}
PROBE_LABEL = [("ping", "连通 / 延迟"), ("json_mode", "JSON 模式"), ("json_schema", "结构化输出"), ("json_prompt", "提示词 JSON"),
               ("tools", "工具调用"), ("max_output", "最大输出"), ("context", "上下文"), ("zh_long", "中文长文本"), ("filter", "内容过滤倾向")]
TIER_TEXT = {"A": "强：大批次、少约束、允许多镜头动画与（可选）代码模式",
             "B": "中：中等批次、schema/enum 约束、两轮修复",
             "C": "弱：小批次、最严约束 + few-shot、规则兜底最多、动画只用两镜头"}


def _probe_cell(name, p):
    if not p:
        return "—"
    if p.get("filtered"):
        return "被过滤"
    if p.get("error"):
        return "✕ " + esc(p["error"][:60])
    extra = {"ping": lambda: f"{p.get('latency')}s" + (" · 推理模型" if p.get("reasoning") else ""),
             "max_output": lambda: f"≥{p.get('max_output_ok')} token",
             "context": lambda: f"≥{p.get('context_ok')} token" if p.get("ok") else "< 8k",
             "zh_long": lambda: f"{p.get('score')} 分",
             "filter": lambda: f"误伤 {p.get('blocked')}/{p.get('of')}"}.get(name)
    return ("✓" if p.get("ok") else "✕") + (" " + esc(extra()) if extra else "")


def settings(user, v, crypto_ok, platform):
    c = (v or {}).get("config") or {}
    prof = (v or {}).get("profile") or {}
    def sel(name, val, opts):
        return f'<select class="inp" id="m_{name}" name="{name}">' + "".join(
            f'<option value="{esc(k)}"{" selected" if str(val) == str(k) else ""}>{esc(t)}</option>' for k, t in opts) + "</select>"
    status = ""
    if v:
        st = {"probing": "探针运行中（约 10–60 秒）…", "ready": f"已通过探针 · <b>{esc(v['tier'])} 档</b> · {esc(TIER_TEXT.get(v['tier'], ''))}",
              "failed": "探针未通过：" + esc(v.get("error") or "")}[v["status"]]
        rows = "".join(f'<tr><th>{esc(lab)}</th><td>{_probe_cell(k, (prof.get("probes") or {}).get(k))}</td></tr>' for k, lab in PROBE_LABEL) if prof else ""
        reasons = "".join(f"<li>{esc(x)}</li>" for x in prof.get("tier_reasons") or [])
        adapt = "".join(f"<li>{esc(x)}</li>" for x in prof.get("adaptations") or [])
        status = f"""<div class="mstatus" id="mstatus" data-status="{esc(v['status'])}"><div class="kicker"><b>{esc(v['tier'] or '…')}</b> 能力档案 / Profile</div>
<p>{status}</p>{'<table class="qtable">' + rows + '</table>' if rows else ''}
{('<p class="hint">分档依据：</p><ul class="hint">' + reasons + '</ul>') if reasons else ''}{('<p class="hint">自动适配：</p><ul class="hint">' + adapt + '</ul>') if adapt else ''}</div>"""
    plat = "、".join(k for k, ok in platform.items() if ok) or "无"
    warn = "" if crypto_ok else '<div class="notice"><b>服务器没有配置加密密钥（VR_SECRET_KEY）。</b>为了不明文保存 key，暂时不能保存自己的模型；「测试」仍可用。</div>'
    return f"""
<section class="plain wrap settings">
 <div class="kicker"><b>@{esc(user['username'])}</b> 模型设置 / Your model</div>
 <h1>用你自己的模型<small>BYO model</small></h1>
 <p>填一个你自己的模型（OpenAI 兼容 / Anthropic / Gemini）。保存后会自动跑一组小探测（约 11 次很短的调用），按能力分成 A / B / C 三档，
 生成时按档位调整批次、约束和兜底：<b>不管接什么模型，原文逐字、校验与兜底都一样</b>，弱模型只是更多交给规则。
 没有配置时用平台默认模型（当前可用：{esc(plat)}）。</p>
 {warn}
 <form id="mform" class="mform" autocomplete="off" novalidate>
  <div class="field"><label for="m_kind">接口类型</label>{sel('kind', c.get('kind', 'openai'), list(KIND_LABEL.items()))}</div>
  <div class="field"><label for="m_base_url">Base URL</label><input class="inp" id="m_base_url" name="base_url" maxlength="300" required value="{esc(c.get('base_url', ''))}" placeholder="https://api.deepseek.com/v1">
   <div class="hint">OpenAI 兼容填到 /v1 为止；Anthropic 如 https://api.anthropic.com；Gemini 如 https://generativelanguage.googleapis.com/v1beta。</div></div>
  <div class="field row2"><div><label for="m_model">模型名</label><input class="inp" id="m_model" name="model" maxlength="120" required value="{esc(c.get('model', ''))}" placeholder="deepseek-chat"></div>
   <div><label for="m_label">显示名 <span style="color:var(--ink3)">· 可选</span></label><input class="inp" id="m_label" name="label" maxlength="60" value="{esc(c.get('label', ''))}"></div></div>
  <div class="field"><label for="m_api_key">API key</label><input class="inp" id="m_api_key" name="api_key" type="password" autocomplete="new-password" maxlength="4000"
   placeholder="{('已保存 ' + esc(v['key']) + ' · 留空不改') if v and v.get('has_key') else 'sk-…'}">
   <div class="hint">只在服务器上用 AES-256-GCM 加密保存；页面、日志、报错里都不会出现明文。</div></div>
  <div class="field row2"><div><label for="m_reasoning">推理强度</label>{sel('reasoning', c.get('reasoning', 'auto'), [("auto", "自动（推荐）"), ("off", "关闭"), ("minimal", "最低"), ("low", "低"), ("medium", "中"), ("high", "高"), ("instant", "instant（Mercury）")])}
   <div class="hint">推理吃光输出上限时会自动降档 / 关闭。</div></div>
   <div><label for="m_concurrency">并发上限</label><input class="inp" id="m_concurrency" name="concurrency" type="number" min="1" max="16" value="{esc(c.get('concurrency', 3))}"></div></div>
  <div class="field row2"><div><label for="m_timeout">单次超时（秒）</label><input class="inp" id="m_timeout" name="timeout" type="number" min="5" max="600" value="{esc(c.get('timeout', 90))}"></div>
   <div><label for="m_max_total_seconds">网关总时长上限（秒，0 不限）</label><input class="inp" id="m_max_total_seconds" name="max_total_seconds" type="number" min="0" max="900" value="{esc(c.get('max_total_seconds', 0))}"></div></div>
  <div class="opts"><label><input type="checkbox" id="m_stream" name="stream" {'checked' if c.get('stream') else ''}> 流式输出（长输出 / 有网关时长限制时更稳）</label></div>
  <div class="go-row"><button class="btn ghost" type="button" id="mtest">测试</button><button class="btn" type="submit" id="msave" {'' if crypto_ok else 'disabled'}>保存并探测 <span class="arr">→</span></button>
   {'<button class="btn ghost" type="button" id="mdel">删除，改回平台默认</button>' if v else ''}</div>
  <div class="err" id="merr" role="alert"></div><div class="hint" id="mres" role="status"></div>
 </form>
 {status}
</section>"""


# ------------------------------------------------------------------ quality report (author only)
SCORE_LABEL = [("schema", "schema 合格率"), ("schema_first_pass", "首次合格率"), ("verbatim", "原文逐字一致"), ("coverage", "模型分镜覆盖（100−降级）"),
               ("scenes", "场景分布"), ("visual_consistency", "组件与原文一致"), ("diversity", "组件/原语多样性"), ("hero", "关键场景动画")]


def _fmt(x, suf=""):
    return "—" if x is None else f"{x}{suf}"


def quality_page(user, b, q):
    if not q:
        return f'<section class="plain wrap"><div class="kicker"><b>QA</b> 质量报告</div><h1>还没有质量报告<small>No report</small></h1><p>这本书是旧版本生成的，重新生成一次就会附上。</p><a class="btn" href="/shelf">回书架 <span class="arr">→</span></a></section>'
    sc = q.get("scores") or {}
    rows = "".join(f"<tr><th>{esc(lab)}</th><td>{_fmt(sc.get(k))}</td></tr>" for k, lab in SCORE_LABEL)
    fl = q.get("floor") or {}
    FL = {"verbatim_100": "原文 100% 逐字一致", "no_missing_text": "没有漏掉任何原文段", "plan_complete": "分镜完整（每段都有、场景首尾相接）",
          "visuals_consistent_after_guard": "页面上的组件都与原文一致", "heroes_render_ok": "关键场景动画全部通过实测或已回退", "browser_verify": "浏览器校验（外链 / JS 报错 / 溢出）"}
    frows = "".join(f"<tr><th>{esc(FL.get(k, k))}</th><td>{'✓' if v else ('✕' if v is False else '—')}</td></tr>" for k, v in fl.items())
    sd = q.get("scene_distribution") or {}; dv = q.get("diversity") or {}; hs = q.get("hero") or {}; cost = q.get("cost") or {}; g = q.get("guard") or {}
    facts = [("模型", q.get("model_label") or q.get("model")), ("档位", q.get("tier")), ("来源", {"user": "你自己的模型", "platform": "平台默认", "eval": "评测"}.get(q.get("source"), q.get("source"))),
             ("降级段落", f"{q.get('rule_paragraphs', 0)} 段（{round(100 * (q.get('degraded_paragraph_ratio') or 0), 1)}%）"),
             ("场景", f"{sd.get('scenes')} 个 · 每场景 {sd.get('min')}–{sd.get('max')} 段（中位 {sd.get('median')}）"),
             ("组件", f"{dv.get('visuals')} 个 / {dv.get('visual_types')} 种；护栏去掉 {g.get('inconsistent', 0)} 个不符 + {g.get('capped', 0)} 个超量"),
             ("关键场景动画", f"{hs.get('count', 0)} 个 · {json.dumps(hs.get('status') or {}, ensure_ascii=False)}" + (f" · 意象一致 {hs.get('imagery_consistency')}" if hs.get("imagery_consistency") is not None else "")),
             ("调用", f"{cost.get('calls')} 次 · 输出 {cost.get('tokens_out')} token · 重试 {cost.get('retries')} · 限流退避 {cost.get('transient')} · 内容过滤 {cost.get('content_filtered')}"),
             ("耗时", f"{q.get('seconds')} 秒")]
    frow = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in facts)
    adapt = "".join(f"<li>{esc(x)}</li>" for x in q.get("adaptations") or [])
    return f"""
<section class="plain wrap quality">
 <div class="kicker"><b>QA</b> 质量报告 · 只有你能看到 / Author only</div>
 <h1>《{esc(b['title'])}》<small>总分 {_fmt(q.get('overall'))} · {esc(q.get('tier') or '—')} 档 · 下限{'守住' if q.get('floor_ok') else '未守住'}</small></h1>
 <div class="qgrid"><div><h3>各项得分</h3><table class="qtable">{rows}</table></div>
 <div><h3>下限检查</h3><table class="qtable">{frows}</table></div></div>
 <h3>明细</h3><table class="qtable wide">{frow}</table>
 {('<h3>自动适配</h3><ul class="hint">' + adapt + '</ul>') if adapt else ''}
 <div class="acts" style="margin-top:28px"><a class="btn" href="/b/{esc(b['id'])}">打开阅读页 <span class="arr">→</span></a><a class="btn ghost" href="/shelf">回书架</a></div>
</section>"""


def inject_author_badge(body, b):
    """只给书的主人：阅读页右下角一个质量徽标（链接到报告页）。读者 / 分享链接拿到的是原文件。"""
    try:
        q = json.loads(b["quality_json"])
    except Exception:
        return body
    ok = q.get("floor_ok")
    badge = (f'<a href="/b/{esc(b["id"])}/quality" target="_top" title="质量报告（只有你能看到）" '
             f'style="position:fixed;right:14px;bottom:14px;z-index:2147483000;font:12px/1.2 ui-monospace,Menlo,monospace;letter-spacing:.06em;'
             f'background:#1a1a1a;color:#faf9f7;padding:8px 11px;text-decoration:none;border-left:3px solid {"#3d7a4a" if ok else "#c41e3a"}">'
             f'作者视图 · 质量 {esc(q.get("overall"))} · {esc(q.get("tier") or "—")} 档</a>').encode()
    i = body.rfind(b"</body>")
    return body[:i] + badge + body[i:] if i >= 0 else body + badge
