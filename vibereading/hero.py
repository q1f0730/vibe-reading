"""Stage 2.5 · hero scenes：每章挑 1–3 个关键场景，由 LLM 写一段"专属动画模块"。

流程（每章）：
  1. select：给 LLM 本章的场景表 + 分镜数值（紧张/亲密/脸红曲线、已有组件）+ 每场景几句**截断的短摘**，
     让它选关键场景并写"场景描述"（摘要、物件、人物、情绪曲线、视觉隐喻、时长、回退组件）。
     被内容过滤拒答 → 去掉短摘只给元数据再试一次 → 仍拒 → 本章不做 hero。
  2. generate：只把"场景描述"（不含原文）交给 LLM，生成自包含的动画模块（HTML/CSS/SVG/Canvas/JS 片段）。
  3. check：静态检查（体积 / 外链 / 禁用 API / 必须订阅滚动进度）→ 无头 Chromium 在沙箱 iframe 里实测
     （JS 报错、外部请求/CSP、横向溢出、帧率与长任务、明暗主题都非空白、随滚动变化、减弱动效下静止）。
  4. 不合格 → 带错误信息让 LLM 修一次 → 仍不合格（或被内容过滤拒答）→ 回退为预制组件。

模块在页面里以 <iframe sandbox="allow-scripts" srcdoc> 运行（不可同源、无网络：CSP default-src 'none'），
父页面用 postMessage 传滚动进度 p∈[0,1] 与主题。原文段落的渲染与逐段校验完全不经过这里。
"""
import concurrent.futures as cf
import html as _html
import json, os, pathlib, re, subprocess, tempfile, time, urllib.error

from . import planner
from . import hero_prims as HP
from .registry import COMPONENTS, prompt_catalog

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_BYTES = int(os.environ.get("VR_HERO_MAX_BYTES", 40 * 1024))
DEFAULT_PER_CHAPTER = 2
HERO_MAX_PER_CHAPTER = 3
MIN_GAP = 6          # 两个 hero 之间至少隔几段（太近会连成一大片动画，读者没法读正文）
EFFORT = os.environ.get("VR_HERO_EFFORT", "instant")      # mercury reasoning_effort for module generation
MODES = ("prims", "code")
DEFAULT_MODE = os.environ.get("VR_HERO_MODE", "prims") if os.environ.get("VR_HERO_MODE", "prims") in MODES else "prims"

PAPER = {"bg": "#faf9f7", "bg2": "#f3f0ea", "ink": "#1a1a1a", "ink2": "#5e5e5e", "rule": "#d9d4ca", "accent": "#c41e3a", "heat": "#ff6a3d"}
NIGHT = {"bg": "#15171b", "bg2": "#1d2025", "ink": "#e4ded3", "ink2": "#a39d92", "rule": "#3a4047", "accent": "#e06a7d", "heat": "#ff8a5c"}

# ------------------------------------------------------------------ sandbox shell
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: blob:; "
       "font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'")

RUNTIME_JS = r"""(function(){
var subs=[],tsubs=[],st={p:0,theme:'paper',reduced:false};
try{st.reduced=matchMedia('(prefers-reduced-motion: reduce)').matches}catch(e){}
function post(m){try{parent.postMessage(Object.assign({vrh:1},m),'*')}catch(e){}}
function rep(e,ln,col){var m=/srcdoc:(\d+):(\d+)/.exec((e&&e.stack)||'');post({t:'error',msg:String((e&&e.message)||e).slice(0,300),line:m?+m[1]:(ln||0),col:m?+m[2]:(col||0)})}
function call(f,v){try{f(v)}catch(e){rep(e)}}
var clamp=function(x,a,b){a=a===undefined?0:a;b=b===undefined?1:b;return x<a?a:x>b?b:x};
window.VRH={
 get progress(){return st.p},get theme(){return st.theme},get reduced(){return st.reduced},
 onProgress:function(f){if(typeof f!=='function'){rep(new TypeError('VRH.onProgress 需要传入函数，收到 '+typeof f));return}subs.push(f);call(f,st.p)},
 onTheme:function(f){if(typeof f!=='function'){rep(new TypeError('VRH.onTheme 需要传入函数，收到 '+typeof f));return}tsubs.push(f);call(f,st.theme)},
 clamp:clamp,lerp:function(a,b,t){return a+(b-a)*t},
 seg:function(p,a,b){return clamp((p-a)/(b-a))},
 ease:function(t){t=clamp(t);return t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2},
 color:function(name){return getComputedStyle(document.documentElement).getPropertyValue('--'+name).trim()},
 prep:function(root){var els=(typeof root==='string'?document.querySelector(root):root||document).querySelectorAll('[data-draw],[data-draw] path,[data-draw] line,[data-draw] polyline,[data-draw] polygon,[data-draw] rect,[data-draw] circle,[data-draw] ellipse');
  for(var i=0;i<els.length;i++){var e=els[i];if(e.getTotalLength&&!e.__L){try{var L=Math.ceil(e.getTotalLength())+1;e.__L=L;e.style.strokeDasharray=L;e.style.strokeDashoffset=L}catch(x){}}}},
 draw:function(sel,t){t=clamp(t);var els=typeof sel==='string'?document.querySelectorAll(sel):(sel&&sel.length!==undefined?sel:[sel]);
  for(var i=0;i<els.length;i++){var g=els[i];if(!g)continue;var kids=g.__L?[g]:g.querySelectorAll('*');for(var j=0;j<kids.length;j++){var e=kids[j];if(e.__L)e.style.strokeDashoffset=e.__L*(1-t)}}},
 camera:function(svg,a,b,t){if(typeof svg==='string')svg=document.querySelector(svg);if(!svg)return;t=clamp(t);var v=[0,1,2,3].map(function(i){return a[i]+(b[i]-a[i])*t});svg.setAttribute('viewBox',v.join(' '))},
 beat:function(p,ts){var k=0;for(var i=0;i<ts.length;i++)if(p>=ts[i])k=i;var a=ts[k],b=k+1<ts.length?ts[k+1]:1;return {i:k,t:b>a?clamp((p-a)/(b-a)):1}}
};
addEventListener('error',function(e){rep(e.error||e.message,e.lineno,e.colno)});
addEventListener('unhandledrejection',function(e){rep(e.reason)});
document.addEventListener('securitypolicyviolation',function(e){post({t:'csp',uri:String(e.blockedURI).slice(0,200),dir:e.violatedDirective})});
var perf=null,lt=[];
try{new PerformanceObserver(function(l){l.getEntries().forEach(function(x){lt.push(x.duration)})}).observe({type:'longtask',buffered:true})}catch(e){}
function setTheme(t){if(t!==st.theme){st.theme=t;document.documentElement.setAttribute('data-theme',t);tsubs.forEach(function(f){call(f,t)})}}
addEventListener('message',function(e){
 if(e.source!==parent)return;var d=e.data||{};if(!d||!d.vrh)return;
 if(d.theme)setTheme(d.theme==='night'?'night':'paper');
 if(typeof d.p==='number'&&isFinite(d.p)){var p=clamp(d.p);if(p!==st.p||d.force){st.p=p;subs.forEach(function(f){call(f,p)})}}
 if(d.t==='perf-start'){lt=[];perf={dts:[],last:performance.now(),on:true};(function f(now){if(!perf||!perf.on)return;perf.dts.push(now-perf.last);perf.last=now;requestAnimationFrame(f)})(performance.now())}
 if(d.t==='perf-stop'&&perf){perf.on=false;var a=perf.dts.slice(1).sort(function(x,y){return x-y});
  post({t:'perf',frames:a.length,p95:a.length?a[Math.floor(a.length*.95)]:0,max:a.length?a[a.length-1]:0,longtasks:lt.length,longtask_ms:Math.round(lt.reduce(function(s,x){return s+x},0)),longtask_max:Math.round(Math.max.apply(null,[0].concat(lt)))});perf=null}
 if(d.t==='probe'){var de=document.documentElement,b=document.body;
  post({t:'probe',sw:Math.max(de.scrollWidth,b?b.scrollWidth:0),iw:innerWidth,sh:de.scrollHeight,ih:innerHeight,nodes:document.getElementsByTagName('*').length})}
});
document.documentElement.setAttribute('data-theme','paper');
addEventListener('load',function(){post({t:'ready'})});
})();"""


def theme_css(chars):
    def block(pal, night):
        v = [f"--{k}:{c};" for k, c in pal.items()]
        for c in chars:
            v.append(f"--c-{c['id']}:{c.get('night') if night else c.get('color')};")
        return "".join(v)
    return (f":root{{{block(PAPER, False)}color-scheme:light}}"
            f":root[data-theme=night]{{{block(NIGHT, True)}color-scheme:dark}}"
            "html,body{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--ink);"
            "font-family:'Noto Serif CJK SC','Source Han Serif SC','Songti SC',serif;-webkit-text-size-adjust:100%}"
            "*,*::before,*::after{box-sizing:border-box}")


def wrap(module, chars):
    """模块片段 → 完整 srcdoc 文档（CSP + 主题变量 + VRH 运行时）。"""
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP}\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<style>{theme_css(chars)}</style><script>{RUNTIME_JS}</script></head><body>{module}</body></html>")


# ------------------------------------------------------------------ static checks
FORBIDDEN = [
    (r"https?:\s*//", "出现了 http(s):// 外部地址（模块必须零外部资源）"),
    (r"(?:src|href|action|poster|srcset)\s*=\s*[\"']?\s*//", "出现了 // 协议相对外链"),
    (r"url\(\s*[\"']?\s*(?!data:|#)[a-z]*:?//", "CSS url() 指向外部地址"),
    (r"@import", "使用了 @import"),
    (r"<\s*(?:link|iframe|frame|object|embed|base|form|meta|audio|video)\b", "使用了禁用标签（link/iframe/object/embed/base/form/meta/audio/video）"),
    (r"<\s*script[^>]*\bsrc\s*=", "使用了外部 <script src>"),
    (r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|SharedWorker|Worker)\s*\(", "使用了网络/Worker API"),
    (r"\bnew\s+(?:XMLHttpRequest|WebSocket|EventSource|Worker|SharedWorker)\b", "使用了网络/Worker API"),
    (r"\bimport\s*\(", "使用了动态 import()"),
    (r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie|caches)\b", "使用了存储 API（沙箱里会直接报错）"),
    (r"\b(?:window\.)?(?:top|opener)\s*\.\s*(?:location|document|postMessage)", "访问了 top/opener"),
    (r"\bparent\s*\.\s*(?:document|location|window)", "访问了父页面"),
    (r"\bwindow\.open\s*\(|\blocation\s*(?:\.href)?\s*=", "试图打开窗口或跳转"),
    (r"\b(?:eval|Function)\s*\(\s*[\"'`]|\bnew\s+Function\b", "使用了 eval / new Function"),
    (r"\balert\s*\(|\bconfirm\s*\(|\bprompt\s*\(", "使用了 alert/confirm/prompt"),
]


def extract_module(raw):
    """从 LLM 输出里取出模块片段：优先 ```html 代码块；去掉 <!DOCTYPE>/<html>/<head>/<body> 外壳。"""
    s = raw or ""
    m = re.findall(r"```(?:html|HTML)?\s*\n(.*?)```", s, flags=re.S)
    if m:
        s = max(m, key=len)
    elif "```" in s:   # 未闭合的代码块（被截断）
        s = s.split("```", 1)[1]
        s = re.sub(r"^(?:html|HTML)?\s*\n", "", s)
    s = re.sub(r"<!DOCTYPE[^>]*>", "", s, flags=re.I)
    s = re.sub(r"</?(?:html|body)\b[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<head\b[^>]*>(.*?)</head>", lambda mm: "\n".join(re.findall(r"<style\b.*?</style>", mm.group(1), flags=re.S | re.I)),
               s, flags=re.S | re.I)
    s = re.sub(r"<meta\b[^>]*>|<title>.*?</title>", "", s, flags=re.I | re.S)
    # 常见错误自动修：<canvas> 写在 <svg> 里会变成 SVG 命名空间元素（没有 getContext）→ 挪到 </svg> 后面
    def _lift(mm):
        block = mm.group(0)
        cvs = re.findall(r"<canvas\b.*?</canvas>", block, flags=re.S | re.I)
        if not cvs:
            return block
        block = re.sub(r"<canvas\b.*?</canvas>", "", block, flags=re.S | re.I)
        return block + "".join(cvs)
    s = re.sub(r"<svg\b.*?</svg>", _lift, s, flags=re.S | re.I)
    # SVG 命名空间属性在 HTML 里是多余的，而且会被外链检查误判
    s = re.sub(r'\s+xmlns(?::\w+)?\s*=\s*["\']https?://www\.w3\.org/[^"\']*["\']', "", s)
    return s.strip()


def static_check(module):
    errs = []
    b = len(module.encode("utf-8"))
    if b == 0:
        return ["模块为空"]
    if b > MAX_BYTES:
        errs.append(f"体积 {b // 1024}KB 超过上限 {MAX_BYTES // 1024}KB，请精简（复用函数生成重复图形，不要逐个手写坐标）")
    for pat, why in FORBIDDEN:
        mm = re.search(pat, module, flags=re.I)
        if mm:
            errs.append(f"{why}：…{module[max(0, mm.start() - 20):mm.end() + 20]!r}…"[:200])
    if "VRH.onProgress" not in module:
        errs.append("没有调用 VRH.onProgress(fn) 订阅滚动进度：动画必须由滚动进度 p 驱动")
    if "<script" not in module.lower():
        errs.append("缺少 <script>：需要用 JS 根据进度 p 更新画面")
    if not re.search(r"<\s*(?:svg|canvas|div)\b", module, flags=re.I):
        errs.append("没有任何可见元素（svg/canvas/div）")
    if module.count("<script") != module.count("</script>"):
        errs.append("<script> 标签未闭合（输出可能被截断）")
    return errs


# ------------------------------------------------------------------ browser checks
CHECK_JS = ROOT / "tools" / "hero_check.js"


def browser_available():
    return CHECK_JS.exists() and (ROOT / "tools/node_modules/puppeteer-core").exists() and \
        os.path.exists(os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"))


def browser_check(docs, timeout=240):
    """docs: {id: srcdoc}. 返回 {id: {"ok": bool, "errors": [...], "metrics": {...}}}。无浏览器时返回 None。"""
    if not browser_available():
        return None
    with tempfile.TemporaryDirectory(prefix="vrhero-") as d:
        inp = pathlib.Path(d) / "in.json"
        inp.write_text(json.dumps([{"id": k, "doc": v} for k, v in docs.items()], ensure_ascii=False), encoding="utf-8")
        try:
            r = subprocess.run(["node", str(CHECK_JS), str(inp)], capture_output=True, text=True, timeout=timeout)
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as e:
            return {k: {"ok": False, "errors": [f"校验器异常：{type(e).__name__}: {str(e)[:200]}"], "metrics": {}, "infra": True} for k in docs}
    return {x["id"]: x for x in res}


# ------------------------------------------------------------------ prompts
SELECT_SCHEMA = {
    "type": "object", "required": ["heroes"],
    "properties": {"heroes": {"type": "array", "maxItems": HERO_MAX_PER_CHAPTER, "items": {
        "type": "object", "required": ["scene", "at", "title", "summary", "objects", "curve", "motif", "seconds"],
        "properties": {
            "scene": {"type": "integer"}, "at": {"type": "integer"},
            "title": {"type": "string", "maxLength": 14}, "caption": {"type": "string", "maxLength": 40},
            "summary": {"type": "string", "maxLength": 220}, "setting": {"type": "string", "maxLength": 80},
            "objects": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 16}},
            "characters": {"type": "array", "maxItems": 4, "items": {"type": "object", "required": ["id"],
                           "properties": {"id": {"type": "string"}, "role": {"type": "string", "maxLength": 30}}}},
            "curve": {"type": "array", "minItems": 3, "maxItems": 6, "items": {
                "type": "object", "required": ["t", "beat"],
                "properties": {"t": {"type": "number", "minimum": 0, "maximum": 1}, "beat": {"type": "string", "maxLength": 30},
                               "tension": {"type": "number", "minimum": 0, "maximum": 1},
                               "intimacy": {"type": "number", "minimum": 0, "maximum": 1}}}},
            "motif": {"type": "string", "maxLength": 60}, "seconds": {"type": "number", "minimum": 8, "maximum": 20},
            "tone": {"enum": ["day", "dim", "night", "memory"]},
            "fallback": {"type": "object", "required": ["type"], "properties": {"type": {"enum": list(COMPONENTS)}, "params": {"type": "object"}}},
        }}}}}

SELECT_SYS = """你是图文长篇（NYT / The Pudding 式滚动叙事）的视觉编辑。你只输出 JSON，绝不复述、续写或引用原文句子。
给你一章小说的分镜摘要：场景表（段号范围、时间地点、基调）、每场景的情绪数值（紧张 te / 亲密 in / 脸红 bl 的峰值与走向）、
已经安排的预制组件，以及少量截断的短摘（只为让你知道发生了什么）。
请从本章选出最多 {k} 个最值得做“专属长动画”的关键场景：优先情绪峰值、关系转折、有强烈空间/物件意象的时刻（门、车内、灯、手、距离变化）。
每个场景写一份给动画师看的“场景描述”，用你自己的话转述，不要摘抄原文：
{{"heroes":[{{"scene":场景序号,"at":动画插在哪一段之后(该场景内的段号，通常是转折发生的那段),
 "title":"≤10字 动画标题","caption":"≤30字 图注（转述，不引原文）",
 "summary":"≤150字 场景摘要：谁、在哪、发生了什么变化",
 "setting":"空间、时间、光线，如 冬日傍晚的厨房，顶灯偏黄",
 "objects":["画面里要出现的 3–6 个具体物件"],
 "characters":[{{"id":"人物id","role":"在画面里的位置/动作"}}],
 "curve":[{{"t":0.0,"beat":"开场画面","tension":0.3,"intimacy":0.2}}, …3–6 个节拍，t 从 0 到 1 递增，对应滚动进度],
 "motif":"核心视觉隐喻，如 两人之间的距离随滚动缩短、门缝里的光",
 "seconds":8到20(这段动画值得读者滚动多少秒),
 "tone":"day|dim|night|memory",
 "fallback":{{"type":"预制组件名","params":{{…}}}}}}]}}
fallback 从下列预制组件里选一个最接近的（动画生成失败时用）：
{catalog}
规则：本章被切成 {k} 个分区（见用户消息里的“分区”），**每个分区必须恰好选 1 个**（at 落在该分区段号范围内），不要都挤在后半章；
开场的进门/到达这类空间性强的时刻也是好选择；at 必须落在该场景段号范围内且是正文段（不是日期行、章名或脚注）；只输出 JSON。"""

GEN_SYS = """你是 The Pudding / NYT 图像部的前端动画师。为一部小说的一个关键场景写一段**自包含**的滚动叙事动画模块。
你拿到的只有场景描述（不是原文）。不要写出原文句子；画面里的文字只允许是短标签（≤8字），且最多 4 处。

## 视觉方向（必须）
编辑部 / 暗房 / 纸与墨：米白纸面上的墨线插画，细线（1–1.6px）、留白多、一两处强调色；像报纸长文里的手绘图解，而不是游戏或 PPT。
拒绝模板感：不要居中大标题 + 渐变背景 + 圆角卡片 + emoji；不要霓虹、发光、彩虹渐变、阴影堆叠。
要有**具体的空间与物件**（按描述画出来：门、料理台、车窗、楼梯……），用 SVG 线描为主，可用 Canvas 做颗粒/光影/雨雪。
人物用一笔线描剪影（头 + 躯干 + 手臂的简单线条，可表现插兜、坐、伸手等姿态）或圆点 + 名字，颜色用 var(--c-人物id)；人物和物件要放在空间里合理的位置（坐在台面上就画在台面上）。
构图：先定一个透视清楚的空间（地平线/墙角线），主体占画面 50–70%，四周留白；整体像一张会动的报纸插画。
叙事要随滚动推进：按描述里的情绪曲线分 3–6 个节拍，**节拍之间画面变化要大到一眼能看出**（不是挪几像素）。至少组合使用其中 3 种：
  ① 墨线逐笔画出空间和物件（VRH.draw）；② 镜头推拉/平移（VRH.camera，从全景推到关键物件或两人之间）；③ 人物符号位置与距离变化（亲密度高→靠近）；
  ④ 光影变化（局部的 var(--heat) 暖光或 var(--ink) 阴影，用径向渐变、覆盖不超过画面 1/3，透明度 ≤0.35，随紧张度/亲密度变化；不要整屏染色）；⑤ 关键物件的动作（门打开、罐子递出、指针转动）；
  ⑥ 画面底部一行小字注释（≤14字，随节拍淡入淡出，像报纸图注，无边框无底色）。
p=0 时只画出很少的线（开场留白、但不能全空），随 p 增长逐步画全；p=1 是完整的终态画面。

## 技术契约（违反任何一条都会被自动拒收）
1. 只输出一个 ```html 代码块，内容是 <body> 里的片段：<style>…</style> + 标记 + <script>…</script>。不要 <html>/<head>/<body>/<!DOCTYPE>。
2. 零外部资源：不许出现 http://、https://、//、@import、<link>、<img src=外链>、<script src>、fetch/XMLHttpRequest/WebSocket/Worker、import()；
   不许用 localStorage/sessionStorage/cookie/indexedDB（沙箱里会报错）；不许访问 parent/top、不许 alert、eval。
3. 运行时已提供全局 VRH：
   - VRH.onProgress(function(p){…})：p∈[0,1] 是读者滚动进度（父页面滚动驱动），**所有动画都必须是 p 的函数**，在回调里同步更新画面；
   - VRH.onTheme(function(theme){…})：'paper' 或 'night'；配色请用 CSS 变量（明暗主题会自动切换）：
     var(--bg) 背景、var(--bg2) 次背景、var(--ink) 主墨色、var(--ink2) 次墨色、var(--rule) 细线、var(--accent) 强调红、var(--heat) 暖光/脸红、var(--c-人物id) 人物色；
     Canvas 里取色用 VRH.color('ink') 这类函数，并在 onTheme 回调里重绘。不要写死 #fff/#000 之类的颜色。
   - VRH.reduced：true 表示读者开启了“减弱动效”：不要做任何自发的 requestAnimationFrame/setInterval 循环动画，只随 p 静态更新；
   - 墨线逐笔画出：给 SVG 分组加 data-draw 属性（如 <g id="room" data-draw>…</g>），启动时调用一次 VRH.prep()，之后在回调里 VRH.draw('#room', t) 让线条按 t∈[0,1] 画出；
   - 镜头推拉：VRH.camera(svgEl, [x,y,w,h 起始 viewBox], [x,y,w,h 结束 viewBox], t) —— 例如从全景推近到两人之间；
   - 节拍：var b = VRH.beat(p, [0,.3,.6,.85]) → {i: 当前节拍序号, t: 节拍内进度 0–1}；
   - 工具：VRH.clamp(x)、VRH.lerp(a,b,t)、VRH.seg(p,a,b)=把 p 在 [a,b] 区间映射到 0–1、VRH.ease(t)。
4. 模块运行在一个固定尺寸的 iframe 里（桌面约 780×530，手机约 360×440）。根元素用 width:100%;height:100vh，SVG 用 viewBox + preserveAspectRatio="xMidYMid meet"，
   任何元素都不能超出宽度（不许横向滚动）；文字 ≥12px；手机竖屏也要构图完整。
5. p=0 时画面就要有内容（开场构图）；p=1 是完整、可读的终态画面；p 来回拖动要可逆（不要依赖“只播一次”）。
6. 性能：回调里只改 transform/opacity/stroke-dashoffset/属性，不要每次重建 DOM；Canvas 粒子 ≤ 150 个；不要在回调里做重计算；整段代码 ≤ 30KB。
   自发的环境小动画（如灯光闪烁、雨丝）只在 !VRH.reduced 时用 requestAnimationFrame，且要轻。
7. 代码要健壮：<canvas> 不能写在 <svg> 里面（要做 svg 的兄弟元素、绝对定位叠在上面）；getElementById 拿到的元素先判空；不要使用未定义变量；不要依赖外部字体（用 serif / sans-serif 即可）。

## 结构建议
<style> #stage{position:relative;width:100%;height:100vh;overflow:hidden;background:var(--bg)} … </style>
<div id="stage"><svg viewBox="0 0 800 520" preserveAspectRatio="xMidYMid meet">…</svg><div class="label">…</div></div>
<script>(function(){ var svg=document.querySelector('#stage svg'); VRH.prep();
 VRH.onProgress(function(p){ VRH.draw('#room', VRH.seg(p,0,.35)); VRH.camera(svg,[0,0,800,520],[220,120,360,234],VRH.ease(VRH.seg(p,.4,.8))); var b=VRH.beat(p,[0,.3,.6,.85]); … });
 VRH.onTheme(function(){ /* 重绘 canvas */ }); })();</script>
"""

BOARD_SYS = """你是报纸图像部的分镜师。根据场景描述，为一段 800×520 坐标系的 SVG 滚动动画写“镜头脚本”。只输出 JSON：
{"ground_y":地面/台面基准线 y,"perspective":"一句话说明视角，如 侧面平视 / 俯视 / 车内后座视角",
 "elements":[{"id":"英文短id","what":"物件或空间结构","x":左上x,"y":左上y,"w":宽,"h":高,"draw_order":1起逐笔画出的先后,"detail":"线描要点：几条线、什么形状"}],
 "figures":[{"id":"人物id","pose":"站/坐/插兜/伸手…","start":[x,y],"end":[x,y]}],
 "beats":[{"t":0.0,"camera":[x,y,w,h viewBox],"change":"这个节拍画面怎么变（具体到哪个元素、怎么动）","light":"暖光/阴影在哪、强弱 0–1","caption":"≤14字图注"}],
 "canvas_fx":"可选：Canvas 颗粒/雨/尘/光斑，没有就空"}
要求：元素 6–14 个，全部在 0–800 × 0–520 内，彼此不重叠得乱七八糟，主体在中间 60% 区域；beats 3–6 个，t 递增，camera 宽高比保持 800:520，
至少一个节拍推近（camera 更小）到关键物件或人物之间；人物位置随亲密度变化。只输出 JSON。"""

HINTS = [
    ("getContext is not a function", "<canvas> 放在了 <svg> 里面：把 canvas 移到 svg 外面，作为兄弟元素绝对定位叠放。"),
    ("is not defined", "用到了未定义的变量/函数：检查拼写，所有变量先声明。"),
    ("Cannot read properties of null", "getElementById/querySelector 取到了 null：检查 id 是否一致，并先判空。"),
    ("横向溢出", "根元素用 width:100%，SVG 用 viewBox + width:100%，不要写固定像素宽度或 100vw + 边距。"),
    ("随滚动变化太小", "确认 VRH.onProgress 的回调真的在改画面；节拍之间要有大变化（逐笔画出、镜头推近、人物移动）。"),
    ("空白", "检查元素是否在可视范围、颜色是否用了 var(--ink)、是否初始就被 opacity:0 / dashoffset 藏起来且 p=1 时也没显示。"),
    ("减弱动效", "VRH.reduced 为 true 时不要启动 requestAnimationFrame / setInterval，只在 onProgress 里静态更新。"),
    ("长任务", "回调里只改属性，不要重建 DOM / 重画大量 Canvas；预先缓存元素和路径长度。"),
]


def fix_message(errs):
    hints = [h for k, h in HINTS if any(k in e for e in errs)]
    return FIX_MSG.format(errs="\n".join(f"- {x}" for x in errs[:12])) + ("\n修复提示：\n" + "\n".join(f"- {h}" for h in hints) if hints else "")


FIX_MSG = """自动校验没有通过，问题如下：
{errs}
请修复所有问题，重新输出完整的模块（同样只输出一个 ```html 代码块，遵守全部技术契约）。保持原来的画面构思，不要缩水成简单图形。"""


# ------------------------------------------------------------------ helpers
def _emotion(plan, a, b):
    P = plan["paras"][a:b + 1]
    if not P:
        return {}
    f = lambda k: [round(p.get(k, 0), 2) for p in P]
    te, inn, bl = f("te"), f("in"), f("bl")
    third = max(1, len(P) // 3)
    trend = lambda xs: f"{sum(xs[:third]) / third:.2f}→{sum(xs[-third:]) / third:.2f}"
    return {"te_max": max(te), "te": trend(te), "in_max": max(inn), "in": trend(inn), "bl_max": max(bl),
            "peak_at": a + max(range(len(P)), key=lambda k: P[k].get("te", 0) + P[k].get("in", 0) + P[k].get("bl", 0))}


def _snips(segs, plan, a, b, n=4, width=48):
    """每场景挑情绪最强的几段，各取前 width 字（截断短摘，不是全文）。"""
    idx = sorted(range(a, b + 1), key=lambda i: -(plan["paras"][i].get("te", 0) + plan["paras"][i].get("bl", 0) + plan["paras"][i].get("in", 0) * .5))
    idx = sorted(i for i in idx[:n] if segs[i]["role"] == "text")
    return [f"[{i}] " + segs[i]["text"][:width] + ("…" if len(segs[i]["text"]) > width else "") for i in idx]


def chapter_digest(seg, plan, a, b, title, with_snips=True):
    segs = seg["segments"]; chars = {c["id"]: c["name"] for c in plan["characters"]}
    lines = [f"章：{title or '（无章名）'}，段号 {a}–{b}", "人物：" + "；".join(f"{k}={v}" for k, v in chars.items())]
    for k, s in enumerate(plan["scenes"]):
        if s["end"] < a or s["start"] > b:
            continue
        sa, sb = max(a, s["start"]), min(b, s["end"])
        em = _emotion(plan, sa, sb)
        pres = sorted({x for p in plan["paras"][sa:sb + 1] for x in p.get("pr", [])})
        vis = [f"{v['type']}@{v['at']}" for v in plan.get("visuals", []) if sa <= v["at"] <= sb]
        lines.append(f"\n场景 {k}「{s['name']}」段 {sa}–{sb}｜{s.get('time', '')}｜{s.get('place', '')}｜{s.get('tone', 'day')}｜在场 {','.join(pres) or '—'}"
                     f"｜紧张 {em.get('te')}(峰{em.get('te_max')}) 亲密 {em.get('in')}(峰{em.get('in_max')}) 脸红峰 {em.get('bl_max')} 情绪峰值段 {em.get('peak_at')}"
                     f"｜已有组件 {','.join(vis) or '无'}")
        if with_snips:
            lines += ["  " + x for x in _snips(segs, plan, sa, sb)]
    return "\n".join(lines)


def _is_filtered(e):
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            body = ""
        e.vr_body = body
        return "content_filter" in body or "content filter" in body.lower() or "safety" in body.lower()
    return False


def _chat(msgs, max_tokens, stats, json_mode, effort=None):
    return planner.chat(msgs, "mercury-3", max_tokens, stats, "mercury", json_mode=json_mode,
                        extra_override={"reasoning_effort": effort} if effort else None,
                        timeout=None if json_mode else 180)


class HeroStats:
    def __init__(self):
        self.selected = 0; self.passed_first = 0; self.passed_fixed = 0; self.fallback = 0; self.filtered = 0; self.rule = 0; self.mode = DEFAULT_MODE
        self.gen_calls = 0; self.select_calls = 0; self.browser_checks = 0; self.log = []; self.warnings = []
        self.llm = planner.Stats("mercury", "mercury-3")

    def as_dict(self):
        d = {k: v for k, v in self.__dict__.items() if k != "llm"}
        d["llm_calls"] = self.llm.calls; d["tokens_out"] = self.llm.tokens_out; d["transient"] = self.llm.transient
        return d


# ------------------------------------------------------------------ stages
def zones(seg, a, b, k):
    """把本章正文段按顺序切成 k 个等长分区，返回 [(lo, hi)]（段号闭区间）。"""
    txt = [i for i in range(a, b + 1) if seg["segments"][i]["role"] == "text"] or list(range(a, b + 1))
    out = []
    for z in range(k):
        lo, hi = txt[len(txt) * z // k], txt[max(len(txt) * (z + 1) // k - 1, len(txt) * z // k)]
        out.append((lo if z else a, hi if z < k - 1 else b))
    return out


def zone_of(zs, at):
    return next((z for z, (lo, hi) in enumerate(zs) if lo <= at <= hi), len(zs) - 1)


def snap_text(seg, at, lo=0, hi=None, skip=()):
    """at 落在日期行 / 脚注 / 章名 / 首屏段上时，挪到最近的正文段（优先往后，范围 lo–hi）。"""
    S = seg["segments"]; hi = len(S) - 1 if hi is None else hi
    ok = lambda i: lo <= i <= hi and S[i]["role"] == "text" and i not in skip
    if 0 <= at < len(S) and ok(at):
        return at
    for d in range(1, len(S)):
        for i in (at + d, at - d):
            if ok(i):
                return i
    return at


def _clean_desc(h, scenes_in, plan, a, b, char_ids, hs):
    import jsonschema
    if not isinstance(h, dict):
        return None
    for key in ("scene", "at"):
        if isinstance(h.get(key), str) and h[key].strip().isdigit():
            h[key] = int(h[key])
    V = jsonschema.Draft202012Validator(SELECT_SCHEMA["properties"]["heroes"]["items"])
    errs = list(V.iter_errors(h))
    if errs:
        # 宽松修复：截断超长字符串、夹紧秒数；结构性缺失则丢弃
        for f, n in (("title", 14), ("caption", 40), ("summary", 220), ("setting", 80), ("motif", 60)):
            if isinstance(h.get(f), str): h[f] = h[f][:n]
        if isinstance(h.get("seconds"), (int, float)): h["seconds"] = min(20, max(8, h["seconds"]))
        h["objects"] = [str(x)[:16] for x in (h.get("objects") or [])][:8]
        h["curve"] = [c for c in (h.get("curve") or []) if isinstance(c, dict) and isinstance(c.get("t"), (int, float))][:6]
        for c in h["curve"]:
            c["t"] = min(1, max(0, c["t"])); c["beat"] = str(c.get("beat", ""))[:30]
        if list(V.iter_errors(h)):
            hs.warnings.append(f"[hero select {a}-{b}] 丢弃不合格描述：{errs[0].message[:80]}"); return None
    sc = h["scene"]
    if sc not in scenes_in:   # 用 at 反推场景
        sc = next((i for i in scenes_in if plan["scenes"][i]["start"] <= h["at"] <= plan["scenes"][i]["end"]), None)
        if sc is None:
            return None
        h["scene"] = sc
    s = plan["scenes"][sc]
    if not (max(a, s["start"]) <= h["at"] <= min(b, s["end"])):
        h["at"] = min(b, s["end"])
    h["characters"] = [c for c in h.get("characters", []) if c.get("id") in char_ids]
    h["fallback"] = clean_fallback(h.get("fallback"))
    h.setdefault("tone", s.get("tone", "day"))
    return h


def select(seg, plan, a, b, title, k, hs):
    """返回本章 hero 场景描述列表（已校验 at/scene，并按前/中/后分区各取 1 个），被拒或失败返回 []。"""
    scenes_in = [i for i, s in enumerate(plan["scenes"]) if not (s["end"] < a or s["start"] > b)]
    if not scenes_in:
        return []
    char_ids = {c["id"] for c in plan["characters"]}
    zs = zones(seg, a, b, k)
    names = ["前段", "中段", "后段"] if k == 3 else (["前半", "后半"] if k == 2 else ["全章"])
    skip = set(plan.get("meta", {}).get("hero_segments", []) or [])
    picked, snips = {}, True
    for round_ in range(2):
        missing = [z for z in range(k) if z not in picked]
        if not missing:
            break
        zone_txt = "\n分区：" + "；".join(f"{names[z]} 段 {zs[z][0]}–{zs[z][1]}" for z in range(k))
        if round_:
            zone_txt += "\n已经选好的分区不用再选；这次只为这些分区各选 1 个：" + "、".join(f"{names[z]}（段 {zs[z][0]}–{zs[z][1]}）" for z in missing)
        o = None
        for with_snips in ((True, False) if snips else (False,)):
            msgs = [{"role": "system", "content": SELECT_SYS.format(k=len(missing) if round_ else k, catalog=prompt_catalog())},
                    {"role": "user", "content": chapter_digest(seg, plan, a, b, title, with_snips) + zone_txt}]
            hs.select_calls += 1
            try:
                raw, fin = _chat(msgs, 3000, hs.llm, True)
                o = planner.parse_json(raw)
                break
            except urllib.error.HTTPError as e:
                if _is_filtered(e):
                    hs.filtered += 1; hs.warnings.append(f"[hero select {a}-{b}] 内容过滤拒答" + ("，去掉短摘重试" if with_snips else "，本章不做 hero"))
                    snips = False
                    continue
                hs.warnings.append(f"[hero select {a}-{b}] HTTP {e.code}"); o = None; break
            except Exception as e:
                hs.warnings.append(f"[hero select {a}-{b}] {type(e).__name__}: {str(e)[:120]}"); o = None; break
        if not isinstance(o, dict):
            break
        for h in (o.get("heroes") or [])[:k]:
            h = _clean_desc(h, scenes_in, plan, a, b, char_ids, hs)
            if not h:
                continue
            sc = plan["scenes"][h["scene"]]
            h["at"] = snap_text(seg, h["at"], max(a, sc["start"]), min(b, sc["end"]), skip)
            if seg["segments"][h["at"]]["role"] != "text":
                h["at"] = snap_text(seg, h["at"], a, b, skip)
            z = zone_of(zs, h["at"])
            if z in picked:
                hs.warnings.append(f"[hero select {a}-{b}] 丢弃与{names[z]}已选场景重复的 {h['scene']}@{h['at']}"); continue
            if any(x["scene"] == h["scene"] or abs(x["at"] - h["at"]) < MIN_GAP for x in picked.values()):
                hs.warnings.append(f"[hero select {a}-{b}] 丢弃与其它 hero 太近的场景 {h['scene']}@{h['at']}"); continue
            h["zone"] = z
            picked[z] = h
    return [picked[z] for z in sorted(picked)]


def _desc_for_gen(h, plan):
    names = {c["id"]: c["name"] for c in plan["characters"]}
    d = {k: h[k] for k in ("title", "summary", "setting", "objects", "curve", "motif", "seconds", "tone") if k in h}
    # 摘要里偶尔夹带原文台词（引号里的话）→ 换成占位，避免原文出现在动画画面里
    if isinstance(d.get("summary"), str):
        d["summary"] = re.sub(r"[“\"'‘「『][^”\"'’」』]{2,40}[”\"'’」』]", "（一句台词）", d["summary"])
    d["characters"] = [{"id": c["id"], "name": names.get(c["id"], c["id"]), "role": c.get("role", ""), "css_color": f"var(--c-{c['id']})"}
                       for c in h.get("characters", [])]
    return json.dumps(d, ensure_ascii=False, indent=1)


def storyboard(desc, hs):
    """先让模型写一份带坐标的镜头脚本（JSON），再按脚本写代码：构图明显更稳。失败就跳过（返回 ""）。"""
    hs.board_calls = getattr(hs, "board_calls", 0) + 1
    try:
        raw, fin = _chat([{"role": "system", "content": BOARD_SYS}, {"role": "user", "content": "场景描述：\n" + desc}], 3500, hs.llm, True)
        o = planner.parse_json(raw or "")
        return json.dumps(o, ensure_ascii=False, separators=(",", ":"))[:6000] if isinstance(o, dict) and o.get("elements") else ""
    except urllib.error.HTTPError as e:
        if _is_filtered(e):
            raise planner.ContentFiltered("hero-board")
        return ""
    except Exception:
        return ""


def generate(h, plan, hs, msgs=None):
    """返回 (module, messages) ；被内容过滤拒答抛 planner.ContentFiltered。"""
    if msgs is None:
        desc = _desc_for_gen(h, plan)
        board = storyboard(desc, hs)
        msgs = [{"role": "system", "content": GEN_SYS},
                {"role": "user", "content": "场景描述：\n" + desc + ("\n\n镜头脚本（坐标基于 viewBox 0 0 800 520，按它构图）：\n" + board if board else "") +
                 "\n\n请输出动画模块。"}]
    hs.gen_calls += 1
    try:
        raw, fin = _chat(msgs, 14000, hs.llm, False, EFFORT)
    except urllib.error.HTTPError as e:
        if _is_filtered(e):
            raise planner.ContentFiltered("hero")
        raise
    if not raw:
        raise ValueError(f"模型没有返回内容（finish={fin}）")
    mod = extract_module(raw)
    if fin == "length":
        mod = mod + "\n<!-- truncated -->"
    return mod, msgs + [{"role": "assistant", "content": raw if len(raw) < 60000 else "```html\n" + mod + "\n```"}]


def check(mods, chars, hs):
    """mods: {id: module}. → {id: errors[]}（[] = 通过）。"""
    out, docs = {}, {}
    for k, m in mods.items():
        e = static_check(m)
        if e:
            out[k] = e
        else:
            docs[k] = wrap(m, chars)
    if docs:
        hs.browser_checks += len(docs)
        res = browser_check(docs)
        if res is None:
            for k in docs:
                out[k] = []
                hs.warnings.append(f"[hero {k}] 没有 Chromium，只做了静态检查")
        else:
            for k in docs:
                r = res.get(k) or {"ok": False, "errors": ["校验器没有返回结果"]}
                out[k] = [] if r.get("ok") else (r.get("errors") or ["未通过"])
                hs.log.append({"id": k, "check": r.get("metrics"), "errors": r.get("errors", [])[:6]})
    return out


def make_one(hid, h, plan, chars, hs):
    """生成 → 校验 → 修一次 → 仍不合格回退。返回 hero 条目。"""
    t0 = time.time()
    entry = {"id": hid, "at": h["at"], "pos": "after", "scene": h["scene"], "title": h.get("title", ""),
             "caption": h.get("caption") or h.get("title", ""), "seconds": h.get("seconds", 12), "tone": h.get("tone", "day"),
             "desc": {k: h.get(k) for k in ("summary", "setting", "objects", "characters", "curve", "motif")},
             "fallback": h.get("fallback"), "status": "fallback", "module": None, "attempts": 0, "errors": []}
    try:
        mod, msgs = generate(h, plan, hs)
        entry["attempts"] = 1
        errs = check({hid: mod}, chars, hs)[hid]
        if errs:
            entry["errors"].append(errs[:8])
            msgs = msgs + [{"role": "user", "content": fix_message(errs)}]
            mod, _ = generate(h, plan, hs, msgs)
            entry["attempts"] = 2
            errs = check({hid: mod}, chars, hs)[hid]
            if errs:
                entry["errors"].append(errs[:8])
                entry["rejected_module"] = mod     # 只写进 <输出>.heroes.json 方便排查，不进 plan / 页面
                hs.fallback += 1
            else:
                hs.passed_fixed += 1; entry.update(status="fixed", module=mod)
        else:
            hs.passed_first += 1; entry.update(status="ok", module=mod)
    except planner.ContentFiltered:
        hs.filtered += 1; hs.fallback += 1; entry["errors"].append(["content_filter"])
        hs.warnings.append(f"[hero {hid}] 内容过滤拒答，回退预制组件")
    except Exception as e:
        hs.fallback += 1; entry["errors"].append([f"{type(e).__name__}: {str(e)[:200]}"])
        hs.warnings.append(f"[hero {hid}] 生成失败（{type(e).__name__}），回退预制组件")
    entry["seconds_spent"] = round(time.time() - t0, 1)
    if entry["module"]:
        entry["bytes"] = len(entry["module"].encode())
    return entry


# ------------------------------------------------------------------ prims mode (default)
def compose(h, plan, hs, msgs=None, used=None):
    """模型只出编排 JSON。返回 (obj|None, messages, parse_error|None)；被内容过滤拒答抛 planner.ContentFiltered。"""
    if msgs is None:
        msgs = [{"role": "system", "content": HP.COMPOSE_SYS.format(catalog=HP.catalog())},
                {"role": "user", "content": HP.compose_user(_desc_for_gen(h, plan), plan["characters"], used)}]
    hs.gen_calls += 1
    try:
        raw, fin = _chat(msgs, 2500, hs.llm, True)
    except urllib.error.HTTPError as e:
        if _is_filtered(e):
            raise planner.ContentFiltered("hero-compose")
        raise
    msgs = msgs + [{"role": "assistant", "content": raw or ""}]
    try:
        return planner.parse_json(raw or ""), msgs, None
    except Exception as e:
        return None, msgs, f"输出不是合法 JSON（{type(e).__name__}）"


COMPOSE_FIX = """编排没有通过自动校验：
{errs}
请修正后重新输出完整的编排 JSON（同样格式、只输出 JSON）。画面文字只能是你自己的意象短词。"""


def _check_comps(items, chars, hs):
    """items: {id: payload} → {id: errors[]}；一次启动浏览器批量实测。"""
    if not items:
        return {}
    docs = {k: HP.build_doc(v, chars) for k, v in items.items()}
    hs.browser_checks += len(docs)
    res = browser_check(docs)
    if res is None:
        for k in docs:
            hs.warnings.append(f"[hero {k}] 没有 Chromium，只做了编排校验")
        return {k: [] for k in docs}
    out = {}
    for k in docs:
        r = res.get(k) or {"ok": False, "errors": ["校验器没有返回结果"]}
        out[k] = [] if r.get("ok") else (r.get("errors") or ["未通过"])
        hs.log.append({"id": k, "check": r.get("metrics"), "errors": r.get("errors", [])[:6]})
    return out


def run_prims(seg, plan, jobs, chars, hs, progress=lambda *a: None):
    """原语模式：顺序编排（快、便于避免重复）→ 批量实测 → 修一次 → 规则编排 → 仍不合格才回退预制组件。"""
    source = "".join(x["text"] for x in seg["segments"])
    char_ids = {c["id"] for c in plan["characters"]}
    E, P, M = {}, {}, {}           # entries, payloads, messages
    used = {}                      # 章 → 本章已用过的原语（按次数上限去重复）
    for fig, (hid, h) in enumerate(jobs, 1):
        t0 = time.time()
        sc = plan["scenes"][h["scene"]]
        e = {"id": hid, "kind": "prims", "at": h["at"], "pos": "after", "scene": h["scene"], "zone": h.get("zone"), "title": h.get("title", ""),
             "caption": h.get("caption") or h.get("title", ""), "seconds": h.get("seconds", 12), "tone": h.get("tone", "day"),
             "desc": {k: h.get(k) for k in ("summary", "setting", "objects", "characters", "curve", "motif")},
             "fallback": h.get("fallback"), "status": "fallback", "module": None, "comp": None, "attempts": 0, "errors": [], "_t": 0.0}
        e["_data"] = {"series": HP.series_for(plan, sc["start"], sc["end"])}; e["_fig"] = fig
        E[hid] = e
        ch = hid.split("-")[0]; e["_ch"] = ch; e["_dt"] = HP.desc_text(h)
        try:
            o, msgs, perr = compose(h, plan, hs, used=used.get(ch, []))
            e["attempts"] = 1
            comp, errs = HP.validate(o, char_ids, source, e["_dt"], used.get(ch, [])) if perr is None else (None, [perr])
            M[hid] = msgs
            if errs:
                e["errors"].append(errs[:8]); e["_need_fix"] = True
            else:
                P[hid] = comp
                used.setdefault(ch, []).extend(x["prim"] for x in comp["shots"])
        except planner.ContentFiltered:
            hs.filtered += 1; e["errors"].append(["content_filter"]); e["_rule"] = True
            hs.warnings.append(f"[hero {hid}] 编排被内容过滤拒答，改用规则编排")
        except Exception as ex:
            e["errors"].append([f"{type(ex).__name__}: {str(ex)[:200]}"]); e["_rule"] = True
        e["_t"] += time.time() - t0

    def payload(hid, comp):
        e = E[hid]
        return HP.page_payload(comp, e["_fig"], float(e["seconds"]), chars, e["_data"])

    # round 1: browser check of first attempts
    res = _check_comps({k: payload(k, c) for k, c in P.items()}, chars, hs) if P else {}
    for k, errs in res.items():
        if errs:
            E[k]["errors"].append(errs[:8]); E[k]["_need_fix"] = True; P.pop(k)
        else:
            E[k].update(status="ok", comp=P[k]); hs.passed_first += 1
    # round 2: one fix
    fixed = {}
    for hid, e in E.items():
        if not e.get("_need_fix") or e.get("_rule"):
            continue
        t0 = time.time()
        try:
            msgs = M[hid] + [{"role": "user", "content": COMPOSE_FIX.format(errs="\n".join(f"- {x}" for x in e["errors"][-1][:12]))}]
            o, msgs, perr = compose(None, plan, hs, msgs=msgs)
            e["attempts"] = 2
            others = [x for k2, e2 in E.items() if k2 != hid and e2["_ch"] == e["_ch"] and k2 in P for x in (y["prim"] for y in P[k2]["shots"])]
            comp, errs = HP.validate(o, char_ids, source, e["_dt"], others) if perr is None else (None, [perr])
            if errs:
                e["errors"].append(errs[:8]); e["_rule"] = True
            else:
                fixed[hid] = comp
        except planner.ContentFiltered:
            hs.filtered += 1; e["errors"].append(["content_filter"]); e["_rule"] = True
        except Exception as ex:
            e["errors"].append([f"{type(ex).__name__}: {str(ex)[:200]}"]); e["_rule"] = True
        e["_t"] += time.time() - t0
    res = _check_comps({k: payload(k, c) for k, c in fixed.items()}, chars, hs) if fixed else {}
    for k, errs in res.items():
        if errs:
            E[k]["errors"].append(errs[:8]); E[k]["_rule"] = True
        else:
            E[k].update(status="fixed", comp=fixed[k]); hs.passed_fixed += 1
    # round 3: rule composition (always valid), still browser-checked
    rules = {}
    for hid, e in E.items():
        if e["status"] in ("ok", "fixed"):
            continue
        others = [x for k2, e2 in E.items() if k2 != hid and e2["_ch"] == e["_ch"] and (e2.get("comp") or k2 in rules)
                  for x in (y["prim"] for y in (e2.get("comp") or rules.get(k2))["shots"])]
        rules[hid] = HP.rule_compose(dict(e["desc"], title=e["title"]), char_ids, source, others)
    res = _check_comps({k: payload(k, c) for k, c in rules.items()}, chars, hs) if rules else {}
    for k, errs in res.items():
        if errs:
            E[k]["errors"].append(["rule: " + x for x in errs[:6]]); hs.fallback += 1
        else:
            E[k].update(status="rule", comp=rules[k]); hs.rule += 1
    out = []
    unsafe = lambda x: not x or HP.leaks(x, source) or re.search(r"[你我吗呢吧啊呀]|^(?:别|不要|求)", x)
    for n_, (hid, e) in enumerate(E.items(), 1):
        # 标题/图注由选场景那步的模型写，可能直接用了原文台词（如“我喜欢你”）→ 换成编排的小标题 / 去掉
        if unsafe(e["title"]):
            e["title"] = next((x for x in [(e["comp"] or {}).get("slug", ""), str((e["desc"] or {}).get("motif") or "")[:10]] if x and not unsafe(x)), "")
        if e.get("caption") and unsafe(e["caption"]):
            e["caption"] = ""
        if e["comp"]:
            e["comp"] = payload(hid, e["comp"])
            e["bytes"] = len(json.dumps(e["comp"], ensure_ascii=False).encode())
        e["seconds_spent"] = round(e.pop("_t"), 1)
        for k in [k for k in e if k.startswith("_")]:
            e.pop(k)
        out.append(e)
        progress("hero+", f"{hid}「{e['title']}」{ {'ok': '通过', 'fixed': '修复后通过', 'rule': '规则编排', 'fallback': '回退预制组件'}[e['status']] }（{n_}/{len(E)}）")
    return out


def chapters_for(seg, plan):
    chs = seg.get("chapters") or plan.get("chapters") or []
    if len(chs) < 2:
        n = len(seg["segments"])
        return [{"start": 0, "end": n - 1, "title": (chs[0]["title"] if chs else "")}]
    return chs


def run(seg, plan, per_chapter=DEFAULT_PER_CHAPTER, progress=lambda *a: None, chapters=None, mode=None):
    """给 plan 加上 plan["heroes"]；返回 HeroStats。plan 的其它部分不变。"""
    from .render import fix_colors
    hs = HeroStats(); t0 = time.time(); hs.mode = mode if mode in MODES else DEFAULT_MODE
    k = max(1, min(HERO_MAX_PER_CHAPTER, int(per_chapter)))
    chars = fix_colors([dict(c) for c in plan["characters"]])
    chs = chapters if chapters is not None else chapters_for(seg, plan)
    progress("hero", f"关键场景动画：{len(chs)} 章 × ≤{k} 个")
    workers = max(1, int(os.environ.get("VR_LLM_CONCURRENCY", 3)))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        sels = list(ex.map(lambda c: select(seg, plan, c["start"], c["end"], c.get("title", ""), k, hs), chs))
    jobs = []
    for ci, hl in enumerate(sels):
        for j, h in enumerate(hl):
            jobs.append((f"h{ci + 1}-{j + 1}", h))
    hs.selected = len(jobs)
    if hs.mode == "prims":
        progress("hero", f"选出 {len(jobs)} 个关键场景，编排原语镜头")
        plan["heroes"] = sorted(run_prims(seg, plan, jobs, chars, hs, progress), key=lambda e: (e["at"], e["id"]))
        hs.seconds = round(time.time() - t0, 1)
        return hs
    progress("hero", f"选出 {len(jobs)} 个关键场景，生成动画模块")
    heroes = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(make_one, hid, h, plan, chars, hs): hid for hid, h in jobs}
        for f in cf.as_completed(futs):
            e = f.result(); heroes.append(e)
            progress("hero+", f"{e['id']}「{e['title']}」{ {'ok': '通过', 'fixed': '修复后通过', 'fallback': '回退预制组件'}[e['status']] }（{len(heroes)}/{len(jobs)}）")
    plan["heroes"] = sorted(heroes, key=lambda e: (e["at"], e["id"]))
    hs.seconds = round(time.time() - t0, 1)
    return hs


def clean_fallback(fb):
    """回退组件：类型必须在预制组件库里，参数按组件 schema 清洗（不合法的键去掉，用默认值）。"""
    import jsonschema
    if not (isinstance(fb, dict) and fb.get("type") in COMPONENTS):
        return None
    params = fb.get("params") if isinstance(fb.get("params"), dict) else {}
    spec = {"type": "object", "properties": COMPONENTS[fb["type"]]["params"]}
    bad = {e.absolute_path[0] for e in jsonschema.Draft202012Validator(spec).iter_errors(params) if e.absolute_path}
    params = {k: v for k, v in params.items() if k not in bad and k in spec["properties"]}
    if list(jsonschema.Draft202012Validator(spec).iter_errors(params)):
        return None
    return {"type": fb["type"], "params": params}


def sanitize_heroes(heroes, n, char_ids=None, source=""):
    """外部 / 复用 plan 里的 heroes：代码模块重新做静态检查；原语编排重新做编排校验（含原文重合检查）。不合格改为回退。"""
    out = []
    for h in heroes or []:
        if not isinstance(h, dict) or not isinstance(h.get("at"), int) or not (0 <= h["at"] < n):
            continue
        h = dict(h)
        if h.get("module") and static_check(h["module"]):
            h["module"] = None; h["status"] = "fallback"
        if h.get("comp"):
            c = h["comp"] if isinstance(h["comp"], dict) else {}
            ids = set(char_ids) if char_ids is not None else {x.get("id") for x in c.get("chars", []) if isinstance(x, dict)}
            clean, errs = HP.validate(c, ids, source)
            if errs:
                h["comp"] = None; h["status"] = "fallback"
            else:
                h["comp"] = dict(c, shots=clean["shots"], slug=clean["slug"])
                h["module"] = None
        h["fallback"] = clean_fallback(h.get("fallback"))
        h["pos"] = "before" if h.get("pos") == "before" else "after"
        try:
            h["seconds"] = min(20.0, max(8.0, float(h.get("seconds") or 12)))
        except (TypeError, ValueError):
            h["seconds"] = 12.0
        out.append(h)
    return out
