// node shots.js page.html outdir   → 桌面首屏 / 中段 / 对话段 / 脸红段 / 夜间主题 / 手机首屏 / 手机中段 / 手机对话
const puppeteer = require('puppeteer-core'); const fs = require('fs');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function scrollToY(p, target) {
  let y = await p.evaluate(() => scrollY);
  while (Math.abs(y - target) > 1) { y += Math.sign(target - y) * Math.min(400, Math.abs(target - y)); await p.evaluate(v => scrollTo(0, v), y); await sleep(50); }
  await sleep(2600);
}
// pickers return a document Y to put at the top of the viewport
const PICK = {
  mid: () => document.documentElement.scrollHeight * 0.45,
  dialogue: () => { const L = [...document.querySelectorAll('p[data-src]')];
    let best = null, run = 0;
    for (let k = 0; k < L.length; k++) { run = L[k].classList.contains('say') ? run + 1 : 0; if (run >= 4) { best = L[k - 2]; break; } }
    best = best || document.querySelector('.utt') || L[Math.floor(L.length / 2)];
    return best.getBoundingClientRect().top + scrollY - innerHeight * 0.5; },
  blush: () => { const P = STATE.paras; let k = 0; P.forEach((p, i) => { if (p.bl > P[k].bl) k = i; });
    const el = document.querySelector(`[data-src="${k}"]`); return el.getBoundingClientRect().top + scrollY - innerHeight * 0.5; },
  figure: () => { const F = [...document.querySelectorAll('figure.fig')]; const f = F[Math.floor(F.length / 2)] || F[0];
    return f ? f.getBoundingClientRect().top + scrollY - innerHeight * 0.18 : 0; },
  late: () => { const F = [...document.querySelectorAll('figure.fig')]; const f = F[F.length - 1];
    return f ? f.getBoundingClientRect().top + scrollY - innerHeight * 0.15 : document.documentElement.scrollHeight * 0.85; },
};
(async () => {
  const [html, out] = process.argv.slice(2); fs.mkdirSync(out, {recursive: true});
  const b = await puppeteer.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox']});
  const jobs = [
    ['01-desktop-hero', 1280, 800, 1, null],
    ['02-desktop-mid', 1280, 800, 1, 'mid'],
    ['03-desktop-dialogue', 1280, 800, 1, 'dialogue'],
    ['04-mobile-figure', 390, 844, 2, 'figure'],
    ['05-desktop-blush', 1280, 800, 1, 'blush'],
    ['06-desktop-night-theme', 1280, 800, 1, 'figure', 'night'],
    ['07-mobile-hero', 390, 844, 2, null],
    ['08-mobile-dialogue', 390, 844, 2, 'dialogue'],
    ['09-desktop-late', 1280, 800, 1, 'late'],
  ];
  for (const [name, w, h, dpr, pick, theme] of jobs) {
    const p = await b.newPage(); await p.setViewport({width: w, height: h, deviceScaleFactor: dpr});
    await p.goto('file://' + html, {waitUntil: 'load'});
    await p.evaluate(t => { localStorage.clear(); if (t) { const k = 'vibereading:' + STATE.key + ':theme'; localStorage.setItem(k, JSON.stringify(t)); } }, theme || null);
    await p.reload({waitUntil: 'load'}); await sleep(3400);
    if (pick) { const y = await p.evaluate(PICK[pick]); await scrollToY(p, Math.max(0, y)); }
    await p.screenshot({path: `${out}/${name}.png`}); await p.close(); console.log(name);
  }
  await b.close();
})();
