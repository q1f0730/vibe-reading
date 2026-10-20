// VibeReading app shell — no dependencies.
(() => {
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const toast = (msg, ms = 2400) => { const t = $('#toast'); if (!t) return; t.textContent = msg; t.classList.add('on'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), ms); };
async function api(method, url, body) {
  const r = await fetch(url, {method, headers: body !== undefined ? {'Content-Type': 'application/json'} : {}, body: body !== undefined ? JSON.stringify(body) : undefined, credentials: 'same-origin'});
  let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) { const e = new Error(j.error || ('HTTP ' + r.status)); e.status = r.status; throw e; }
  return j;
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const pad = (n, w = 2) => String(n).padStart(w, '0');

// masthead clock (Asia/Shanghai, like a dateline)
const clk = $('[data-clock]');
if (clk) { const tick = () => { clk.textContent = new Date().toLocaleTimeString('zh-CN', {hour12: false, timeZone: 'Asia/Shanghai'}) + ' CST'; }; tick(); setInterval(tick, 1000); }

// logout
$$('[data-logout]').forEach(b => b.onclick = async () => { try { await api('POST', '/api/logout', {}); } catch (e) {} location.href = '/'; });

// ---------------- auth ----------------
const af = $('#authform');
if (af) af.onsubmit = async e => {
  e.preventDefault(); const err = $('#err'); err.textContent = '';
  const fd = Object.fromEntries(new FormData(af)); const btn = af.querySelector('button[type=submit]'); btn.disabled = true;
  try {
    await api('POST', af.dataset.mode === 'register' ? '/api/register' : '/api/login', fd);
    const n = af.dataset.next || '/shelf'; location.href = n.startsWith('/') && !n.startsWith('//') ? n : '/shelf';
  } catch (x) { err.textContent = x.message; btn.disabled = false; }
};

// ---------------- uploader ----------------
async function startBuild(payload) {
  const j = await api('POST', '/api/books', payload);
  location.href = '/jobs/' + j.job;
}
const go = $('#go');
if (go) {
  let text = null, fname = '', plan = null;
  const modeVal = () => ($('input[name=mode]:checked') || {}).value;
  const firstOk = $$('input[name=mode]').find(i => !i.disabled); if (firstOk) firstOk.checked = true;
  const upd = () => {
    const m = modeVal(); $('#planf').classList.toggle('hide', m !== 'plan');
    go.disabled = !(text && text.trim()) || !m || (m === 'plan' && !plan);
    const est = n => n < 15000 ? Math.max(20, Math.round(n / 120)) + ' 秒左右' : `${Math.max(3, Math.round(n / 12000))}–${Math.max(6, Math.round(n / 6000))} 分钟（整本书按章并发，进度页可看到第几章）`;
    $('#est').textContent = text ? `${text.length.toLocaleString()} 字 · 预计 ${m === 'plan' ? '2–5 秒' : est(text.length)}` : '';
  };
  $$('input[name=mode]').forEach(i => i.onchange = upd);
  const load = file => {
    fname = file.name; const r = new FileReader();
    r.onload = () => {
      const buf = new Uint8Array(r.result); let t = new TextDecoder('utf-8').decode(buf), enc = 'UTF-8';
      if ((t.match(/\uFFFD/g) || []).length > 5) { try { t = new TextDecoder('gb18030').decode(buf); enc = 'GB18030'; } catch (e) {} }
      text = t; $('#fname').textContent = file.name; $('#fmeta').textContent = `${t.length.toLocaleString()} 字 · ${enc} · ${(file.size / 1024).toFixed(1)} KB`;
      $('#drop').classList.add('has'); if (!$('#title').value) $('#title').placeholder = file.name.replace(/\.[^.]+$/, ''); upd();
    };
    r.readAsArrayBuffer(file);
  };
  $('#f').onchange = e => e.target.files[0] && load(e.target.files[0]);
  const d = $('#drop');
  d.ondragover = e => { e.preventDefault(); d.classList.add('over'); }; d.ondragleave = () => d.classList.remove('over');
  d.ondrop = e => { e.preventDefault(); d.classList.remove('over'); e.dataTransfer.files[0] && load(e.dataTransfer.files[0]); };
  $('#pf').onchange = e => { const f = e.target.files[0]; if (!f) return; const r = new FileReader();
    r.onload = () => { try { plan = JSON.parse(r.result); $('#pname').textContent = f.name; $('#pdrop').classList.add('has'); $('#err').textContent = ''; }
      catch (x) { plan = null; $('#pname').textContent = '不是合法 JSON'; } upd(); }; r.readAsText(f); };
  go.onclick = async () => {
    go.disabled = true; $('#err').textContent = '';
    const m = modeVal();
    try {
      await startBuild({name: fname, text, title: $('#title').value || $('#title').placeholder.replace(/^留空则用文件名$/, '') || fname,
        author: $('#author').value, model: m === 'plan' ? null : m, plan: m === 'plan' ? plan : null,
        verify: $('#verify').checked, theme: $('#night').checked ? 'night' : 'paper'});
    } catch (x) { $('#err').textContent = x.status === 401 ? '登录已过期，请重新登录' : x.message; go.disabled = false; }
  };
  upd();
}
// one-click sample run (plan.json mode)
$$('[data-try-sample]').forEach(b => b.onclick = async () => {
  b.disabled = true;
  try { const s = await api('GET', '/api/sample/kongyiji');
    await startBuild({name: 'kongyiji.txt', text: s.text, title: '孔乙己', author: '鲁迅', plan: s.plan, verify: true, theme: 'paper'});
  } catch (x) { toast(x.message); b.disabled = false; }
});

// ---------------- shelf ----------------
const books = $('#books');
if (books) {
  const closeMenus = () => $$('.menu', books).forEach(m => m.remove());
  document.addEventListener('click', e => { if (!e.target.closest('.menu') && !e.target.closest('[data-act]')) closeMenus(); });
  const menu = (card, items) => { closeMenus(); const m = document.createElement('div'); m.className = 'menu';
    items.forEach(([label, sub, fn, dis]) => { const b = document.createElement('button'); b.type = 'button'; b.disabled = !!dis;
      b.innerHTML = esc(label) + (sub ? `<small>${esc(sub)}</small>` : ''); b.onclick = async ev => { ev.stopPropagation(); closeMenus(); await fn(); }; m.appendChild(b); });
    card.appendChild(m); };
  const shareUrl = id => location.origin + '/b/' + id;
  const copy = async s => { try { await navigator.clipboard.writeText(s); return true; } catch (e) { const ta = document.createElement('textarea'); ta.value = s; document.body.appendChild(ta); ta.select(); let ok = false; try { ok = document.execCommand('copy'); } catch (x) {} ta.remove(); return ok; } };
  books.addEventListener('click', async e => {
    const btn = e.target.closest('[data-act]'); if (!btn || btn.getAttribute('aria-disabled') === 'true') return;
    const card = btn.closest('.book'), id = card.dataset.book, act = btn.dataset.act;
    if (act === 'delete') {
      if (!confirm('删除这本书？阅读页、分镜和原文都会从服务器删除，无法恢复。')) return;
      try { await api('DELETE', '/api/books/' + id); card.style.opacity = .3; setTimeout(() => location.reload(), 250); } catch (x) { toast(x.message); }
    } else if (act === 'rebuild') {
      const cfg = await api('GET', '/api/config'); const anyLlm = Object.values(cfg.llm).some(Boolean);
      menu(card, [
        ['沿用分镜，重新渲染', '不调用模型 · 用最新渲染器重排版', async () => { try { const j = await api('POST', `/api/books/${id}/rebuild`, {mode: 'rerender'}); location.href = '/jobs/' + j.job; } catch (x) { toast(x.message); } }, card.dataset.hasplan !== '1'],
        ['重新规划分镜', anyLlm ? '调用模型重新写分镜' : '服务器未配置模型 key', async () => { try { const j = await api('POST', `/api/books/${id}/rebuild`, {mode: 'replan'}); location.href = '/jobs/' + j.job; } catch (x) { toast(x.message); } }, !anyLlm],
      ]);
    } else if (act === 'share') {
      const vis = card.dataset.vis;
      menu(card, vis === 'unlisted' ? [
        ['复制分享链接', '拿到链接的人都能读（不能改、不能删）', async () => toast(await copy(shareUrl(id)) ? '已复制：' + shareUrl(id) : shareUrl(id), 4000)],
        ['改回仅自己可见', '旧链接立即失效（返回 403）', async () => { try { await api('POST', `/api/books/${id}/visibility`, {visibility: 'private'}); location.reload(); } catch (x) { toast(x.message); } }],
      ] : [
        ['开启链接分享并复制', '可见性改为“链接可见”，不会出现在任何公开列表里', async () => { try { await api('POST', `/api/books/${id}/visibility`, {visibility: 'unlisted'}); const ok = await copy(shareUrl(id)); toast(ok ? '已开启并复制链接' : shareUrl(id), 3000); setTimeout(() => location.reload(), 900); } catch (x) { toast(x.message); } }],
      ]);
    }
  });
  if ($$('.stamp.running,.stamp.queued', books).length) setTimeout(() => location.reload(), 4000);
}
const da = $('#delacct');
if (da) da.onsubmit = async e => { e.preventDefault(); if (!confirm('永久删除账号和书架上的全部书？')) return;
  try { await api('DELETE', '/api/me', {password: $('#delpw').value}); location.href = '/'; } catch (x) { $('#delerr').textContent = x.message; } };

// ---------------- darkroom progress ----------------
const dev = $('#dev');
if (dev) {
  const jid = dev.dataset.job, bid = dev.dataset.book; const t0 = parseFloat(dev.dataset.created) * 1000;
  const STEP_OF = {queued: null, start: null, segment: 'segment', outline: 'plan', detail: 'plan', 'detail+': 'plan', plan: 'plan', cast: 'plan', chapters: 'plan', 'chapter+': 'plan', render: 'render', verify: 'verify', shots: 'verify', done: 'done', failed: null};
  const ORDER = ['segment', 'plan', 'render', 'verify'];
  let endT = null, last = -1;
  const tc = $('#tc'); const tcSmall = tc.querySelector('small').outerHTML;
  const drawTc = () => { const ms = Math.max(0, (endT || Date.now()) - t0); const s = Math.floor(ms / 1000);
    tc.innerHTML = `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}:${pad(Math.floor((ms % 1000) / 1000 * 24))}` + tcSmall; };
  const tcI = setInterval(drawTc, 1000 / 24);
  const render = j => {
    $('#meter').style.width = j.pct + '%'; $('#pct').textContent = Math.round(j.pct) + '%';
    const lines = j.log.map(l => { const [t, s, ...m] = l.split('|'); return {t, s, m: m.join('|')}; });
    if (lines.length !== last) {
      last = lines.length;
      $('#console').innerHTML = lines.map(l => l.s === undefined || l.m === '' && !l.s ? `<div class="ln"><span class="t"></span><span class="s"></span><span class="m">${esc(l.t)}</span></div>`
        : `<div class="ln ${l.s === 'error' ? 'error' : ''}"><span class="t">${esc(l.t)}s</span><span class="s">${esc(l.s)}</span><span class="m">${esc(l.m)}</span></div>`).join('') + (j.status === 'running' || j.status === 'queued' ? '<span class="cursor"></span>' : '');
      $('#console').scrollTop = 1e9;
    }
    // step states
    const cur = STEP_OF[j.stage] ?? null; const ci = cur === 'done' ? 4 : ORDER.indexOf(cur);
    const notes = {}; lines.forEach(l => { const st = STEP_OF[l.s]; if (st && st !== 'done') notes[st] = l.m; });
    ORDER.forEach((k, i) => { const f = $(`[data-step="${k}"]`); f.classList.remove('active', 'done', 'failed');
      let st = '等待';
      if (j.status === 'done') { const skipped = k === 'verify' && !lines.some(l => l.s === 'verify'); f.classList.add('done'); st = skipped ? '跳过' : '完成'; }
      else if (j.status === 'failed') { if (i < ci) { f.classList.add('done'); st = '完成'; } else if (i === Math.max(ci, 0)) { f.classList.add('failed'); st = '失败'; } }
      else if (i < ci) { f.classList.add('done'); st = '完成'; } else if (i === ci) { f.classList.add('active'); st = '进行中'; }
      f.querySelector('.state').textContent = st; f.querySelector('[data-note]').textContent = notes[k] || ''; });
    const res = $('#result');
    if (j.status === 'done') {
      endT = j.finished_at * 1000; drawTc(); clearInterval(tcI);
      const v = j.verify; const vtxt = v ? (v.text_identical ? `${v.matched}/${v.segments} 段一致` : '未通过') : '未校验';
      res.innerHTML = `<h3>定影完成<small>Fixed — the print is ready</small></h3><dl>
        <div><dt>耗时</dt><dd>${(j.seconds ?? 0).toFixed ? (j.seconds ?? 0).toFixed(1) : j.seconds} s</dd></div>
        <div><dt>原文校验</dt><dd class="ok">${esc(vtxt)}</dd></div>
        <div><dt>外部请求 / JS 报错</dt><dd>${v ? (v.external_requests || []).length + ' / ' + (v.js_errors || []).length : '—'}</dd></div>
        <div><dt>分镜来源</dt><dd>${esc(j.model || '—')}</dd></div></dl>
        <div class="acts"><a class="btn" href="/b/${esc(bid)}" target="_blank" rel="noopener">打开阅读页 <span class="arr">→</span></a><a class="btn ghost" href="/shelf">回书架</a></div>`;
      return true;
    }
    if (j.status === 'failed') {
      endT = (j.finished_at || Date.now() / 1000) * 1000; drawTc(); clearInterval(tcI);
      res.innerHTML = `<h3>显影失败<small>The print didn't come out</small></h3><p class="wait" style="color:var(--safelight)">${esc(j.error || '未知错误')}</p>
        <div class="acts" style="margin-top:20px"><a class="btn" href="/shelf">回书架重新生成</a><a class="btn ghost" href="/#upload">换个文件</a></div>`;
      return true;
    }
    return false;
  };
  const poll = async () => { try { const j = await api('GET', '/api/jobs/' + jid); if (render(j)) return; } catch (x) { if (x.status === 403 || x.status === 404) { $('#result').innerHTML = `<h3>看不到这个任务</h3><p class="wait">${esc(x.message)}</p>`; return; } }
    setTimeout(poll, 900); };
  poll();
}
})();
