// node verify.js page.html segments.json  → 最后一行输出 JSON 报告
// 检查：[data-src] 文本与原文逐段 100% 一致且顺序完整；无外部请求；无 JS 报错；1280/390 无横向溢出。
const puppeteer = require('puppeteer-core'); const fs = require('fs');
(async () => {
  const [html, segPath] = process.argv.slice(2);
  const segs = JSON.parse(fs.readFileSync(segPath, 'utf8')).segments;
  const b = await puppeteer.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox']});
  const rep = {ok: false, segments: segs.length};
  const errs = [], ext = [];
  const p = await b.newPage();
  p.on('pageerror', e => errs.push(e.message));
  p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('request', r => { const u = r.url(); if (!u.startsWith('file:') && !u.startsWith('data:') && !u.startsWith('blob:')) ext.push(u); });
  await p.setViewport({width: 1280, height: 800});
  await p.goto('file://' + html, {waitUntil: 'load', timeout: 120000});
  // 整本书分章模式：其余章节在 <template> 里，先全部挂进 DOM 再逐段比对
  rep.chapter_mode = await p.evaluate(() => !!(window.VR && STATE.chapters));
  if (rep.chapter_mode) await p.evaluate(() => VR.renderAll());
  const got = await p.$$eval('[data-src]', els => els.map(e => [+e.dataset.src, e.textContent]));
  const mism = [];
  if (got.length !== segs.length) mism.push(`count ${got.length} != ${segs.length}`);
  got.forEach(([i, t], k) => { if (i !== k) mism.push(`order@${k}: got ${i}`); else if (t !== segs[i].text) mism.push(`text@${i}`); });
  rep.text_identical = mism.length === 0; rep.mismatches = mism.slice(0, 20);
  rep.matched = got.filter(([i, t]) => segs[i] && t === segs[i].text).length;
  // scroll through the whole page so every reveal / observer runs
  const H = await p.evaluate(() => document.documentElement.scrollHeight);
  const step = Math.max(700, Math.ceil(H / (rep.chapter_mode ? 80 : 300)));   // 大页面限制步数（整本书分章模式全挂载后很长）
  for (let y = 0; y < H; y += step) { await p.evaluate(v => scrollTo(0, v), y); await new Promise(r => setTimeout(r, 40)); }
  await new Promise(r => setTimeout(r, 600));
  rep.overflow = {};
  for (const [w, h] of [[1280, 800], [390, 844]]) {
    await p.setViewport({width: w, height: h, deviceScaleFactor: 1});
    await new Promise(r => setTimeout(r, 300));
    rep.overflow[w] = await p.evaluate(() => {
      const sw = document.documentElement.scrollWidth, iw = innerWidth;
      const wide = [...document.querySelectorAll('main *')].filter(e => !e.closest('svg') && !e.closest('.night-band')).filter(e => { const r = e.getBoundingClientRect(); return r.width && (r.right > iw + 1 || r.left < -1); })
        .slice(0, 5).map(e => e.tagName + '.' + (e.className.baseVal ?? e.className));
      return {scrollWidth: sw, innerWidth: iw, ok: sw <= iw, offenders: wide};
    });
  }
  rep.external_requests = ext; rep.js_errors = errs;
  rep.ok = rep.text_identical && !ext.length && !errs.length && rep.overflow[1280].ok && rep.overflow[390].ok;
  await b.close();
  console.log(JSON.stringify(rep));
})().catch(e => { console.log(JSON.stringify({ok: false, fatal: String(e)})); process.exit(1); });
