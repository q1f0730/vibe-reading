// node tools/ui_shots.js http://127.0.0.1:PORT outdir
// 在一个干净的服务上造数据（注册 → plan.json 生成两本 + 一本失败），然后截首页/登录/注册/书架/进度页，桌面 1440 与手机 390。
const puppeteer = require('puppeteer-core'); const fs = require('fs'); const path = require('path');
const [base, out] = process.argv.slice(2); fs.mkdirSync(out, {recursive: true});
const ROOT = path.resolve(__dirname, '..');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let cookie = '';
async function api(method, p, body) {
  const r = await fetch(base + p, {method, headers: {'Content-Type': 'application/json', Cookie: cookie}, body: body ? JSON.stringify(body) : undefined});
  const sc = r.headers.get('set-cookie'); if (sc) cookie = sc.split(';')[0];
  return [r.status, await r.json().catch(() => null)];
}
async function waitJob(id) { for (;;) { const [, j] = await api('GET', '/api/jobs/' + id); if (j.status === 'done' || j.status === 'failed') return j; await sleep(500); } }
(async () => {
  const text = fs.readFileSync(path.join(ROOT, 'examples/kongyiji/kongyiji.txt'), 'utf8');
  const plan = JSON.parse(fs.readFileSync(path.join(ROOT, 'examples/kongyiji/kongyiji.plan.json'), 'utf8'));
  const mplan = JSON.parse(fs.readFileSync(path.join(ROOT, 'examples/kongyiji/kongyiji.mercury.plan.json'), 'utf8'));
  const user = 'q1f_demo' + Date.now() % 10000;
  let [st] = await api('POST', '/api/register', {username: user, password: 'demo-pass-2026'}); if (st !== 201) throw new Error('register ' + st);
  let [, a] = await api('POST', '/api/books', {name: 'kongyiji.txt', text, title: '孔乙己', author: '鲁迅', plan, verify: true}); await waitJob(a.job);
  let [, b] = await api('POST', '/api/books', {name: 'kongyiji.txt', text, title: '孔乙己 · Mercury 分镜', author: '鲁迅', plan: mplan, verify: false, theme: 'night'}); await waitJob(b.job);
  await api('POST', `/api/books/${b.book}/visibility`, {visibility: 'unlisted'});
  let [, c] = await api('POST', '/api/books', {name: 'draft.txt', text: '第一章\n\n这是一份分镜写坏了的草稿。', title: '一份写坏的分镜', plan: {scenes: 'oops'}, verify: false}); await waitJob(c.job);
  const br = await puppeteer.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox']});
  const errs = [];
  const views = {desktop: {width: 1440, height: 900, deviceScaleFactor: 1}, mobile: {width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true}};
  async function shot(name, url, {auth = false, full = true, before} = {}) {
    for (const [vn, vp] of Object.entries(views)) {
      const p = await br.newPage(); await p.setViewport(vp);
      p.on('pageerror', e => errs.push(`${name}/${vn}: ${e.message}`));
      p.on('console', m => { if (m.type() === 'error') errs.push(`${name}/${vn}: ${m.text()}`); });
      p.on('request', r => { if (!r.url().startsWith(base) && !r.url().startsWith('data:')) errs.push(`${name}/${vn}: external ${r.url()}`); });
      if (auth) { const [k, v] = cookie.split('='); await p.setCookie({name: k, value: v, url: base}); }
      await p.goto(base + url, {waitUntil: 'networkidle0'}); if (before) await before(p); await sleep(900);
      const ov = await p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      if (ov > 0) errs.push(`${name}/${vn}: horizontal overflow ${ov}px`);
      await p.screenshot({path: path.join(out, `${name}-${vn}.png`), fullPage: full}); await p.close();
    }
  }
  await shot('01-home', '/');
  await shot('02-login', '/login');
  await shot('03-register', '/register');
  await shot('04-home-member', '/', {auth: true});
  await shot('05-shelf', '/shelf', {auth: true});
  await shot('06-progress-done', '/jobs/' + a.job, {auth: true});
  // 进度页进行中：起一个带校验的任务，在“校验”阶段截图
  for (const [vn, vp] of Object.entries(views)) {
    let [, d] = await api('POST', '/api/books', {name: 'kongyiji.txt', text, title: '孔乙己（第二版）', author: '鲁迅', plan, verify: true});
    const p = await br.newPage(); await p.setViewport(vp); const [k, v] = cookie.split('='); await p.setCookie({name: k, value: v, url: base});
    await p.goto(base + '/jobs/' + d.job, {waitUntil: 'domcontentloaded'});
    await p.waitForFunction(() => document.querySelector('[data-step=verify].active'), {timeout: 20000}).catch(() => errs.push('progress: never saw verify active'));
    await sleep(1200);
    await p.screenshot({path: path.join(out, `07-progress-running-${vn}.png`), fullPage: true}); await p.close();
    await waitJob(d.job);
  }
  await shot('08-progress-failed', '/jobs/' + c.job, {auth: true});
  await shot('09-403', '/b/doesnotexist0', {});
  await br.close();
  console.log(JSON.stringify({user, out, errors: errs}));
})().catch(e => { console.error(e); process.exit(1); });
