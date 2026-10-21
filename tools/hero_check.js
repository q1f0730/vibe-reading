// node hero_check.js modules.json  → 最后一行输出 JSON：[{id, ok, errors:[], metrics:{}}]
// modules.json = [{id, doc}]，doc 是 hero.wrap() 生成的完整 srcdoc（CSP + VRH 运行时 + 模块）。
// 每个模块都放进与阅读页相同的 <iframe sandbox="allow-scripts" srcdoc> 里实测：
//   JS 报错 / CSP 违规 / 外部请求；桌面 820×560 与手机 358×480 横向溢出；明暗主题下 p=0/.5/1 非空白；
//   画面随滚动进度变化；夜间主题背景不是写死的浅色；进度扫动时的帧间隔与长任务；减弱动效下画面静止。
const puppeteer = require('puppeteer-core'); const fs = require('fs');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const LIMITS = { bytes: 40 * 1024, nonblank_mid: 0.008, nonblank_start: 0.004, scroll_changed: 0.025,
  longtask_max: 250, longtask_ms: 600, p95: 90, reduced_diff: 0.006, nodes: 4000, night_bg_lum: 0.45 };

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
  for (let i = 0; i < d.length; i += 4){ const dist = Math.abs(d[i]-bg[0]) + Math.abs(d[i+1]-bg[1]) + Math.abs(d[i+2]-bg[2]); if (dist > 40) ink++; if (dist > 110) strong++; }
  const cx = cv.getContext('2d'); cx.drawImage(im, 0, 0, W, H); const s = cx.getImageData(0, 0, W, H).data; const sig = [];
  for (let i = 0; i < s.length; i += 4) sig.push(Math.round(0.3 * s[i] + 0.59 * s[i+1] + 0.11 * s[i+2]));
  const lum = (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255;
  res({nonblank: ink / n, strong: strong / n, bg_lum: +lum.toFixed(3), sig}); }; im.src = 'data:image/png;base64,' + b64; });
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
async function frame(page, p, theme, wait = 260){
  await page.evaluate((p, theme) => send({p, theme, force: true}), p, theme);
  await sleep(wait);
  const el = await page.$('#f');
  const b64 = await el.screenshot({encoding: 'base64'});
  return page.evaluate(b => analyze(b), b64);
}
async function probe(page){
  await page.evaluate(() => send({t: 'probe'})); await sleep(120);
  return page.evaluate(() => [...MSGS].reverse().find(m => m.t === 'probe') || null);
}

async function checkOne(b, mod){
  const errors = [], m = {}, ext = new Set(), cons = [];
  const bytes = Buffer.byteLength(mod.doc); m.doc_bytes = bytes;
  const page = await b.newPage();
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
      fr.p0 = await frame(page, 0, 'paper'); fr.p5 = await frame(page, 0.5, 'paper'); fr.p1 = await frame(page, 1, 'paper');
      fr.p05 = await frame(page, 0.05, 'paper'); fr.p95 = await frame(page, 0.95, 'paper');
      const pr = await probe(page); m.desktop = pr && {sw: pr.sw, iw: pr.iw, nodes: pr.nodes};
      if (pr && pr.sw > pr.iw + 1) errors.push(`桌面宽度 ${pr.iw}px 下横向溢出（内容宽 ${pr.sw}px）`);
      if (pr && pr.nodes > LIMITS.nodes) errors.push(`DOM 节点 ${pr.nodes} 个，太多（≤${LIMITS.nodes}）`);
      // perf sweep
      await page.evaluate(() => send({t: 'perf-start'}));
      for (let i = 0; i <= 90; i++){ await page.evaluate(p => send({p}), i / 90); await sleep(16); }
      await page.evaluate(() => send({t: 'perf-stop'})); await sleep(200);
      const perf = await page.evaluate(() => [...MSGS].reverse().find(x => x.t === 'perf') || null); m.perf = perf;
      if (perf){
        if (perf.longtask_max > LIMITS.longtask_max) errors.push(`滚动时出现 ${perf.longtask_max}ms 的长任务（上限 ${LIMITS.longtask_max}ms）：回调里不要做重计算/重建 DOM`);
        if (perf.longtask_ms > LIMITS.longtask_ms) errors.push(`滚动扫动期间长任务累计 ${perf.longtask_ms}ms（上限 ${LIMITS.longtask_ms}ms）`);
        if (perf.p95 > LIMITS.p95) errors.push(`帧间隔 p95 = ${Math.round(perf.p95)}ms（上限 ${LIMITS.p95}ms），动画太重`);
      }
      fr.n5 = await frame(page, 0.5, 'night'); fr.n1 = await frame(page, 1, 'night');
    }
    // ---- mobile
    await page.setViewport({width: 390, height: 844, deviceScaleFactor: 1});
    const t2 = await load(page, mod.doc, 358, 440, '#faf9f7');
    if (t2 >= 0){
      fr.m5 = await frame(page, 0.5, 'paper'); fr.m1 = await frame(page, 1, 'paper');
      const pr = await probe(page); m.mobile = pr && {sw: pr.sw, iw: pr.iw};
      if (pr && pr.sw > pr.iw + 1) errors.push(`手机宽度 ${pr.iw}px 下横向溢出（内容宽 ${pr.sw}px）：用 viewBox 缩放，别用固定像素宽度`);
    } else if (t >= 0) errors.push('手机尺寸下模块没有加载完成');
    // ---- reduced motion
    await page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}]);
    await page.setViewport({width: 1280, height: 800, deviceScaleFactor: 1});
    const t3 = await load(page, mod.doc, 820, 560, '#faf9f7');
    if (t3 >= 0){
      const a = await frame(page, 1, 'paper', 300); const c = await frame(page, 1, 'paper', 900);
      m.reduced_diff = +sigDiff(a.sig, c.sig).toFixed(4); fr.r1 = a;
      if (m.reduced_diff > LIMITS.reduced_diff) errors.push('减弱动效（prefers-reduced-motion）下画面仍在自己动：VRH.reduced 为 true 时不要启动 requestAnimationFrame/setInterval 循环');
    }
    await page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'no-preference'}]);
  } catch (e) { errors.push('校验过程异常：' + String(e.message || e).slice(0, 160)); }
  const msgs = await page.evaluate(() => MSGS).catch(() => []);
  await page.close();
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
  m.bg_lum = {paper: fr.p5 && fr.p5.bg_lum, night: fr.n5 && fr.n5.bg_lum};
  if (fr.p0 && fr.p0.nonblank < LIMITS.nonblank_start) errors.push('p=0 时画面几乎空白：开场就要有构图');
  for (const [k, label] of [['p5', '纸面主题 p=0.5'], ['p1', '纸面主题 p=1'], ['n5', '夜间主题 p=0.5'], ['n1', '夜间主题 p=1'], ['m5', '手机 p=0.5'], ['m1', '手机 p=1']])
    if (fr[k] && fr[k].nonblank < LIMITS.nonblank_mid) errors.push(`${label} 画面几乎空白（有效像素 ${(fr[k].nonblank * 100).toFixed(1)}%）：线条颜色要用 var(--ink)，并确认元素在可视区内`);
  if (fr.n5 && fr.n5.bg_lum > LIMITS.night_bg_lum) errors.push('夜间主题下背景仍是浅色：背景请用 var(--bg)，不要写死颜色');
  if (fr.p05 && fr.p95 && fr.p5){ m.scroll_changed = +changed(fr.p05.sig, fr.p95.sig).toFixed(4); m.scroll_changed_mid = +changed(fr.p05.sig, fr.p5.sig).toFixed(4);
    if (m.scroll_changed < LIMITS.scroll_changed) errors.push(`画面随滚动变化太小（p=0.05→0.95 只有 ${(m.scroll_changed * 100).toFixed(1)}% 的画面变化，至少要 ${LIMITS.scroll_changed * 100}%）：用 VRH.draw 逐笔画出、VRH.camera 推近、人物移动、光影变化，让每个节拍一眼可见`); }
  if (fr.p5 && fr.n5) m.theme_diff = +sigDiff(fr.p5.sig, fr.n5.sig).toFixed(4);
  return {id: mod.id, ok: errors.length === 0, errors, metrics: m};
}

(async () => {
  const mods = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const b = await puppeteer.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true,
    args: ['--no-sandbox', '--disable-features=IsolateOrigins,site-per-process', '--disable-gpu']});
  const out = [];
  for (const mod of mods) out.push(await checkOne(b, mod));
  await b.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify([{id: '*', ok: false, errors: ['校验器崩溃：' + e.message]}])); process.exit(1); });
