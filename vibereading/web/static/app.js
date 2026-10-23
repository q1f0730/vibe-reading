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
    go.disabled = true; go.classList.add('sending'); $('#err').textContent = '';
    const m = modeVal();
    try {
      await startBuild({name: fname, text, title: $('#title').value || $('#title').placeholder.replace(/^留空则用文件名$/, '') || fname,
        author: $('#author').value, model: m === 'plan' ? null : m, plan: m === 'plan' ? plan : null,
        verify: $('#verify').checked, theme: $('#night').checked ? 'night' : 'paper',
        hero: m !== 'plan' && $('#hero').checked, hero_max: +$('#heroMax').value});
    } catch (x) { $('#err').textContent = x.status === 401 ? '登录已过期，请重新登录' : x.message; go.disabled = false; go.classList.remove('sending'); }
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
if (da) da.onsubmit = async e => { e.preventDefault(); const slid = da._slid; da._slid = false; const sl = $('[data-slide]', da);
  // the slide-to-confirm gesture is the confirmation; a plain Enter in the password field still asks
  if (!slid && !confirm('永久删除账号和书架上的全部书？')) return;
  if (!$('#delpw').value) { $('#delerr').textContent = '先输入密码'; if (sl && sl._reset) sl._reset(); return; }
  try { await api('DELETE', '/api/me', {password: $('#delpw').value}); location.href = '/'; } catch (x) { $('#delerr').textContent = x.message; if (sl && sl._reset) sl._reset(); } };

// ---------------- model settings ----------------
const mf = $('#mform');
if (mf) {
  const form = () => { const d = Object.fromEntries(new FormData(mf)); d.stream = $('#m_stream').checked; if (!d.api_key) delete d.api_key; return d; };
  const merr = $('#merr'), mres = $('#mres');
  $('#mtest').onclick = async () => {
    merr.textContent = ''; mres.textContent = '测试中…'; $('#mtest').disabled = true;
    try { const r = await api('POST', '/api/model/test', form());
      mres.textContent = r.ok ? `连通 ✓ · ${r.latency}s · 回复「${r.reply || ''}」${r.reasoning ? ' · 推理模型' : ''}` : '';
      if (!r.ok) merr.textContent = '测试失败：' + (r.error || '没有回复'); }
    catch (x) { mres.textContent = ''; merr.textContent = x.message; }
    $('#mtest').disabled = false;
  };
  mf.onsubmit = async e => {
    e.preventDefault(); merr.textContent = ''; $('#msave').disabled = true; mres.textContent = '保存中…';
    try { await api('POST', '/api/model', form()); location.reload(); }
    catch (x) { merr.textContent = x.message; mres.textContent = ''; $('#msave').disabled = false; }
  };
  const del = $('#mdel');
  if (del) del.onclick = async () => { if (!confirm('删除你的模型配置（含加密保存的 key），改回平台默认？')) return;
    try { await api('DELETE', '/api/model', {}); location.reload(); } catch (x) { merr.textContent = x.message; } };
  const ms = $('#mstatus');
  if (ms && ms.dataset.status === 'probing') {
    const poll = async () => { try { const j = await api('GET', '/api/model'); if (j.model && j.model.status !== 'probing') return location.reload(); } catch (x) {} setTimeout(poll, 2500); };
    setTimeout(poll, 2500);
  }
}

// ---------------- darkroom progress ----------------
const dev = $('#dev');
if (dev) {
  const jid = dev.dataset.job, bid = dev.dataset.book; const t0 = parseFloat(dev.dataset.created) * 1000;
  const STEP_OF = {queued: null, start: null, segment: 'segment', outline: 'plan', detail: 'plan', 'detail+': 'plan', plan: 'plan', cast: 'plan', chapters: 'plan', 'chapter+': 'plan', hero: 'plan', 'hero-sel': 'plan', 'hero+': 'plan', render: 'render', verify: 'verify', shots: 'verify', done: 'done', failed: null};
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

// =====================================================================
// ui-rewamp · interaction layer. Design inspired by Rewamp UI (rewampui.com); this code is written for VibeReading
// (vanilla JS, no dependencies) and copies no Rewamp UI source. Every effect is skipped under prefers-reduced-motion.
// =====================================================================
(() => {
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const root = document.documentElement;

// ---- paper / ink theme switch (theme-toggle) ----
const sw = $$('[data-theme-toggle]');
const paint = () => { const dark = root.getAttribute('data-theme') === 'dark';
  sw.forEach(b => { b.setAttribute('aria-checked', dark ? 'true' : 'false'); b.setAttribute('aria-label', dark ? '切换到纸（浅色）主题' : '切换到墨（深色）主题'); });
  const m = $('meta[name="color-scheme"]'); if (m && !m.hasAttribute('data-fixed')) m.setAttribute('content', dark ? 'dark' : 'light'); };
paint();
sw.forEach(b => b.addEventListener('click', () => {
  const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next); try { localStorage.setItem('vr:theme', next); } catch (e) {} paint();
}));

// ---- split-text reveal (characters) + word-by-word reveal ----
function wrapText(node, unit, state) {
  [...node.childNodes].forEach(n => {
    if (n.nodeType === 3) {
      const parts = unit === 'w' ? n.textContent.split(/(\s+)/) : [...n.textContent];
      const frag = document.createDocumentFragment();
      parts.forEach(p => {
        if (!p) return;
        if (/^\s+$/.test(p)) { frag.appendChild(document.createTextNode(p)); return; }
        const s = document.createElement('span'); s.className = unit; s.textContent = p; s.setAttribute('aria-hidden', 'true');
        const i = state.i++; s.style.setProperty('--i', i);
        if (unit === 'ch') { const sg = i % 2 ? 1 : -1; s.style.setProperty('--y', (sg * (22 + (i * 37 % 30))) + 'px'); s.style.setProperty('--r', (sg * (4 + (i * 53 % 9))) + 'deg'); }
        frag.appendChild(s);
      });
      n.replaceWith(frag);
    } else if (n.nodeType === 1 && n.tagName !== 'BR' && !(unit === 'ch' && n.hasAttribute('data-words'))) wrapText(n, unit, state);
  });
}
if (!reduce) $$('[data-split]').forEach(el => {
  el.setAttribute('aria-label', el.textContent.replace(/\s+/g, ' ').trim());
  wrapText(el, 'ch', {i: 0}); $$('[data-words]', el).forEach(w => wrapText(w, 'w', {i: 0}));
  el.classList.add('js-split');
});

// ---- film-edge marquee speeds up with scroll velocity (velocity-marquee-text) ----
const edge = $('.edge-track');
if (edge && !reduce && edge.getAnimations) {
  let lastY = scrollY, boost = 0, raf = 0;
  const anim = () => edge.getAnimations()[0];
  const loop = () => { boost *= 0.92; const a = anim(); if (a) a.playbackRate = 1 + boost; raf = boost > 0.02 ? requestAnimationFrame(loop) : 0; if (!raf && a) a.playbackRate = 1; };
  addEventListener('scroll', () => { const v = Math.abs(scrollY - lastY); lastY = scrollY; boost = Math.min(14, boost + v * 0.08); if (!raf) raf = requestAnimationFrame(loop); }, {passive: true});
}

// ---- sliding ink indicator for tab rows (morphing-tab-navbar) ----
$$('[data-morph]').forEach(row => {
  const ink = $('.tab-ink', row); if (!ink) return;
  const items = $$(':scope > a, :scope > button', row);
  const current = () => items.find(t => t.getAttribute('aria-current') === 'page' || t.getAttribute('aria-selected') === 'true' || t.getAttribute('aria-checked') === 'true');
  const move = (t, instant) => { if (!t) return; if (instant) ink.style.transition = 'none';
    ink.style.setProperty('--tx', t.offsetLeft + 'px'); ink.style.setProperty('--tw', t.offsetWidth + 'px');
    if (instant) { ink.offsetWidth; ink.style.transition = ''; } };
  // auth tabs: start from the tab the user came from, then glide to the current one
  let from = null; try { from = sessionStorage.getItem('vr:tabfrom'); sessionStorage.removeItem('vr:tabfrom'); } catch (e) {}
  const start = from && items.find(t => t.dataset.tab === from);
  move(start || current(), true); row.classList.add('ready');
  if (start) requestAnimationFrame(() => requestAnimationFrame(() => move(current())));
  items.forEach(t => {
    t.addEventListener('mouseenter', () => move(t)); t.addEventListener('focus', () => move(t));
    if (t.dataset.tab) t.addEventListener('click', () => { const c = current(); try { if (c) sessionStorage.setItem('vr:tabfrom', c.dataset.tab); } catch (e) {} });
  });
  row.addEventListener('mouseleave', () => move(current()));
  row.addEventListener('focusout', e => { if (!row.contains(e.relatedTarget)) move(current()); });
  row._move = move; row._current = current;
  addEventListener('resize', () => move(current(), true));
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => move(current(), true));
});

// ---- settings: interface-type segmented control drives the (visually hidden) <select> ----
$$('.kseg').forEach(row => {
  const sel = document.getElementById(row.dataset.for); if (!sel) return;
  const btns = $$('button[data-val]', row);
  const set = v => { btns.forEach(b => b.setAttribute('aria-checked', b.dataset.val === v ? 'true' : 'false')); if (row._move) row._move(row._current()); };
  btns.forEach(b => b.addEventListener('click', () => { sel.value = b.dataset.val; sel.dispatchEvent(new Event('change', {bubbles: true})); set(b.dataset.val); }));
  row.addEventListener('keydown', e => { const i = btns.findIndex(b => b.getAttribute('aria-checked') === 'true');
    const d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0; if (!d) return;
    e.preventDefault(); const n = btns[(i + d + btns.length) % btns.length]; n.focus(); n.click(); });
  sel.addEventListener('change', () => set(sel.value));
  btns.forEach(b => b.tabIndex = b.getAttribute('aria-checked') === 'true' ? 0 : -1);
});

// ---- shelf: status filter + search capsule ----
const books = $('#books');
if (books) {
  const q = $('#shelfq'), seg = $('.shelf-tools .seg'); let f = 'all';
  const apply = () => { const s = (q && q.value || '').trim().toLowerCase(); let shown = 0;
    $$('.book', books).forEach(c => { const st = c.dataset.status;
      const okF = f === 'all' || (f === 'busy' ? (st === 'running' || st === 'queued') : st === f);
      const txt = (($('h3', c) || {}).textContent + ' ' + (($('.by', c) || {}).textContent || '')).toLowerCase();
      const ok = okF && (!s || txt.includes(s)); c.hidden = !ok; if (ok) shown++; });
    const nr = $('#nores'); if (nr) nr.classList.toggle('hide', shown > 0); };
  if (seg) $$('button[data-filter]', seg).forEach(b => b.addEventListener('click', () => {
    f = b.dataset.filter; $$('button[data-filter]', seg).forEach(x => x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
    if (seg._move) seg._move(b); apply(); }));
  if (q) { q.addEventListener('input', apply);
    const cap = q.closest('.capsule');
    cap.addEventListener('pointerdown', e => { if (reduce) return; const r = cap.getBoundingClientRect(); const d = document.createElement('span'); d.className = 'ripple';
      d.style.left = (e.clientX - r.left) + 'px'; d.style.top = (e.clientY - r.top) + 'px'; cap.appendChild(d); setTimeout(() => d.remove(), 650); }); }
  // folder card: pointer-following paper sheen + stamp parallax
  if (!reduce && matchMedia('(hover: hover)').matches) books.addEventListener('pointermove', e => {
    const c = e.target.closest('.book'); if (!c) return; const r = c.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    c.style.setProperty('--mx', (x * 100) + '%'); c.style.setProperty('--my', (y * 100) + '%');
    c.style.setProperty('--mxn', ((x - .5) * 2).toFixed(2)); c.style.setProperty('--myn', ((y - .5) * 2).toFixed(2)); });
}

// ---- slide to confirm (irreversible actions) ----
$$('[data-slide]').forEach(sl => {
  const knob = $('.slide-knob', sl), form = sl.closest('form'); let x0 = 0, dragging = false, done = false;
  const max = () => sl.clientWidth - knob.offsetWidth;
  const setX = x => sl.style.setProperty('--x', Math.max(0, Math.min(max(), x)) + 'px');
  const finish = () => { if (done) return; done = true; sl.classList.add('spring', 'done'); setX(max());
    setTimeout(() => { form._slid = true; if (form.requestSubmit) form.requestSubmit(); else form.dispatchEvent(new Event('submit', {cancelable: true})); }, reduce ? 0 : 380); };
  const reset = () => { done = false; sl.classList.add('spring'); sl.classList.remove('done'); setX(0); };
  sl._reset = reset;
  knob.addEventListener('pointerdown', e => { if (done) return; dragging = true; sl.classList.remove('spring'); x0 = e.clientX - (parseFloat(sl.style.getPropertyValue('--x')) || 0); knob.setPointerCapture(e.pointerId); });
  knob.addEventListener('pointermove', e => { if (dragging) setX(e.clientX - x0); });
  const up = () => { if (!dragging) return; dragging = false; (parseFloat(sl.style.getPropertyValue('--x')) || 0) >= max() * 0.9 ? finish() : reset(); };
  knob.addEventListener('pointerup', up); knob.addEventListener('pointercancel', up);
  knob.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); finish(); } });
});

// ---- darkroom: scramble-decoded step states + typewriter for the newest log line ----
const dev = $('#dev');
if (dev) {
  const GL = '░▒▓█01ABCDEFXYZ#%&@';
  const scramble = (el, text, done) => { if (reduce) { el.textContent = text; done && done(); return; } cancelAnimationFrame(el._raf); let k = 0; const N = text.length * 3 + 6;
    const step = () => { el.textContent = [...text].map((c, i) => i < (k - 6) / 3 ? c : (c === ' ' ? ' ' : GL[(Math.random() * GL.length) | 0])).join('');
      if (k++ < N) el._raf = requestAnimationFrame(step); else { el.textContent = text; done && done(); } };
    step(); };
  const watch = (el, cb) => new MutationObserver(cb).observe(el, {childList: true, characterData: true, subtree: true});
  // step labels: app.js core writes .state text; re-run it through the decoder whenever it changes
  $$('.steps .state', dev).forEach(el => { let shown = el.textContent; watch(el, () => { const t = el.textContent;
    if (el._busy || t === shown) return; shown = t; el._busy = true; scramble(el, t, () => setTimeout(() => { el._busy = false; }, 0)); }); });
  const res = $('#result');
  if (res) watch(res, () => { const h = $('h3', res); if (!h || h._done) return; h._done = true; const t = h.firstChild;
    if (t && t.nodeType === 3) { const s = document.createElement('span'); s.className = 'scr-h'; t.replaceWith(s); scramble(s, t.textContent); } });
  const con = $('#console'); let seen = -1;
  if (con && !reduce) watch(con, () => { const lines = $$('.ln', con); if (lines.length === seen) return;
    const first = seen < 0; seen = lines.length; if (first || !lines.length) return;
    const m = $('.m', lines[lines.length - 1]); if (!m || m._typed) return; m._typed = true; const full = m.textContent; const ln = m.parentElement;
    ln.classList.add('typing'); m.textContent = ''; let i = 0; const per = Math.max(6, Math.min(28, 700 / Math.max(1, full.length)));
    const t = setInterval(() => { i += 1; if (!m.isConnected) return clearInterval(t); m.textContent = full.slice(0, i); if (i >= full.length) { clearInterval(t); ln.classList.remove('typing'); } }, per); });
}
})();
