// node perf.js page.html [--mobile]  → 最后一行输出 JSON：加载耗时 / 首次内容绘制 / JS 堆 / DOM 节点数 / 滚动全书后的内存
// 用于大文件（整本书）阅读页的性能回归，不检查内容（内容见 verify.js）。
const puppeteer = require('puppeteer-core'); const fs = require('fs');
(async () => {
  const [html, ...flags] = process.argv.slice(2);
  const mobile = flags.includes('--mobile');
  const b = await puppeteer.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true,
                                    args: ['--no-sandbox', '--enable-precise-memory-info']});
  const p = await b.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.setViewport(mobile ? {width: 390, height: 844, deviceScaleFactor: 1} : {width: 1280, height: 800});
  const t0 = Date.now();
  await p.goto('file://' + html, {waitUntil: 'load', timeout: 120000});
  const wall = Date.now() - t0;
  await new Promise(r => setTimeout(r, 500));
  const nav = await p.evaluate(() => {
    const n = performance.getEntriesByType('navigation')[0] || {};
    const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime;
    return {dcl_ms: Math.round(n.domContentLoadedEventEnd || 0), load_ms: Math.round(n.loadEventEnd || 0), fcp_ms: Math.round(fcp || 0),
            nodes: document.getElementsByTagName('*').length, rendered_paras: document.querySelectorAll('main [data-src]').length,
            height: document.documentElement.scrollHeight};
  });
  const m0 = await p.metrics();
  // 滚过全书（模拟连续阅读）
  // 均匀跳 40 个位置（模拟连续阅读 + 目录跳转），记录每步耗时
  let steps = 0; const per = [];
  const t1 = Date.now();
  for (let k = 0; k <= 40; k++) {
    const H = await p.evaluate(() => document.documentElement.scrollHeight - innerHeight);
    const s = Date.now();
    await p.evaluate(v => new Promise(r => { scrollTo(0, v); requestAnimationFrame(() => requestAnimationFrame(r)); }), Math.round(H * k / 40));
    per.push(Date.now() - s); steps++;
  }
  const scrollMs = Date.now() - t1;
  await new Promise(r => setTimeout(r, 800));
  const m1 = await p.metrics();
  const after = await p.evaluate(() => ({nodes: document.getElementsByTagName('*').length,
                                          rendered_paras: document.querySelectorAll('main [data-src]').length,
                                          height: document.documentElement.scrollHeight}));
  const rep = {file_kb: Math.round(fs.statSync(html).size / 1024), viewport: mobile ? '390x844' : '1280x800', wall_load_ms: wall, ...nav,
               heap_mb: +(m0.JSHeapUsedSize / 1048576).toFixed(1), layout_ms: Math.round(m0.LayoutDuration * 1000),
               script_ms: Math.round(m0.ScriptDuration * 1000),
               after_scroll: {...after, heap_mb: +(m1.JSHeapUsedSize / 1048576).toFixed(1), scroll_steps: steps, scroll_ms: scrollMs, step_ms_max: Math.max(...per), step_ms_avg: Math.round(per.reduce((a,b)=>a+b,0)/per.length)},
               js_errors: errs};
  await b.close();
  console.log(JSON.stringify(rep));
})().catch(e => { console.log(JSON.stringify({fatal: String(e)})); process.exit(1); });
