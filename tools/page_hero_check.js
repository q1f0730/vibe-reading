// node page_hero_check.js page.html|http://… [outdir] [--max 6] [--full] [--firefox]
// 整页里的关键场景动画，按“用户真实浏览器”的条件逐个实测（单个模块的 hero_check.js 测不到宿主页面这一层）：
//   · file:// 直接打开（父页面 origin = 'null'）或 http 打开；
//   · 不调用 VR.renderAll：像读者一样切到章节、用滚轮自然滚到 hero，让 IntersectionObserver 懒加载 iframe；
//   · 窗口不是 1280：1000×650 @DPR1.5（--full 再加 1000×650@1、1366×768@2、390×844@3）；
//   · 恶劣条件：iframe 先以 0×0 挂上、并屏蔽 iframe 里浏览器原生的 resize 事件，加载后才撑开（Firefox / 进程隔离 iframe 的真实情况）；
//   · 滚到一半改窗口尺寸，看是否按新尺寸重排。
// 每个 hero 检查：#vrp 的布局尺寸 = iframe 视口且 > 0；画框截图非空白；元素不全挤在左上角。
// outdir 给了就把每张帧存下来。最后一行输出 JSON：{ok, checked, failures:[…], shots:[…]}
const puppeteer = require('puppeteer-core'); const fs = require('fs'); const path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const argv = process.argv.slice(2), flag = k => argv.includes(k), opt = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d; };
const pos = argv.filter((a, i) => !a.startsWith('--') && !(i > 0 && argv[i - 1] === '--max'));
const [target, outdir] = pos; const MAX = +opt('--max', 6), FF = flag('--firefox');
const url = /^https?:/.test(target) ? target : 'file://' + path.resolve(target);
const CONDS = flag('--full') ? [[1000, 650, 1.5, true], [1000, 650, 1, false], [1366, 768, 2, true], [390, 844, 3, true]] : [[1000, 650, 1.5, true]];

// 注入到每个文档（含沙箱 srcdoc iframe）：iframe 里屏蔽浏览器原生 resize；父页面里让未“放行”的 hero iframe 保持 0×0
const INJECT = `(() => {
  if (window !== window.top){
    const a = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function(t, f, o){ if (t === 'resize' && this === window && typeof f === 'function'){ const g = f; f = function(e){ if (e && e.isTrusted) return; return g.apply(this, arguments); }; } return a.call(this, t, f, o); };
    try { Object.defineProperty(window, 'onresize', {set(){}, get(){ return null; }, configurable: true}); } catch(e){}
  } else if (window.__HOSTILE__ !== false){
    document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('style'); s.id = '__zero';
      s.textContent = '.hs-frame iframe:not([data-sized]){width:0!important;height:0!important}'; document.head.appendChild(s); });
  }
})();`;

(async () => {
  const b = await puppeteer.launch(FF ? {browser: 'firefox', executablePath: process.env.FIREFOX_PATH || '/usr/bin/firefox-esr', headless: true}
    : {executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const failures = [], shots = []; let checked = 0;
  if (outdir) fs.mkdirSync(outdir, {recursive: true});
  for (const [w, h, dpr, hostile] of CONDS){
    const tag = `${w}x${h}@${dpr}${hostile ? '-hostile' : ''}`;
    const p = await b.newPage(); await p.setViewport({width: w, height: h, deviceScaleFactor: dpr});
    const errs = []; p.on('pageerror', e => errs.push(String(e.message).slice(0, 160)));
    if (hostile) await p.evaluateOnNewDocument(INJECT);
    await p.goto(url, {waitUntil: 'load', timeout: 120000});
    await p.evaluate(() => { try { localStorage.clear(); } catch(e){} });
    // 要测的 hero：按章节均匀取样
    const plan = await p.evaluate(max => {
      const secs = [...document.querySelectorAll('.chapter')];
      const all = secs.length ? secs.flatMap((c, k) => { const src = c.querySelector('template') ? c.querySelector('template').innerHTML : c.innerHTML;
        return [...src.matchAll(/data-hs="([^"]+)"/g)].map(m => [k, m[1]]); }) : [...document.querySelectorAll('.hs')].map(e => [-1, e.dataset.hs]);
      if (all.length <= max) return all; const step = all.length / max; return Array.from({length: max}, (_, i) => all[Math.floor(i * step)]);
    }, MAX);
    let resized = false;
    for (let n = 0; n < plan.length; n++){
      const [k, id] = plan[n];
      // 像读者一样：切章（只挂这一章），从这个 hero 前面约 1.5 屏开始用滚轮往下滚，让懒加载自然发生
      await p.evaluate((k, id) => { if (k >= 0 && window.VR) VR.show(k); const el = document.querySelector(`.hs[data-hs="${id}"]`);
        scrollTo({top: el.getBoundingClientRect().top + scrollY - innerHeight * 1.6, behavior: 'instant'}); }, k, id);
      await sleep(250);
      const vw = (await p.evaluate(() => innerWidth)), vh = await p.evaluate(() => innerHeight);
      for (let i = 0; i < 40; i++){
        await p.mouse.move(vw / 2, vh / 2); await p.mouse.wheel({deltaY: 160}); await sleep(45);
        const st = await p.evaluate(id => { const el = document.querySelector(`.hs[data-hs="${id}"]`), r = el.getBoundingClientRect();
          return {mounted: !!el._f, prog: -r.top / Math.max(1, r.height - innerHeight * 0.75)}; }, id);
        if (st.mounted && hostile) await p.evaluate(id => { const el = document.querySelector(`.hs[data-hs="${id}"]`);   // iframe 加载完后才撑开
          if (el._f && !el._f.dataset.sized && !el._f.__t) el._f.__t = setTimeout(() => { el._f.dataset.sized = '1'; }, 400); }, id);
        if (st.prog > 0.55) break;
      }
      if (!resized && n === Math.floor(plan.length / 2)){   // 中途改窗口尺寸
        resized = true; await p.setViewport({width: Math.round(w * 1.18), height: Math.round(h * 1.1), deviceScaleFactor: dpr}); await sleep(500);
      }
      await sleep(1000);
      const fh = await p.$(`.hs[data-hs="${id}"] .hs-frame`);
      const ifr = await p.$(`.hs[data-hs="${id}"] iframe`);
      const fail = m => failures.push(`${tag} ${id}: ${m}`);
      checked++;
      if (!ifr){ fail('iframe 没有挂载（懒加载没触发）'); continue; }
      const fr = await ifr.contentFrame();
      let info = null;
      try { info = await fr.evaluate(() => { const r = document.getElementById('vrp'); if (!r) return {novrp: true, iw: innerWidth, ih: innerHeight};
        // 可见元素的包围盒：全挤在左上角 = 按 0×0 布局了
        let maxR = 0, maxB = 0; r.querySelectorAll('.vl *').forEach(e => { const q = e.getBoundingClientRect(); if (q.width * q.height > 0 && getComputedStyle(e).visibility !== 'hidden'){ maxR = Math.max(maxR, q.right); maxB = Math.max(maxB, q.bottom); } });
        return {iw: innerWidth, ih: innerHeight, w: +r.getAttribute('data-w') || 0, h: +r.getAttribute('data-h') || 0, maxR, maxB}; }); }
      catch (e){ fail('读不到 iframe 内部：' + String(e.message).slice(0, 80)); }
      if (info){
        if (info.novrp) { /* 代码模式模块：只做截图检查 */ }
        else if (!(info.w > 0 && info.h > 0)) fail(`没有完成布局（布局尺寸 ${info.w}×${info.h}，iframe 视口 ${info.iw}×${info.ih}）`);
        else if (Math.abs(info.w - info.iw) > 1 || Math.abs(info.h - info.ih) > 1) fail(`布局尺寸 ${info.w}×${info.h} 与 iframe 视口 ${info.iw}×${info.ih} 不一致（尺寸变化后没有重排）`);
        else if (info.maxR < info.iw * 0.25 && info.maxB < info.ih * 0.25) fail(`元素全挤在左上角（包围盒右下角 ${Math.round(info.maxR)},${Math.round(info.maxB)}）`);
      }
      const buf = await fh.screenshot();
      if (outdir){ const f = path.join(outdir, `${id}-${tag}${resized ? '-resized' : ''}.png`); fs.writeFileSync(f, buf); shots.push(f); }
      const nb = await p.evaluate(async b64 => { const im = new Image(); await new Promise(r => { im.onload = r; im.src = 'data:image/png;base64,' + b64; });
        const c = document.createElement('canvas'); c.width = 160; c.height = Math.max(8, Math.round(160 * im.height / im.width)); const x = c.getContext('2d'); x.drawImage(im, 0, 0, c.width, c.height);
        const d = x.getImageData(0, 0, c.width, c.height).data, hist = new Map(); for (let i = 0; i < d.length; i += 4){ const k = (d[i] >> 4) << 8 | (d[i+1] >> 4) << 4 | (d[i+2] >> 4); hist.set(k, (hist.get(k) || 0) + 1); }
        let bk = 0, bc = -1; hist.forEach((v, k) => { if (v > bc){ bc = v; bk = k; } }); const bg = [((bk >> 8) & 15) * 16 + 8, ((bk >> 4) & 15) * 16 + 8, (bk & 15) * 16 + 8];
        let ink = 0; for (let i = 0; i < d.length; i += 4) if (Math.abs(d[i] - bg[0]) + Math.abs(d[i+1] - bg[1]) + Math.abs(d[i+2] - bg[2]) > 40) ink++; return ink / (d.length / 4); }, buf.toString('base64'));
      if (nb < 0.004) fail(`画框几乎空白（有效像素 ${(nb * 100).toFixed(1)}%）`);
    }
    if (errs.length) failures.push(`${tag}: 页面 JS 报错 ${errs.slice(0, 2).join(' | ')}`);
    await p.close();
  }
  await b.close();
  console.log(JSON.stringify({ok: failures.length === 0 && checked > 0, checked, failures, shots}));
})().catch(e => { console.log(JSON.stringify({ok: false, fatal: String(e && e.message || e)})); process.exit(1); });
