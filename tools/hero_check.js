// 批量：node hero_check.js modules.json  → 最后一行输出 JSON：[{id, ok, errors:[], metrics:{}}]
// 服务：node hero_check.js --serve [--concurrency 2] [--timeout 40] [--fast]
//   stdin 每行一个 {"id","doc","timeout"?,"fast"?}；stdout 每行一个 {"event":"start","id"} 或结果 {id, ok, errors, metrics}。
//   一个浏览器实例常驻，页面池限并发；单个模块超时只判它一个（关掉它的页面），不影响其它模块。
//   fast：原语编排（库代码已整体测过）用的精简采样：少截帧、短等待、短扫动、不测减弱动效。
// modules.json = [{id, doc}]，doc 是 hero.wrap() 生成的完整 srcdoc（CSP + VRH 运行时 + 模块）。
// 每个模块都放进与阅读页相同的 <iframe sandbox="allow-scripts" srcdoc> 里实测：
//   JS 报错 / CSP 违规 / 外部请求；桌面 820×560 与手机 358×480 横向溢出；明暗主题下 p=0/.5/1 非空白；
//   画面随滚动进度变化；夜间主题背景不是写死的浅色；进度扫动时的帧间隔与长任务；减弱动效下画面静止。
const puppeteer = require('puppeteer-core'); const fs = require('fs');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const LIMITS = { bytes: 40 * 1024, nonblank_mid: 0.008, nonblank_start: 0.004, scroll_changed: 0.025,
  longtask_max: 250, longtask_ms: 600, p95: 90, reduced_diff: 0.006, nodes: 4000, night_bg_lum: 0.45, coverage: 0.012, contrast: 3.0 };

const harness = (w, h, bg) => `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="margin:0;background:${bg}">
<div id="wrap" style="width:${w}px;height:${h}px;margin:20px 16px"><iframe id="f" sandbox="allow-scripts" title="hero" style="width:100%;height:100%;border:0;display:block"></iframe></div>
<canvas id="cv" width="10" height="10" style="display:none"></canvas>
<script>
window.MSGS = []; addEventListener('message', e => { if (e.data && e.data.vrh && e.source === document.getElementById('f').contentWindow) MSGS.push(e.data); });
window.send = m => document.getElementById('f').contentWindow.postMessage(Object.assign({vrh: 1}, m), '*');
window.analyze = b64 => new Promise(res => { const im = new Image(); im.onload = () => {
  const W = 128, H = Math.max(8, Math.round(128 * im.height / im.width)); const cv = document.getElementById('cv'); cv.width = W; cv.height = H;
  const big = document.createElement('canvas'); big.width = Math.min(im.width, 400); big.height = Math.round(big.width * im.height / im.width);
  const bx = big.getContext('2d'); bx.drawImage(im, 0, 0, big.width, big.height); const d = bx.getImageData(0, 0, big.width, big.height).data;
  const hist = new Map(); for (let i = 0; i < d.length; i += 4){ const k = (d[i] >> 3) << 10 | (d[i+1] >> 3) << 5 | (d[i+2] >> 3); hist.set(k, (hist.get(k) || 0) + 1); }
  let bk = 0, bc = -1; hist.forEach((c, k) => { if (c > bc){ bc = c; bk = k; } });
  const bg = [((bk >> 10) & 31) * 8 + 4, ((bk >> 5) & 31) * 8 + 4, (bk & 31) * 8 + 4];
  let ink = 0, strong = 0; const n = d.length / 4;
  const L = (r, g, b) => { const f = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const Lbg = L(bg[0], bg[1], bg[2]); const far = [];
  for (let i = 0; i < d.length; i += 4){ const dist = Math.abs(d[i]-bg[0]) + Math.abs(d[i+1]-bg[1]) + Math.abs(d[i+2]-bg[2]); if (dist > 40){ ink++; far.push(L(d[i], d[i+1], d[i+2])); } if (dist > 110) strong++; }
  // 对比度：背景 vs 离背景最远的 2% 有效像素（文字/主体）的平均亮度，WCAG 公式
  let contrast = 1;
  if (far.length){ far.sort((a, b) => Math.abs(b - Lbg) - Math.abs(a - Lbg)); const k = Math.max(1, Math.floor(far.length * 0.02)); let m = 0; for (let i = 0; i < k; i++) m += far[i]; m /= k;
    contrast = (Math.max(m, Lbg) + 0.05) / (Math.min(m, Lbg) + 0.05); }
  const cx = cv.getContext('2d'); cx.drawImage(im, 0, 0, W, H); const s = cx.getImageData(0, 0, W, H).data; const sig = [];
  for (let i = 0; i < s.length; i += 4) sig.push(Math.round(0.3 * s[i] + 0.59 * s[i+1] + 0.11 * s[i+2]));
  const lum = (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255;
  res({nonblank: ink / n, strong: strong / n, bg_lum: +lum.toFixed(3), contrast: +contrast.toFixed(2), sig}); }; im.src = 'data:image/png;base64,' + b64; });
</script></body></html>`;
const sigDiff = (a, b) => { let s = 0; for (let i = 0; i < Math.min(a.length, b.length); i++) s += Math.abs(a[i] - b[i]); return s / (a.length || 1) / 255; };
// 变化面积：128 宽缩略图里灰度变化 > 14 的像素占比（细线条的移动也能测到）
const changed = (a, b) => { let c = 0; for (let i = 0; i < Math.min(a.length, b.length); i++) if (Math.abs(a[i] - b[i]) > 14) c++; return c / (a.length || 1); };

async function load(page, doc, w, h, bg){
  await page.setContent(harness(w, h, bg), {waitUntil: 'load'});
  await page.evaluate(d => { document.getElementById('f').srcdoc = d; }, doc);
  const t0 = Date.now();
  while (Date.now() - t0 < 8000){ if (await page.evaluate(() => MSGS.some(m => m.t === 'ready'))) return Date.now() - t0; await sleep(60); }
  return -1;
}
// 真实浏览器的恶劣条件（Firefox / Chrome 进程隔离的 iframe 常见）：iframe 刚创建时视口是 0×0，之后长到真实尺寸，
// 但浏览器不一定补发 resize 事件。这里把 iframe 先放进 0×0 的容器，并在文档里屏蔽“浏览器原生的”resize 事件（isTrusted），
// 再把容器撑开，看画面是否按真实尺寸重新布局（而不是全挤在左上角 / 空白）。
const NO_NATIVE_RESIZE = "<script>(function(){var a=EventTarget.prototype.addEventListener;EventTarget.prototype.addEventListener=function(t,f,o){" +
  "if(t==='resize'&&this===window&&typeof f==='function'){var g=f;f=function(e){if(e&&e.isTrusted)return;return g.apply(this,arguments)}}return a.call(this,t,f,o)};" +
  "try{Object.defineProperty(window,'onresize',{set:function(){},get:function(){return null},configurable:true})}catch(e){}})()<\/script>";
function hostileDoc(doc){ const i = doc.indexOf('<head>'); return i < 0 ? NO_NATIVE_RESIZE.replace('<\\/', '</') + doc : doc.slice(0, i + 6) + NO_NATIVE_RESIZE.replace('<\\/', '</') + doc.slice(i + 6); }
async function hostileLoad(page, doc, w, h, bg){
  await page.setContent(harness(w, h, bg), {waitUntil: 'load'});
  await page.evaluate(() => { const wr = document.getElementById('wrap'); wr.style.width = '0px'; wr.style.height = '0px'; });
  await page.evaluate(d => { document.getElementById('f').srcdoc = d; }, hostileDoc(doc));
  const t0 = Date.now(); let ok = false;
  while (Date.now() - t0 < 8000){ if (await page.evaluate(() => MSGS.some(m => m.t === 'ready'))){ ok = true; break; } await sleep(60); }
  await page.evaluate((w, h) => { const wr = document.getElementById('wrap'); wr.style.width = w + 'px'; wr.style.height = h + 'px'; }, w, h);
  await sleep(450);
  return ok;
}
async function frame(page, p, theme, wait = 260){
  await page.evaluate((p, theme) => send({p, theme, force: true}), p, theme);
  await sleep(wait);
  const el = await page.$('#f');
  let r = await page.evaluate(b => analyze(b), await el.screenshot({encoding: 'base64'}));
  if (r.nonblank < LIMITS.nonblank_mid){   // 慢机器上偶尔截到还没画完的一帧：再等一下重截一次
    await sleep(500); r = await page.evaluate(b => analyze(b), await el.screenshot({encoding: 'base64'}));
  }
  return r;
}
async function probe(page){
  await page.evaluate(() => send({t: 'probe'})); await sleep(120);
  return page.evaluate(() => [...MSGS].reverse().find(m => m.t === 'probe') || null);
}

async function checkOne(b, mod, fast){
  const errors = [], m = {}, ext = new Set(), cons = [];
  const W = fast ? 140 : 260; m.fast = !!fast;
  mod._page = null;
  const bytes = Buffer.byteLength(mod.doc); m.doc_bytes = bytes;
  // 每个模块独立的浏览器上下文 = 独立渲染进程：一个模块死循环不会拖住同时在测的其它模块
  const ctx = b.createBrowserContext ? await b.createBrowserContext() : await b.createIncognitoBrowserContext();
  mod._ctx = ctx;
  const page = await ctx.newPage(); mod._page = page;
  page.on('request', r => { const u = r.url(); if (!/^(data:|about:|blob:)/.test(u)) ext.add(u.slice(0, 120)); });
  page.on('console', x => { if (x.type() === 'error') cons.push(x.text().slice(0, 200)); });
  page.on('pageerror', e => cons.push(String(e.message).slice(0, 200)));
  const fr = {};
  try {
    // ---- desktop, paper + night
    await page.setViewport({width: 1280, height: 800, deviceScaleFactor: 1});
    const t = await load(page, mod.doc, 820, 560, '#faf9f7'); m.load_ms = t;
    if (t < 0){ errors.push('模块 8 秒内没有加载完成（脚本死循环或语法错误？）'); }
    else {
      fr.p0 = await frame(page, 0, 'paper', W); fr.p5 = await frame(page, 0.5, 'paper', W); fr.p1 = await frame(page, 1, 'paper', W);
      fr.p05 = await frame(page, 0.05, 'paper', W); fr.p95 = await frame(page, 0.95, 'paper', W);
      const pr = await probe(page); m.desktop = pr && {sw: pr.sw, iw: pr.iw, nodes: pr.nodes};
      if (pr && pr.sw > pr.iw + 1) errors.push(`桌面宽度 ${pr.iw}px 下横向溢出（内容宽 ${pr.sw}px）`);
      if (pr && pr.nodes > LIMITS.nodes) errors.push(`DOM 节点 ${pr.nodes} 个，太多（≤${LIMITS.nodes}）`);
      // perf sweep
      await page.evaluate(() => send({t: 'perf-start'}));
      const NS = fast ? 30 : 90;
      for (let i = 0; i <= NS; i++){ await page.evaluate(p => send({p}), i / NS); await sleep(16); }
      await page.evaluate(() => send({t: 'perf-stop'})); await sleep(200);
      const perf = await page.evaluate(() => [...MSGS].reverse().find(x => x.t === 'perf') || null); m.perf = perf;
      if (perf){
        if (perf.longtask_max > LIMITS.longtask_max) errors.push(`滚动时出现 ${perf.longtask_max}ms 的长任务（上限 ${LIMITS.longtask_max}ms）：回调里不要做重计算/重建 DOM`);
        if (perf.longtask_ms > LIMITS.longtask_ms) errors.push(`滚动扫动期间长任务累计 ${perf.longtask_ms}ms（上限 ${LIMITS.longtask_ms}ms）`);
        if (perf.p95 > LIMITS.p95) errors.push(`帧间隔 p95 = ${Math.round(perf.p95)}ms（上限 ${LIMITS.p95}ms），动画太重`);
      }
      if (!fast) fr.n5 = await frame(page, 0.5, 'night', W);
      fr.n1 = await frame(page, 1, 'night', W);
    }
    // ---- 恶劣条件 1：iframe 以 0×0 启动、浏览器不补发 resize（用户真实浏览器里出现过：元素全挤在左上角）
    if (t >= 0 && fr.p1){
      if (await hostileLoad(page, mod.doc, 820, 560, '#faf9f7')){
        fr.h1 = await frame(page, 1, 'paper', W);
        const pr = await probe(page); m.hostile = {iw: pr && pr.iw, ih: pr && pr.ih, changed_vs_normal: +changed(fr.p1.sig, fr.h1.sig).toFixed(4)};
        // 变化面积超过 6%，或超过画面主体面积的一半（小主体整个挪了位置也算）
        const moved = (a, b, c) => c > 0.06 || (c > 0.004 && c > 0.5 * Math.max(a.nonblank, b.nonblank));
        if (moved(fr.p1, fr.h1, m.hostile.changed_vs_normal)) errors.push(`iframe 以 0×0 启动、之后才长到真实尺寸时画面没有重新布局（和正常加载相比 ${(m.hostile.changed_vs_normal * 100).toFixed(1)}% 的画面不同）：尺寸为 0 时要延迟初始化，并用 ResizeObserver 监听尺寸变化`);
      } else errors.push('iframe 以 0×0 启动时模块没有加载完成');
      // ---- 恶劣条件 2：父页面的进度消息一直没到（file:// 打开、消息被丢）→ 至少要显示终态画面，不能空白
      if (await load(page, mod.doc, 820, 560, '#faf9f7') >= 0){
        await sleep(2300); const el = await page.$('#f');
        fr.q = await page.evaluate(b => analyze(b), await el.screenshot({encoding: 'base64'}));
        for (let k = 0; k < 2 && (fr.q.nonblank < LIMITS.nonblank_mid || changed(fr.p1.sig, fr.q.sig) > 0.06); k++){   // 慢机器：再等等重截
          await sleep(900); fr.q = await page.evaluate(b => analyze(b), await el.screenshot({encoding: 'base64'})); }
        m.no_progress = {nonblank: +fr.q.nonblank.toFixed(4), changed_vs_final: +changed(fr.p1.sig, fr.q.sig).toFixed(4)};
        const c = m.no_progress.changed_vs_final;
        if (fr.q.nonblank < LIMITS.nonblank_mid || c > 0.06 || (c > 0.004 && c > 0.5 * Math.max(fr.p1.nonblank, fr.q.nonblank))) errors.push('收不到父页面的进度消息时没有显示终态画面（应在约 1.5 秒后自行兜底到 p=1）');
      }
    }
    // ---- mobile
    await page.setViewport({width: 390, height: 844, deviceScaleFactor: 1});
    const t2 = await load(page, mod.doc, 358, 440, '#faf9f7');
    if (t2 >= 0){
      if (!fast) fr.m5 = await frame(page, 0.5, 'paper', W);
      fr.m1 = await frame(page, 1, 'paper', W);
      const pr = await probe(page); m.mobile = pr && {sw: pr.sw, iw: pr.iw};
      if (pr && pr.sw > pr.iw + 1) errors.push(`手机宽度 ${pr.iw}px 下横向溢出（内容宽 ${pr.sw}px）：用 viewBox 缩放，别用固定像素宽度`);
    } else if (t >= 0) errors.push('手机尺寸下模块没有加载完成');
    // ---- reduced motion（fast 模式跳过：原语库在 VRH.reduced 下不起循环，库测试里整体测过）
    if (!fast){
    await page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}]);
    await page.setViewport({width: 1280, height: 800, deviceScaleFactor: 1});
    const t3 = await load(page, mod.doc, 820, 560, '#faf9f7');
    if (t3 >= 0){
      const a = await frame(page, 1, 'paper', 300); const c = await frame(page, 1, 'paper', 900);
      m.reduced_diff = +sigDiff(a.sig, c.sig).toFixed(4); fr.r1 = a;
      if (m.reduced_diff > LIMITS.reduced_diff) errors.push('减弱动效（prefers-reduced-motion）下画面仍在自己动：VRH.reduced 为 true 时不要启动 requestAnimationFrame/setInterval 循环');
    }
    await page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'no-preference'}]);
    }
  } catch (e) { errors.push('校验过程异常：' + String(e.message || e).slice(0, 160)); }
  const msgs = await page.evaluate(() => MSGS).catch(() => []);
  await page.close().catch(() => {}); await ctx.close().catch(() => {}); mod._page = null; mod._ctx = null;
  // ---- verdicts
  const lines = mod.doc.split('\n');
  const where = x => { if (!x.line || !lines[x.line - 1]) return ''; const L = lines[x.line - 1], c = Math.max(0, (x.col || 1) - 1);
    return `（出错代码附近：${L.slice(Math.max(0, c - 70), c + 50).trim()}）`; };
  const jsErr = [...new Set(msgs.filter(x => x.t === 'error').map(x => x.msg + where(x)).concat(cons.filter(c => !/Content Security Policy/.test(c))))];
  if (jsErr.length) errors.unshift('JS 报错：' + jsErr.slice(0, 4).join(' | '));
  const csp = msgs.filter(x => x.t === 'csp').map(x => `${x.dir} ${x.uri}`);
  if (csp.length || cons.some(c => /Content Security Policy/.test(c))) errors.push('尝试加载外部/被禁止的资源（CSP 拦截）：' + [...new Set(csp)].slice(0, 3).join(' | '));
  if (ext.size) errors.push('发出了外部请求：' + [...ext].slice(0, 3).join(' | '));
  const nb = k => fr[k] ? +fr[k].nonblank.toFixed(4) : null;
  m.nonblank = {p0: nb('p0'), p5: nb('p5'), p1: nb('p1'), n5: nb('n5'), n1: nb('n1'), m5: nb('m5'), m1: nb('m1'), r1: nb('r1')};
  m.bg_lum = {paper: fr.p5 && fr.p5.bg_lum, night: (fr.n5 || fr.n1) && (fr.n5 || fr.n1).bg_lum};
  if (fr.p0 && fr.p0.nonblank < LIMITS.nonblank_start) errors.push('p=0 时画面几乎空白：开场就要有构图');
  for (const [k, label] of [['p5', '纸面主题 p=0.5'], ['p1', '纸面主题 p=1'], ['n5', '夜间主题 p=0.5'], ['n1', '夜间主题 p=1'], ['m5', '手机 p=0.5'], ['m1', '手机 p=1']])
    if (fr[k] && fr[k].nonblank < LIMITS.nonblank_mid) errors.push(`${label} 画面几乎空白（有效像素 ${(fr[k].nonblank * 100).toFixed(1)}%）：线条颜色要用 var(--ink)，并确认元素在可视区内`);
  const cov = ['p05', 'p5', 'p95', 'p1'].filter(k => fr[k]).map(k => fr[k].nonblank);
  if (cov.length){ m.coverage = +(cov.reduce((a, b) => a + b, 0) / cov.length).toFixed(4);
    if (m.coverage < LIMITS.coverage) errors.push(`画面非空白占比太低（全程平均 ${(m.coverage * 100).toFixed(1)}%，至少 ${LIMITS.coverage * 100}%）：主体太小或太淡`); }
  m.contrast = {p1: fr.p1 && fr.p1.contrast, n1: fr.n1 && fr.n1.contrast, p5: fr.p5 && fr.p5.contrast};
  for (const [k, label] of [['p1', '纸面主题终态'], ['n1', '夜间主题终态']])
    if (fr[k] && fr[k].nonblank >= LIMITS.nonblank_mid && fr[k].contrast < LIMITS.contrast) errors.push(`${label}对比度只有 ${fr[k].contrast}:1（至少 ${LIMITS.contrast}:1）：主体颜色和背景太接近`);
  const nb5 = fr.n5 || fr.n1;
  if (nb5 && nb5.bg_lum > LIMITS.night_bg_lum) errors.push('夜间主题下背景仍是浅色：背景请用 var(--bg)，不要写死颜色');
  if (fr.p05 && fr.p95 && fr.p5){ m.scroll_changed = +changed(fr.p05.sig, fr.p95.sig).toFixed(4); m.scroll_changed_mid = +changed(fr.p05.sig, fr.p5.sig).toFixed(4);
    if (m.scroll_changed < LIMITS.scroll_changed) errors.push(`画面随滚动变化太小（p=0.05→0.95 只有 ${(m.scroll_changed * 100).toFixed(1)}% 的画面变化，至少要 ${LIMITS.scroll_changed * 100}%）：用 VRH.draw 逐笔画出、VRH.camera 推近、人物移动、光影变化，让每个节拍一眼可见`); }
  if (fr.p5 && fr.n5) m.theme_diff = +sigDiff(fr.p5.sig, fr.n5.sig).toFixed(4);
  return {id: mod.id, ok: errors.length === 0, errors, metrics: m};
}

const LAUNCH = () => puppeteer.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true,
  args: ['--no-sandbox', '--disable-features=IsolateOrigins,site-per-process', '--disable-gpu', '--disable-dev-shm-usage']});

// 单个模块带超时：超时就关掉它的页面，返回超时结果；浏览器本身挂了由调用方重启
async function checkTimed(b, mod, fast, ms){
  let timer;
  const to = new Promise(res => { timer = setTimeout(() => res('__timeout__'), ms); });
  const r = await Promise.race([checkOne(b, mod, fast).catch(e => ({id: mod.id, ok: false, errors: ['校验过程异常：' + String(e.message || e).slice(0, 160)], metrics: {}})), to]);
  clearTimeout(timer);
  if (r === '__timeout__'){
    if (mod._ctx) await Promise.race([mod._ctx.close().catch(() => {}), sleep(5000)]);
    else if (mod._page) await Promise.race([mod._page.close().catch(() => {}), sleep(3000)]);
    return {id: mod.id, ok: false, timeout: true, errors: [`校验超时（单个模块 ${Math.round(ms / 1000)}s）`], metrics: {}};
  }
  return r;
}

async function serve(){
  const arg = k => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
  const C = Math.max(1, parseInt(arg('--concurrency') || '2', 10)); const T = 1000 * parseFloat(arg('--timeout') || '40');
  const FAST = process.argv.includes('--fast');
  let b = await LAUNCH(); const queue = []; let active = 0, closing = false;
  const out = o => process.stdout.write(JSON.stringify(o) + '\n');
  const ensure = async () => { if (!b.connected){ try { await b.close(); } catch (e) {} b = await LAUNCH(); } };
  const pump = () => {
    while (active < C && queue.length){
      const mod = queue.shift(); active++;
      (async () => {
        try { await ensure(); } catch (e) { out({id: mod.id, ok: false, infra: true, errors: ['校验器无法启动浏览器：' + String(e.message).slice(0, 120)], metrics: {}}); return; }
        out({event: 'start', id: mod.id});
        const r = await checkTimed(b, mod, mod.fast === undefined ? FAST : !!mod.fast, mod.timeout ? mod.timeout * 1000 : T);
        delete r._page; delete r._ctx; out(r);
      })().finally(() => { active--; pump(); if (closing && !active && !queue.length) b.close().finally(() => process.exit(0)); });
    }
  };
  require('readline').createInterface({input: process.stdin}).on('line', l => {
    l = l.trim(); if (!l) return;
    let mod; try { mod = JSON.parse(l); } catch (e) { return; }
    queue.push(mod); pump();
  }).on('close', () => { closing = true; if (!active && !queue.length) b.close().finally(() => process.exit(0)); });
  out({event: 'ready', concurrency: C});
}

if (process.argv.includes('--serve')) serve().catch(e => { process.stdout.write(JSON.stringify({event: 'fatal', error: String(e.message || e)}) + '\n'); process.exit(1); });
else (async () => {
  const mods = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const b = await LAUNCH();
  const out = [];
  for (const mod of mods){ const r = await checkTimed(b, mod, !!mod.fast, (mod.timeout || 120) * 1000); delete r._page; out.push(r); }
  await b.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify([{id: '*', ok: false, errors: ['校验器崩溃：' + e.message]}])); process.exit(1); });
