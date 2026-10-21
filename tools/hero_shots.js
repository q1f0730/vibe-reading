// node hero_shots.js page.html outdir  → 每个关键场景动画在桌面 1280 / 手机 390 下各截 3 帧（滚动进度 ≈ 0.1 / 0.5 / 0.9），
// 另加一张桌面夜间主题 p≈0.7。最后一行输出 JSON：[{id, files:[…]}]
const puppeteer = require('puppeteer-core'); const fs = require('fs'); const path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const [html, out] = process.argv.slice(2); fs.mkdirSync(out, {recursive: true});
  const b = await puppeteer.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true,
    args: ['--no-sandbox', '--disable-features=IsolateOrigins,site-per-process']});
  const res = {};
  for (const [w, h, tag] of [[1280, 800, 'desktop'], [390, 844, 'mobile']]){
    const p = await b.newPage(); await p.setViewport({width: w, height: h, deviceScaleFactor: tag === 'mobile' ? 2 : 1});
    await p.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch(e){} });
    await p.goto('file://' + path.resolve(html), {waitUntil: 'load'});
    await p.addStyleTag({content: '.toast{display:none!important}'});
    await p.evaluate(() => { try { localStorage.clear(); } catch(e){} document.body.classList.remove('theme-night'); });
    if (await p.evaluate(() => !!(window.VR && STATE.chapters))) await p.evaluate(() => VR.renderAll());
    const ids = await p.$$eval('.hs', els => els.map(e => e.dataset.hs));
    for (const id of ids){
      const shots = tag === 'desktop' ? [[0.1, 'paper'], [0.5, 'paper'], [0.9, 'paper'], [0.7, 'night']] : [[0.1, 'paper'], [0.5, 'paper'], [0.9, 'paper']];
      for (const [prog, theme] of shots){
        await p.evaluate(t => { const n = t === 'night'; if (document.body.classList.contains('theme-night') !== n) document.getElementById('bTheme').click(); }, theme);
        // 先滚到附近让 iframe 挂载，再滚到目标进度
        await p.evaluate(id => document.querySelector(`.hs[data-hs="${id}"]`).scrollIntoView({block: 'start', behavior: 'instant'}), id);
        await sleep(700);
        await p.evaluate((id, prog) => { const el = document.querySelector(`.hs[data-hs="${id}"]`); const top = el.getBoundingClientRect().top + scrollY;
          scrollTo({top: top + prog * (el.offsetHeight - innerHeight * 0.75), behavior: 'instant'}); dispatchEvent(new Event('scroll')); }, id, prog);
        await sleep(900);
        const f = path.join(out, `${id}-${tag}-${theme === 'night' ? 'night-' : ''}p${String(prog).replace('.', '')}.png`);
        await p.screenshot({path: f}); (res[id] = res[id] || []).push(path.basename(f));
      }
      await p.evaluate(() => { if (document.body.classList.contains('theme-night')) document.getElementById('bTheme').click(); });
    }
    await p.close();
  }
  await b.close();
  console.log(JSON.stringify(Object.entries(res).map(([id, files]) => ({id, files}))));
})();
