// node prim_gallery.js in.json outdir [ps] → 每个 doc 在桌面 860×560 / 手机 358×440 的沙箱 iframe 里按进度截图。
// in.json = [{id, doc}]；ps 形如 "0.1,0.5,0.9"；另加桌面夜间 p=0.7。最后一行输出 JSON：[{id, files}]
const puppeteer = require('puppeteer-core'); const fs = require('fs'); const path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const harness = (w, h, bg) => `<!DOCTYPE html><html><body style="margin:0;background:${bg}"><iframe id="f" sandbox="allow-scripts" style="width:${w}px;height:${h}px;border:0;display:block"></iframe>
<script>window.R=0;addEventListener('message',e=>{if(e.data&&e.data.vrh&&e.data.t==='ready')R=1;if(e.data&&e.data.vrh&&e.data.t==='error')console.error('MODERR '+e.data.msg)});
window.send=m=>document.getElementById('f').contentWindow.postMessage(Object.assign({vrh:1},m),'*');</script></body></html>`;
(async () => {
  const [inp, out, psArg] = process.argv.slice(2); fs.mkdirSync(out, {recursive: true});
  const docs = JSON.parse(fs.readFileSync(inp, 'utf8')); const ps = (psArg || '0.1,0.5,0.9').split(',').map(Number);
  const b = await puppeteer.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-gpu']});
  const res = []; const errs = [];
  for (const d of docs){
    const files = [];
    for (const [w, h, tag, dpr] of [[860, 560, 'desktop', 1], [358, 440, 'mobile', 2]]){
      const p = await b.newPage(); await p.setViewport({width: w, height: h, deviceScaleFactor: dpr});
      p.on('console', m => { if (m.type() === 'error') errs.push(d.id + ' ' + m.text().slice(0, 200)); });
      p.on('pageerror', e => errs.push(d.id + ' ' + e.message));
      await p.setContent(harness(w, h, '#faf9f7')); await p.evaluate(doc => { document.getElementById('f').srcdoc = doc; }, d.doc);
      for (let i = 0; i < 60 && !(await p.evaluate(() => R)); i++) await sleep(50);
      const shots = ps.map(x => [x, 'paper']); if (tag === 'desktop') shots.push([0.7, 'night']);
      for (const [x, th] of shots){
        await p.evaluate((x, th) => send({p: x, theme: th, force: true}), x, th); await sleep(220);
        const f = path.join(out, `${d.id}-${tag}-${th === 'night' ? 'night-' : ''}p${String(x).replace('.', '')}.png`);
        await (await p.$('#f')).screenshot({path: f}); files.push(path.basename(f));
      }
      await p.close();
    }
    res.push({id: d.id, files});
  }
  await b.close();
  if (errs.length) console.error(errs.join('\n'));
  console.log(JSON.stringify(res));
})();
