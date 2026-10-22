/* VibeReading hero primitives (VRP) · zero-dependency, scroll-driven, no rAF loops.
   Every primitive is a pure function of local progress t∈[0,1]: VRP.mount(comp) builds layers, VRH.onProgress drives them. */
(function(){
'use strict';
var M = Math, PI = M.PI;
function clamp(x, a, b){ a = a == null ? 0 : a; b = b == null ? 1 : b; return x < a ? a : x > b ? b : x; }
function lerp(a, b, t){ return a + (b - a) * t; }
function seg(t, a, b){ return clamp((t - a) / (b - a)); }
function eo(t){ t = clamp(t); return 1 - M.pow(1 - t, 4); }            // ease-out quart
function eio(t){ t = clamp(t); return t < .5 ? 4 * t * t * t : 1 - M.pow(-2 * t + 2, 3) / 2; }
function es(t){ t = clamp(t); return t * t * (3 - 2 * t); }               // smoothstep
function rng(seed){ var s = seed >>> 0 || 1; return function(){ s = (s + 0x6D2B79F5) >>> 0; var x = s; x = M.imul(x ^ (x >>> 15), x | 1); x ^= x + M.imul(x ^ (x >>> 7), x | 61); return ((x ^ (x >>> 14)) >>> 0) / 4294967296; }; }
function hash(str){ var h = 2166136261; str = String(str); for (var i = 0; i < str.length; i++){ h ^= str.charCodeAt(i); h = M.imul(h, 16777619); } return h >>> 0; }
function el(tag, cls, parent, css){ var e = document.createElement(tag); if (cls) e.className = cls; if (css) e.style.cssText = css; if (parent) parent.appendChild(e); return e; }
var SVGNS = 'http://www.w3.org/2000/svg';
function sv(tag, attrs, parent){ var e = document.createElementNS(SVGNS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
function css(name){ return getComputedStyle(document.documentElement).getPropertyValue('--' + name).trim(); }
function rgb(hex){ hex = (hex || '#000').replace('#', ''); if (hex.length === 3) hex = hex.replace(/./g, '$&$&'); var n = parseInt(hex, 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
function rgba(c, a){ return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + (+a).toFixed(3) + ')'; }
function night(){ return document.documentElement.getAttribute('data-theme') === 'night'; }
function chars(s){ return Array.from(String(s || '')); }
function keys(arr, t){ // keyframes evenly spaced, cosine-smoothed
  if (!arr || !arr.length) return 0; if (arr.length === 1) return arr[0];
  var x = clamp(t) * (arr.length - 1), i = M.min(arr.length - 2, M.floor(x)), f = x - i; f = (1 - M.cos(f * PI)) / 2;
  return lerp(arr[i], arr[i + 1], f);
}
// value noise / fbm
function vnoise(seed){ var R = rng(seed), P = new Float32Array(256 * 256); for (var i = 0; i < P.length; i++) P[i] = R();
  function v(x, y){ var xi = M.floor(x), yi = M.floor(y), xf = x - xi, yf = y - yi; xi &= 255; yi &= 255; var x1 = (xi + 1) & 255, y1 = (yi + 1) & 255;
    var u = xf * xf * (3 - 2 * xf), w = yf * yf * (3 - 2 * yf);
    return lerp(lerp(P[yi * 256 + xi], P[yi * 256 + x1], u), lerp(P[y1 * 256 + xi], P[y1 * 256 + x1], u), w); }
  return function(x, y, oct){ var a = 0, amp = .5, f = 1, n = 0; for (var o = 0; o < (oct || 4); o++){ a += amp * v(x * f, y * f); n += amp; amp *= .5; f *= 2.03; } return a / n; };
}
// textures (generated once, data: URLs are allowed by the sandbox CSP)
var TEX = {};
function grainURL(){ if (TEX.g) return TEX.g; var c = document.createElement('canvas'); c.width = c.height = 192; var x = c.getContext('2d'), d = x.createImageData(192, 192), R = rng(7);
  for (var i = 0; i < d.data.length; i += 4){ var v = R(), g = v < .5 ? 0 : 255, a = M.pow(M.abs(v - .5) * 2, 2.2) * 255; d.data[i] = d.data[i + 1] = d.data[i + 2] = g; d.data[i + 3] = a; }
  x.putImageData(d, 0, 0); return TEX.g = c.toDataURL(); }
function fiberURL(){ if (TEX.f) return TEX.f; var c = document.createElement('canvas'); c.width = c.height = 320; var x = c.getContext('2d'), R = rng(11);
  x.lineCap = 'round';
  for (var i = 0; i < 520; i++){ var px = R() * 320, py = R() * 320, a = R() * PI * 2, L = 6 + R() * 26; x.strokeStyle = 'rgba(90,70,45,' + (0.05 + R() * .11).toFixed(3) + ')'; x.lineWidth = .4 + R() * .7;
    x.beginPath(); x.moveTo(px, py); x.quadraticCurveTo(px + M.cos(a + .6) * L * .5, py + M.sin(a + .6) * L * .5, px + M.cos(a) * L, py + M.sin(a) * L); x.stroke(); }
  for (var j = 0; j < 1600; j++){ x.fillStyle = 'rgba(70,55,35,' + (R() * .08).toFixed(3) + ')'; x.fillRect(R() * 320, R() * 320, 1, 1); }
  return TEX.f = c.toDataURL(); }
function speckURL(){ if (TEX.s) return TEX.s; var c = document.createElement('canvas'); c.width = c.height = 160; var x = c.getContext('2d'), d = x.createImageData(160, 160), n = vnoise(5);
  for (var yy = 0; yy < 160; yy++) for (var xx = 0; xx < 160; xx++){ var i = (yy * 160 + xx) * 4, v = n(xx / 9, yy / 9, 3) * .55 + n(xx / 1.7, yy / 1.7, 1) * .45;
    d.data[i] = d.data[i + 1] = d.data[i + 2] = 0; d.data[i + 3] = clamp((v - .28) * 3.2) * 255; }
  x.putImageData(d, 0, 0); return TEX.s = c.toDataURL(); }

var SERIF = "var(--serif)";
function fit(word, maxW, maxH){ var n = M.max(1, chars(word).length); return M.max(14, M.min(maxW / (n * 1.04), maxH)); }
function tone(k){ return k === 'safe' ? 'var(--safe)' : k === 'cool' ? 'var(--cool)' : 'var(--ink)'; }

/* ============================================================== primitives
   P[name] = function(layer, params, ctx) → {update(t), theme?()}; ctx = {W, H, mob, data, chars, seed} */
var P = {};

/* 1 · typeset — 动态排版：拆字聚合 / 字距拉开 / 遮罩揭示 / 竖排 */
P.typeset = function(L, o, C){
  var W = C.W, H = C.H, words = (o.words || []).filter(Boolean).slice(0, 3); if (!words.length) words = ['　'];
  var mode = o.mode || 'assemble', col = tone(o.tone), acc = o.accent == null ? -1 : +o.accent, R = rng(C.seed + 3);
  var main = words[0], rest = words.slice(1), A = [], U = {};
  if (mode === 'mask'){
    var maxLen = M.max.apply(null, words.map(function(w){ return chars(w).length; }));
    var sz = M.min(fit('x'.repeat(maxLen), W * .74, 9e9), H * .62 / (words.length * 1.12)), top = H * .5 - words.length * sz * 1.12 / 2;
    words.forEach(function(w, k){ var mk = el('div', 'mk', L, 'left:' + (W * (C.mob ? .07 : .12) + k * W * (C.mob ? .05 : .075)) + 'px;top:' + (top + k * sz * 1.12) + 'px;font-size:' + sz + 'px;font-weight:' + (k === words.length - 1 ? 900 : 600) + ';color:' + (k === words.length - 1 ? col : 'var(--ink)') + ';letter-spacing:.02em');
      var s = el('span', '', mk); s.textContent = w; A.push({mk: mk, s: s}); });
    var lastTop = top + (words.length - 1) * sz * 1.12 + sz * 1.18;
    U.rule = el('div', 'rule', L, 'left:' + (W * (C.mob ? .07 : .12) + (words.length - 1) * W * (C.mob ? .05 : .075)) + 'px;top:' + lastTop + 'px;width:' + M.min(W * .5, sz * 2.6) + 'px;height:2px;color:var(--safe)');
    return {update: function(t){
      A.forEach(function(a, k){ var tk = eo(seg(t, k * .2 - .16, k * .2 + .4)); a.s.style.transform = 'translateY(' + ((1 - tk) * 108).toFixed(2) + '%)';
        a.mk.style.opacity = k < A.length - 1 ? lerp(1, .26, es(seg(t, .74, 1))) : 1; });
      U.rule.style.transform = 'scaleX(' + es(seg(t, .62, .9)).toFixed(3) + ')'; }};
  }
  if (mode === 'vertical'){
    var cs = chars(main), vs = M.min(H * .78 / cs.length, W * (C.mob ? .3 : .2), H * .3), vx = W * (C.mob ? .62 : .64);
    var box = el('div', 'w', L, 'left:' + vx + 'px;top:' + (H * .5 - cs.length * vs * 1.04 / 2) + 'px;writing-mode:vertical-rl;font-size:' + vs + 'px;font-weight:900;color:' + col + ';letter-spacing:.04em;line-height:1');
    cs.forEach(function(c, k){ var s = el('span', 'ch', box); s.textContent = c; if (k === acc) s.style.color = 'var(--safe)'; A.push(s); });
    U.line = el('div', '', L, 'position:absolute;left:' + (vx - vs * .5) + 'px;top:' + H * .14 + 'px;width:1px;height:' + H * .72 + 'px;background:var(--ink2);transform-origin:50% 0;opacity:.55');
    if (rest.length){ U.note = el('div', 'w', L, 'left:' + W * (C.mob ? .1 : .2) + 'px;top:' + H * .62 + 'px;font-size:' + M.max(15, M.min(W * .045, 26)) + 'px;color:var(--ink2);letter-spacing:.3em');
      U.note.textContent = rest.join('　'); }
    U.no = el('div', 'note', L, 'position:absolute;left:' + W * (C.mob ? .1 : .2) + 'px;top:' + (H * .62 - 26) + 'px;font-family:var(--mono);letter-spacing:.2em'); U.no.textContent = '— ' + ('0' + (C.idx + 1)).slice(-2);
    return {update: function(t){
      A.forEach(function(s, k){ var tk = eo(seg(t, -.14 + k * .1, .4 + k * .1)); s.style.transform = 'translateY(' + ((1 - tk) * -.35).toFixed(3) + 'em)'; s.style.opacity = tk.toFixed(3); s.style.filter = 'blur(' + ((1 - tk) * 6).toFixed(1) + 'px)'; });
      U.line.style.transform = 'scaleY(' + lerp(.25, 1, eo(seg(t, 0, .55))).toFixed(3) + ')';
      if (U.note){ var q = es(seg(t, .55, .85)); U.note.style.opacity = q; U.note.style.transform = 'translateX(' + ((1 - q) * -14) + 'px)'; U.note.style.letterSpacing = lerp(.6, .3, q).toFixed(3) + 'em'; }
      U.no.style.opacity = es(seg(t, .5, .75)) * .8; }};
  }
  // assemble / scatter share a centred line of glyphs
  var cs2 = chars(main), n = cs2.length, size = M.min(fit(main, W * (mode === 'scatter' ? .5 : .76), H * .34), n === 1 ? H * .46 : 9e9);
  var line = el('div', 'w', L, 'left:50%;top:' + H * .44 + 'px;transform:translate(-50%,-50%);font-size:' + size + 'px;font-weight:900;color:' + col + ';letter-spacing:.02em');
  cs2.forEach(function(c, k){ var s = el('span', 'ch', line); s.textContent = c; if (k === acc) s.style.color = 'var(--safe)';
    A.push({s: s, dx: (R() - .5) * W * .5, dy: (R() - .5) * H * .55, r: (R() - .5) * 34, k: k}); });
  var ruleY = H * .44 + size * .62;
  if (mode === 'scatter'){
    U.dim = el('div', '', L, 'position:absolute;top:' + ruleY + 'px;left:0;height:9px;border-left:1px solid var(--ink2);border-right:1px solid var(--ink2);transform:translateY(-4px)');
    U.dl = el('div', '', U.dim, 'position:absolute;left:0;right:0;top:4px;height:1px;background:var(--ink2)');
    if (rest.length){ U.lb = el('div', 'note', L, 'position:absolute;top:' + (ruleY + 16) + 'px;left:50%;transform:translateX(-50%)'); U.lb.textContent = rest.join(' · '); }
    var maxGap = M.max(0, (W * .86 - n * size) / M.max(1, n - 1)) * (n === 1 ? 0 : 1);
    return {update: function(t){
      var g = es(seg(t, .12, .82)) * M.min(maxGap, W * .5);
      A.forEach(function(a){ var off = (a.k - (n - 1) / 2) * g; a.s.style.transform = 'translateX(' + off.toFixed(1) + 'px) translateY(' + (M.sin(a.k * 1.7) * g * .03).toFixed(1) + 'px)'; });
      var inner = n > 1 ? g * (n - 1) - size * .1 : 0, vis = es(seg(t, .3, .6));
      U.dim.style.width = M.max(0, inner).toFixed(1) + 'px'; U.dim.style.left = (W / 2 - inner / 2).toFixed(1) + 'px'; U.dim.style.opacity = vis;
      if (U.lb) U.lb.style.opacity = es(seg(t, .55, .8)); }};
  }
  var gc = chars(main)[acc >= 0 && acc < n ? acc : 0], gs = H * 1.15;
  U.ghost = el('div', 'w', L, 'right:' + (-gs * .18) + 'px;top:' + (-gs * .1) + 'px;font-size:' + gs + 'px;font-weight:900;color:transparent;-webkit-text-stroke:1px var(--ink3);opacity:.55;z-index:-1');
  U.ghost.textContent = gc; line.style.zIndex = 1;
  U.rule = el('div', 'rule', L, 'left:' + (W / 2 - size * .9) + 'px;top:' + ruleY + 'px;width:' + size * 1.8 + 'px;color:var(--ink2)');
  if (rest.length){ U.lb = el('div', 'note', L, 'position:absolute;top:' + (ruleY + 18) + 'px;left:50%;transform:translateX(-50%)'); U.lb.textContent = rest.join(' · '); }
  return {update: function(t){
    A.forEach(function(a){ var tk = eo(seg(t, .04 + a.k * .07, .5 + a.k * .07)); var q = 1 - tk;
      a.s.style.transform = 'translate(' + (a.dx * q).toFixed(1) + 'px,' + (a.dy * q).toFixed(1) + 'px) rotate(' + (a.r * q).toFixed(2) + 'deg)';
      a.s.style.opacity = lerp(.12, 1, es(seg(tk, .15, .75))).toFixed(3); a.s.style.filter = q > .01 ? 'blur(' + (q * 12).toFixed(1) + 'px)' : 'none'; });
    line.style.letterSpacing = (.02 + .22 * es(seg(t, .6, 1))).toFixed(3) + 'em';
    U.ghost.style.transform = 'translateY(' + lerp(H * .06, -H * .06, t).toFixed(1) + 'px)'; U.ghost.style.opacity = '.55';
    U.rule.style.transform = 'scaleX(' + es(seg(t, .5, .82)).toFixed(3) + ')';
    if (U.lb){ var v = es(seg(t, .68, .92)); U.lb.style.opacity = v; U.lb.style.letterSpacing = lerp(.7, .32, v).toFixed(3) + 'em'; } }};
};

/* 2 · develop — 暗房显影：红色安全灯下，相纸在药水里从模糊到清晰 */
P.develop = function(L, o, C){
  L.classList.add('dk'); var W = C.W, H = C.H, word = o.word || '', note = o.note || '';
  el('div', '', L, 'position:absolute;left:50%;top:-55%;width:' + W * 1.4 + 'px;height:' + W * 1.0 + 'px;transform:translateX(-50%);background:radial-gradient(closest-side,rgba(196,30,26,.42),rgba(150,18,16,.12) 55%,transparent)');
  var tw = C.mob ? W * .9 : M.min(W * .74, H * 1.18), th = tw * (C.mob ? .78 : .6);
  var tray = el('div', '', L, 'position:absolute;left:50%;top:' + (H * .5 - th * .46) + 'px;width:' + tw + 'px;height:' + th + 'px;margin-left:' + (-tw / 2) + 'px;border-radius:7px;' +
    'background:linear-gradient(180deg,#22171499,#0b0807);box-shadow:inset 0 0 0 1px rgba(255,255,255,.05),inset 0 24px 50px rgba(0,0,0,.75),0 40px 80px rgba(0,0,0,.6);overflow:hidden');
  var sheen = el('div', '', tray, 'position:absolute;top:0;bottom:0;left:0;width:260%;background:linear-gradient(105deg,transparent 35%,rgba(255,110,90,.09) 48%,transparent 60%);will-change:transform');
  var pw = tw * (C.mob ? .8 : .66), ph = pw * .7;
  var pr = el('div', '', tray, 'position:absolute;left:50%;top:50%;width:' + pw + 'px;height:' + ph + 'px;margin:' + (-ph / 2) + 'px 0 0 ' + (-pw / 2) + 'px;background:#ece5d9;transform:rotate(-1.6deg);will-change:transform;box-shadow:0 0 0 1px rgba(0,0,0,.25),0 6px 18px rgba(0,0,0,.35)');
  var img = el('div', '', pr, 'position:absolute;left:6%;right:6%;top:7%;bottom:15%;overflow:hidden'), cps = [];
  [['blur(16px) contrast(.4) brightness(1.8)'], ['blur(5px) contrast(.8) brightness(1.25)'], ['contrast(1.12)']].forEach(function(f){
    var c = el('div', '', img, 'position:absolute;inset:0;filter:' + f[0] + ';background:radial-gradient(120% 120% at 70% 30%,#8d857a,#2a2420 70%,#120e0c);will-change:opacity');
    el('div', '', c, 'position:absolute;top:0;bottom:0;left:60%;width:16%;background:linear-gradient(90deg,transparent,rgba(240,232,220,.5),transparent)');
    var w = el('div', 'w', c, 'left:9%;bottom:12%;font-size:' + fit(word, pw * .62, ph * .42) + 'px;font-weight:900;color:#f3ede3;letter-spacing:.06em'); w.textContent = word; cps.push(c); });
  var gr = el('div', '', img, 'position:absolute;inset:0;background-image:url(' + grainURL() + ');background-size:96px;opacity:.55;mix-blend-mode:overlay');
  var edge = el('div', 'w', pr, 'left:6%;bottom:4.5%;font:600 ' + M.max(8, pw * .022) + 'px/1 var(--mono);letter-spacing:.24em;color:rgba(176,96,40,.85)');
  edge.textContent = 'VR 400TX  \u25B8 ' + (14 + C.idx) + '   \u25B8 ' + (14 + C.idx) + 'A';
  el('div', '', tray, 'position:absolute;inset:0;background:rgba(150,20,14,.34);pointer-events:none');
  var rip = [0, 1].map(function(){ return el('div', '', tray, 'position:absolute;left:50%;top:50%;width:' + pw * 1.1 + 'px;height:' + ph * 1.1 + 'px;margin:' + (-ph * .55) + 'px 0 0 ' + (-pw * .55) + 'px;border-radius:50%;border:1px solid rgba(255,140,120,.16);will-change:transform,opacity'); });
  var tm = el('div', 'w', L, 'left:' + (W / 2 - tw / 2) + 'px;top:' + (H * .5 + th * .58) + 'px;font:500 11px/1 var(--mono);letter-spacing:.2em;color:rgba(232,86,70,.8)');
  var nt = note ? el('div', 'note', L, 'position:absolute;right:' + (W / 2 - tw / 2) + 'px;top:' + (H * .5 + th * .58) + 'px;color:rgba(239,231,218,.55)') : null; if (nt) nt.textContent = note;
  return {dark: true, update: function(t){
    var d = es(seg(t, .02, .72)), q = 1 - d;
    cps[0].style.opacity = lerp(.08, 1, es(seg(d, 0, .35))).toFixed(3); cps[1].style.opacity = es(seg(d, .25, .65)).toFixed(3); cps[2].style.opacity = es(seg(d, .55, 1)).toFixed(3);
    edge.style.opacity = es(seg(t, .3, .7)).toFixed(3); gr.style.opacity = lerp(.75, .4, d).toFixed(2);
    sheen.style.transform = 'translateX(' + lerp(-60, 0, t).toFixed(1) + '%)';
    rip.forEach(function(r, i){ var ph2 = (t * 2.2 + i * .5) % 1; r.style.transform = 'scale(' + lerp(.92, 1.35, ph2).toFixed(3) + ')'; r.style.opacity = ((1 - ph2) * .9).toFixed(3); });
    var s = M.round(t * 95); tm.textContent = 'DEV  ' + M.floor(s / 60) + '\u2032' + ('0' + s % 60).slice(-2) + '\u2033';
    if (nt) nt.style.opacity = es(seg(t, .6, .9)); }};
};

/* 3 · ink — 墨迹晕染：墨滴落在纸上，沿纤维晕开，边缘积墨；字从墨里留白显出 */
P.ink = function(L, o, C){
  var W = C.W, H = C.H, word = o.word || '', note = o.note || '', nd = M.max(1, M.min(3, +o.drops || 1));
  el('div', '', L, 'position:absolute;inset:0;background-image:url(' + fiberURL() + ');background-size:320px;opacity:.55');
  var cv = el('canvas', '', L), cw = M.min(440, M.round(W * M.min(2, window.devicePixelRatio || 1) / 1.7)), ch = M.round(cw * H / W); cv.width = cw; cv.height = ch;
  var x = cv.getContext('2d'), img = x.createImageData(cw, ch), A = new Float32Array(cw * ch), N = new Float32Array(cw * ch), n = vnoise(C.seed + 21);
  var spots = [[.5, .47, 1]], R0 = M.min(cw, ch) * (C.mob ? .33 : .34), R = rng(C.seed + 4), n2 = vnoise(C.seed + 33);
  if (nd === 2) spots = [[.4, .44, 1], [.63, .56, .85]]; if (nd === 3) spots = [[.36, .42, 1], [.6, .38, .8], [.52, .64, .85]];
  if (nd > 1) R0 *= .74;
  for (var sp2 = 0, ns = spots.length; sp2 < ns; sp2++) for (var q2 = 0; q2 < 3; q2++){ var an = R() * PI * 2, rr = (1.15 + R() * .5) * R0 * spots[sp2][2];
    spots.push([spots[sp2][0] + M.cos(an) * rr / cw, spots[sp2][1] + M.sin(an) * rr / ch, .06 + R() * .06, .3 + R() * .3]); }
  for (var yy = 0; yy < ch; yy++) for (var xx = 0; xx < cw; xx++){ var k = yy * cw + xx, best = 9;
    var wx = (n(xx / cw * 3 + 7, yy / ch * 3, 3) - .5) * R0 * .9, wy = (n(xx / cw * 3, yy / ch * 3 + 3, 3) - .5) * R0 * .9, fib = n2(xx / cw * 3, yy / ch * 40, 2);
    for (var s = 0; s < spots.length; s++){ var sz = R0 * spots[s][2], dx = xx + wx * spots[s][2] - spots[s][0] * cw, dy = (yy + wy * spots[s][2] - spots[s][1] * ch) * 1.06, d = M.sqrt(dx * dx + dy * dy) / sz;
      var a = d * (.86 + .3 * n(xx / cw * 9, yy / ch * 9, 2)) * (1 - .1 * (fib - .5)) + (spots[s][3] || s * .1); if (a < best) best = a; }
    A[k] = best; N[k] = n2(xx / 9, yy / 9, 4) * .7 + n2(xx / 2.2, yy / 2.2, 1) * .3; }
  var inkc = night() ? rgb(css('ink')) : [20, 23, 38];
  var wbox = el('div', 'w', L, 'left:' + spots[0][0] * W + 'px;top:' + spots[0][1] * H + 'px;transform:translate(-50%,-50%);font-size:' + fit(word, R0 * W / cw * .9, H * .18) + 'px;font-weight:900;color:var(--paper);letter-spacing:.08em');
  wbox.textContent = word;
  var nt = note ? el('div', 'note', L, 'position:absolute;left:50%;bottom:' + H * .08 + 'px;transform:translateX(-50%)') : null; if (nt) nt.textContent = note;
  var lastF = -1;
  function paint(f){ var D = img.data;
    for (var k = 0; k < A.length; k++){ var a = A[k] + (N[k] - .5) * .07, al = 0;
      if (a < f + .05){ var e = es(seg(a, f + .05, f - .04)), rim = M.exp(-M.pow((f - a) / .06, 2)) * .16, body = .5 + .34 * N[k] + .16 * clamp(1 - a / M.max(f, .05));
        al = M.min(.97, body + rim) * e; }
      var i = k * 4; D[i] = inkc[0]; D[i + 1] = inkc[1]; D[i + 2] = inkc[2]; D[i + 3] = al * 255; }
    x.putImageData(img, 0, 0); }
  return {theme: function(){ inkc = night() ? rgb(css('ink')) : [20, 23, 38]; lastF = -1; }, update: function(t){
    var f = eo(seg(t, 0, .78)) * .92 + .12; if (M.abs(f - lastF) > .002){ paint(f); lastF = f; }
    var w = es(seg(t, .4, .78)); wbox.style.opacity = w.toFixed(3); wbox.style.filter = 'blur(' + ((1 - w) * 5).toFixed(1) + 'px)';
    wbox.style.letterSpacing = lerp(.02, .12, es(seg(t, .55, 1))).toFixed(3) + 'em';
    if (nt) nt.style.opacity = es(seg(t, .7, .95)); }};
};

/* 4 · press — 凸版压印：字被压进纸里，墨不均匀地吃进纤维；角线 / 套准标；可选一枚小印 */
P.press = function(L, o, C){
  var W = C.W, H = C.H, word = o.word || '', note = o.note || '', seal = (o.seal || '').slice(0, 2);
  el('div', '', L, 'position:absolute;inset:0;background-image:url(' + fiberURL() + ');background-size:320px;opacity:.6');
  var g = sv('svg', {}, L), m = M.min(W, H) * .09, c = 'var(--ink3)';
  [[m, m, 1, 1], [W - m, m, -1, 1], [m, H - m, 1, -1], [W - m, H - m, -1, -1]].forEach(function(p){
    sv('path', {d: 'M' + (p[0] - p[2] * 6) + ' ' + p[1] + 'h' + (-p[2] * 18) + 'M' + p[0] + ' ' + (p[1] - p[3] * 6) + 'v' + (-p[3] * 18), stroke: c, 'stroke-width': .8, fill: 'none'}, g); });
  [[W / 2, m * .55], [W / 2, H - m * .55]].forEach(function(p){ sv('circle', {cx: p[0], cy: p[1], r: 6, stroke: c, fill: 'none', 'stroke-width': .8}, g);
    sv('path', {d: 'M' + (p[0] - 11) + ' ' + p[1] + 'h22M' + p[0] + ' ' + (p[1] - 11) + 'v22', stroke: c, 'stroke-width': .8}, g); });
  var size = fit(word, W * .72, H * .38), top = H * .47;
  var deb = el('div', 'w', L, 'left:50%;top:' + top + 'px;transform:translate(-50%,-50%);font-size:' + size + 'px;font-weight:900;letter-spacing:.06em'); deb.textContent = word;
  var ink = el('div', 'w', L, 'left:50%;top:' + top + 'px;font-size:' + size + 'px;font-weight:900;color:var(--ink);letter-spacing:.06em;-webkit-mask-image:url(' + speckURL() + ');mask-image:url(' + speckURL() + ');-webkit-mask-size:' + size * .9 + 'px;mask-size:' + size * .9 + 'px');
  ink.textContent = word;
  var sl = null; if (seal){ var ss = M.max(30, size * .32); var sx = M.min(W / 2 + chars(word).length * size * .55, W - ss - m * .6); sl = el('div', 'w', L, 'left:' + sx + 'px;top:' + (top + size * .2) + 'px;width:' + ss + 'px;height:' + ss + 'px;display:flex;align-items:center;justify-content:center;background:var(--safe);color:var(--paper);font-size:' + ss * (seal.length > 1 ? .4 : .62) + 'px;font-weight:700;border-radius:3px;-webkit-mask-image:url(' + speckURL() + ');mask-image:url(' + speckURL() + ');-webkit-mask-size:70px;mask-size:70px;writing-mode:vertical-rl');
    sl.textContent = seal; }
  var nt = note ? el('div', 'note', L, 'position:absolute;left:50%;top:' + (top + size * .78) + 'px;transform:translateX(-50%)') : null; if (nt) nt.textContent = note;
  return {update: function(t){
    var s = lerp(.3, 1, es(seg(t, .04, .42))), nightK = night() ? .5 : 1; deb.style.color = night() ? '#2b2c31' : 'var(--paper2)';
    deb.style.textShadow = '0 ' + (-1.2 * s).toFixed(2) + 'px ' + (1.2 * s).toFixed(2) + 'px rgba(0,0,0,' + (.32 * s * nightK + (night() ? .25 * s : 0)).toFixed(3) + '),0 ' + (1.3 * s).toFixed(2) + 'px ' + (.8 * s).toFixed(2) + 'px rgba(255,255,255,' + (.75 * s * (night() ? .12 : 1)).toFixed(3) + ')';
    deb.style.transform = 'translate(-50%,-50%) scale(' + lerp(1.035, 1, s).toFixed(4) + ')'; deb.style.opacity = lerp(.25, 1, s).toFixed(3);
    var k = es(seg(t, .38, .8)); ink.style.opacity = (k * .9).toFixed(3);
    ink.style.transform = 'translate(calc(-50% + ' + ((1 - k) * 5 + .7).toFixed(2) + 'px),calc(-50% + ' + ((1 - k) * -3 + .4).toFixed(2) + 'px))';
    if (sl){ var q = eo(seg(t, .72, .86)); sl.style.opacity = q * .92; sl.style.transform = 'rotate(-4deg) scale(' + lerp(1.35, 1, q).toFixed(3) + ')'; }
    if (nt) nt.style.opacity = es(seg(t, .78, 1)); }};
};

/* 5 · window — 窗格投影：一块窗形的光在墙上移动、拉长、变色；尘埃只在光里亮；字只在光照到的地方显出 */
P.window = function(L, o, C){
  var W = C.W, H = C.H, cols = M.max(2, M.min(4, +o.cols || 2)), rows = M.max(2, M.min(4, +o.rows || 3)), word = o.word || '';
  var LC = {noon: [255, 240, 205], dusk: [250, 198, 150], night: [168, 192, 222]}, c0 = LC[o.from] || LC.noon, c1 = LC[o.to] || LC.dusk;
  var wall = el('div', '', L, 'position:absolute;inset:0'), shade = el('div', '', L, 'position:absolute;inset:0;mix-blend-mode:multiply');
  var glow = el('div', '', L, 'position:absolute;left:0;top:0;width:' + W * 1.1 + 'px;height:' + H * 1.1 + 'px;margin:' + (-H * .55) + 'px 0 0 ' + (-W * .55) + 'px;will-change:transform');
  var lc = el('canvas', '', L), LS = 8; lc.width = M.ceil(W / LS); lc.height = M.ceil(H / LS); var lx = lc.getContext('2d');
  var cv = el('canvas', '', L); cv.width = M.round(W); cv.height = M.round(H); var x = cv.getContext('2d'), R = rng(C.seed + 5), dust = [];
  for (var j = 0; j < 90; j++) dust.push([R() * W, R() * H, .5 + R() * 1.3, R() * 2 - 1, R()]);
  var wd, wl, size = fit(word, W * .5, H * .26);
  if (word){ wd = el('div', 'w', L, 'left:' + W * .55 + 'px;top:' + H * .52 + 'px;transform:translate(-50%,-50%);font-size:' + size + 'px;font-weight:900;color:var(--ink);opacity:.07;letter-spacing:.1em'); wd.textContent = word;
    wl = el('div', '', L, 'position:absolute;inset:0'); var w2 = el('div', 'w', wl, 'left:' + W * .55 + 'px;top:' + H * .52 + 'px;transform:translate(-50%,-50%);font-size:' + size + 'px;font-weight:900;color:var(--ink);opacity:.8;letter-spacing:.1em'); w2.textContent = word; }
  var Q0 = C.mob ? [[.06, .14], [.42, .1], [.5, .62], [.12, .7]] : [[.14, .16], [.38, .1], [.45, .68], [.2, .76]];
  var Q1 = C.mob ? [[.46, .3], [.94, .26], [1.0, .9], [.56, .96]] : [[.5, .24], [.84, .2], [.98, .86], [.6, .93]];
  function bil(Q, u, v){ var a = [lerp(Q[0][0], Q[1][0], u), lerp(Q[0][1], Q[1][1], u)], b = [lerp(Q[3][0], Q[2][0], u), lerp(Q[3][1], Q[2][1], u)]; return [lerp(a[0], b[0], v) * W, lerp(a[1], b[1], v) * H]; }
  function inside(Q, px, py){ var s = 0; for (var k = 0; k < 4; k++){ var a = Q[k], b = Q[(k + 1) % 4], cr = (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0]); if (cr > 0) s++; else s--; } return M.abs(s) === 4; }
  function setWall(){ var nt = night(); wall.style.background = nt ? 'linear-gradient(170deg,#24252a,#141518)' : 'linear-gradient(170deg,#ddd3c3,#c7bba6)'; }
  setWall();
  return {theme: setWall, update: function(t){
    var e = eio(t), Q = Q0.map(function(p, k){ return [lerp(p[0], Q1[k][0], e), lerp(p[1], Q1[k][1], e)]; });
    var cc = [0, 1, 2].map(function(k){ return M.round(lerp(c0[k], c1[k], es(seg(t, .25, 1)))); }), a = night() ? .42 : .62;
    var mx = (Q[0][0] + Q[2][0]) / 2 * 100, my = (Q[0][1] + Q[2][1]) / 2 * 100; glow.style.transform = 'translate(' + (mx * W / 100).toFixed(1) + 'px,' + (my * H / 100).toFixed(1) + 'px)';
    if (!glow._c || M.abs(glow._c - cc[1]) > 6 || glow._n !== night()){ glow._c = cc[1]; glow._n = night(); glow.style.background = 'radial-gradient(closest-side,' + rgba(cc, night() ? .12 : .22) + ',transparent)'; }
    var mw = .055; lx.clearRect(0, 0, lc.width, lc.height);
    for (var r = 0; r < rows; r++) for (var c = 0; c < cols; c++){
      var u0 = c / cols + mw / 2, u1 = (c + 1) / cols - mw / 2, v0 = r / rows + mw / 2, v1 = (r + 1) / rows - mw / 2;
      var pts = [bil(Q, u0, v0), bil(Q, u1, v0), bil(Q, u1, v1), bil(Q, u0, v1)];
      lx.fillStyle = rgba(cc, a * (1 - .35 * (r / rows))); lx.beginPath(); lx.moveTo(pts[0][0] / LS, pts[0][1] / LS);
      for (var z = 1; z < 4; z++) lx.lineTo(pts[z][0] / LS, pts[z][1] / LS); lx.closePath(); lx.fill(); }
    var dk = (o.to === 'night' ? .38 : o.to === 'dusk' ? .14 : 0) * es(seg(t, .3, 1));
    shade.style.background = 'rgba(40,52,84,' + dk.toFixed(3) + ')';
    var QP = Q.map(function(p){ return [p[0] * W, p[1] * H]; });
    if (wl) wl.style.clipPath = 'polygon(' + QP.map(function(p){ return p[0].toFixed(1) + 'px ' + p[1].toFixed(1) + 'px'; }).join(',') + ')';
    x.clearRect(0, 0, W, H);
    for (var d = 0; d < dust.length; d++){ var q = dust[d], px = (q[0] + t * W * .08 * q[3] + W) % W, py = (q[1] - t * H * .12 * (q[4] + .3) + H) % H;
      var on = inside(QP, px, py); if (!on && q[4] > .25) continue;
      x.fillStyle = on ? 'rgba(255,248,232,' + (.35 + .5 * q[4]).toFixed(2) + ')' : 'rgba(255,255,255,.12)'; x.beginPath(); x.arc(px, py, q[2], 0, 6.283); x.fill(); } }};
};

/* 6 · focus — 景深 / 对焦转移：远处一个词、近处一个词，焦点从远拉到近；光斑跟着变大；底部是镜头距离刻度 */
P.focus = function(L, o, C){
  L.classList.add('dk'); var W = C.W, H = C.H, far = o.far || '', near = o.near || '', R = rng(C.seed + 9);
  var LC = {warm: [255, 186, 112], cool: [146, 186, 232], safe: [232, 72, 60]}, lc = LC[o.light] || LC.warm;
  L.style.background = 'radial-gradient(120% 90% at 50% 40%,#1c1814,#0a0908)';
  var bl = el('div', '', L, 'position:absolute;inset:-8%;'), bk = [];
  for (var i = 0; i < 16; i++){ var s = W * (.035 + R() * .12), cc = R() < .2 ? [255, 236, 210] : lc, al = .55 + R() * .45;
    var b = el('div', 'bokeh', bl, 'left:' + (R() * 100) + '%;top:' + (8 + R() * 70) + '%;width:' + s + 'px;height:' + s + 'px;margin:' + (-s / 2) + 'px;mix-blend-mode:screen;' +
      'filter:blur(' + (1 + R() * 2.5).toFixed(1) + 'px);background:radial-gradient(circle,' + rgba(cc, .16 * al) + ' 0,' + rgba(cc, .2 * al) + ' 56%,' + rgba(cc, .28 * al) + ' 66%,' + rgba(cc, 0) + ' 71%)');
    bk.push([b, R() * 2 - 1]); }
  var fs = fit(far, W * .5, H * .22), ns = fit(near, W * .56, H * .3);
  var fw = el('div', 'w', L, 'left:' + W * (C.mob ? .1 : .16) + 'px;top:' + H * .24 + 'px;font-size:' + fs + 'px;font-weight:600;color:rgba(239,231,218,.92);letter-spacing:.18em'); fw.textContent = far;
  var nw = el('div', 'w', L, 'right:' + W * (C.mob ? .08 : .14) + 'px;top:' + H * .5 + 'px;font-size:' + ns + 'px;font-weight:900;color:' + rgba([255, 242, 224], 1) + ';letter-spacing:.06em'); nw.textContent = near;
  var g = sv('svg', {}, L), y = H * .86, x0 = W * .16, x1 = W * .84, marks = ['\u221E', '10', '5', '3', '2', '1.5', '1', '0.7'];
  sv('line', {x1: x0, x2: x1, y1: y, y2: y, stroke: 'rgba(239,231,218,.28)', 'stroke-width': .8}, g);
  marks.forEach(function(m, k){ var xx = lerp(x0, x1, k / (marks.length - 1)); sv('line', {x1: xx, x2: xx, y1: y - 4, y2: y + 4, stroke: 'rgba(239,231,218,.4)', 'stroke-width': .8}, g);
    var tx = sv('text', {x: xx, y: y + 18, 'text-anchor': 'middle', fill: 'rgba(239,231,218,.5)', 'font-size': 10, 'font-family': 'monospace', 'letter-spacing': '.05em'}, g); tx.textContent = m; });
  var idx = sv('path', {d: 'M0 0l-5 -8h10z', fill: rgba(lc, .95)}, g);
  var mt = sv('text', {x: x0 - 10, y: y + 4, 'text-anchor': 'end', fill: 'rgba(239,231,218,.45)', 'font-size': 10, 'font-family': 'monospace'}, g); mt.textContent = 'm';
  return {dark: true, update: function(t){
    var r = eio(seg(t, .3, .72));
    fw.style.filter = 'blur(' + (r * 9).toFixed(1) + 'px)'; fw.style.opacity = lerp(1, .42, r).toFixed(3); fw.style.transform = 'translateX(' + lerp(-6, 8, t).toFixed(1) + 'px)';
    nw.style.filter = 'blur(' + ((1 - r) * 13).toFixed(1) + 'px)'; nw.style.opacity = lerp(.3, 1, r).toFixed(3); nw.style.transform = 'translateX(' + lerp(26, -18, t).toFixed(1) + 'px) scale(' + lerp(1.08, 1, r).toFixed(3) + ')';
    bl.style.transform = 'translateX(' + lerp(-14, 14, t).toFixed(1) + 'px) scale(' + lerp(1, 1.3, r).toFixed(3) + ')';
    bk.forEach(function(q){ q[0].style.opacity = lerp(.55 + .3 * q[1], 1, r).toFixed(3); });
    idx.setAttribute('transform', 'translate(' + lerp(x0, x1, lerp(.04, .86, r)).toFixed(1) + ',' + (y - 6) + ')'); }};
};

/* 7 · orbit — 关系引力：两个人是两个点；距离、张力线、被彼此挤压的场线；下方是尺寸标注 */
P.orbit = function(L, o, C){
  var W = C.W, H = C.H, ids = [o.a, o.b, o.c], cy = H * (o.c ? .52 : .47);
  function who(id){ var c = (C.chars || []).filter(function(x){ return x.id === id; })[0]; return {name: c ? c.name : (id || ''), col: c ? 'var(--c-' + c.id + ')' : 'var(--ink)'}; }
  var A = who(ids[0]), B = who(ids[1]), Cc = ids[2] ? who(ids[2]) : null;
  var dist = (o.dist && o.dist.length ? o.dist : [.9, .55, .25]).map(function(v){ return clamp(+v || 0, .04, 1); }), ten = (o.tension && o.tension.length ? o.tension : [.3, .6, .4]).map(function(v){ return clamp(+v || 0); });
  var g = sv('svg', {}, L), rg = sv('g', {}, g), th = sv('g', {}, g), rings = [], threads = [], NT = 13;
  for (var s = 0; s < 2; s++) for (var k = 0; k < 5; k++) rings.push(sv('path', {fill: 'none', stroke: k ? 'var(--ink3)' : 'var(--ink2)', 'stroke-width': k ? .9 : .7, opacity: (1 - k * .15).toFixed(2)}, rg));
  for (var i = 0; i < NT; i++) threads.push(sv('path', {fill: 'none', stroke: i === (NT >> 1) ? 'var(--safe)' : 'var(--ink2)', 'stroke-width': i === (NT >> 1) ? 1.3 : .7, opacity: i === (NT >> 1) ? .95 : .62}, th));
  function dot(P2){ var gg = sv('g', {}, g); sv('circle', {r: 15, fill: 'none', stroke: P2.col, 'stroke-width': 1, opacity: .4}, gg); sv('circle', {r: 7, fill: P2.col}, gg);
    var tx = sv('text', {y: 40, 'text-anchor': 'middle', fill: 'var(--ink)', 'font-size': M.max(14, M.min(18, W * .022)), 'letter-spacing': '.24em', 'font-family': SERIF}, gg); tx.textContent = P2.name; gg.tx = tx; return gg; }
  var dA = dot(A), dB = dot(B), dC = Cc ? dot(Cc) : null, cl = Cc ? [sv('line', {stroke: 'var(--ink3)', 'stroke-dasharray': '2 4'}, rg), sv('line', {stroke: 'var(--ink3)', 'stroke-dasharray': '2 4'}, rg)] : null;
  var my = cy + H * .26, dim = sv('path', {fill: 'none', stroke: 'var(--ink2)', 'stroke-width': .8}, g);
  var ml = o.measure ? sv('text', {y: my + 20, 'text-anchor': 'middle', fill: 'var(--ink2)', 'font-size': 12, 'letter-spacing': '.3em', 'font-family': SERIF}, g) : null; if (ml) ml.textContent = o.measure;
  function ring(cx, r, toward, prox){ var d = ''; for (var a = 0; a <= 48; a++){ var th2 = a / 48 * PI * 2, rr = r * (1 - .32 * prox * M.max(0, M.cos(th2 - toward)) + .06 * prox * M.cos(2 * th2));
      d += (a ? 'L' : 'M') + (cx + M.cos(th2) * rr).toFixed(1) + ' ' + (cy + M.sin(th2) * rr * .82).toFixed(1); } return d + 'Z'; }
  return {update: function(t){
    var d = keys(dist, t), te = keys(ten, t), half = M.max(W * .03, d * W * (C.mob ? .4 : .34)), xa = W / 2 - half, xb = W / 2 + half, prox = 1 - d;
    dA.setAttribute('transform', 'translate(' + xa.toFixed(1) + ',' + cy + ')'); dB.setAttribute('transform', 'translate(' + xb.toFixed(1) + ',' + cy + ')');
    var tight = xb - xa < 130; dA.tx.setAttribute('text-anchor', tight ? 'end' : 'middle'); dA.tx.setAttribute('x', tight ? -6 : 0); dB.tx.setAttribute('text-anchor', tight ? 'start' : 'middle'); dB.tx.setAttribute('x', tight ? 6 : 0);
    for (var k = 0; k < 5; k++){ var r = M.min(H, W * 1.1) * (.055 + k * .068) * (1 + .15 * te);
      rings[k].setAttribute('d', ring(xa, r, 0, prox)); rings[5 + k].setAttribute('d', ring(xb, r, PI, prox)); }
    for (var i = 0; i < NT; i++){ var u = i / (NT - 1) - .5, sag = u * (H * .34 * (1 - te) + H * .012) * (1 + prox * .4), vib = te > .5 ? M.sin(i * 2.3 + t * 70) * (te - .5) * 7 : 0;
      var c1 = xa + (xb - xa) * .3, c2 = xa + (xb - xa) * .7;
      threads[i].setAttribute('d', 'M' + (xa + 7).toFixed(1) + ' ' + cy + 'C' + c1.toFixed(1) + ' ' + (cy + sag + vib).toFixed(1) + ' ' + c2.toFixed(1) + ' ' + (cy + sag - vib).toFixed(1) + ' ' + (xb - 7).toFixed(1) + ' ' + cy); }
    dim.setAttribute('d', 'M' + xa.toFixed(1) + ' ' + (my - 5) + 'v10M' + xa.toFixed(1) + ' ' + my + 'H' + xb.toFixed(1) + 'M' + xb.toFixed(1) + ' ' + (my - 5) + 'v10');
    if (ml) ml.setAttribute('x', (W / 2).toFixed(1));
    if (dC){ var cx3 = W / 2, cy3 = cy - H * .34 + prox * H * .04; dC.setAttribute('transform', 'translate(' + cx3 + ',' + cy3.toFixed(1) + ')'); dC.style.opacity = .7;
      cl[0].setAttribute('x1', cx3); cl[0].setAttribute('y1', cy3); cl[0].setAttribute('x2', xa); cl[0].setAttribute('y2', cy);
      cl[1].setAttribute('x1', cx3); cl[1].setAttribute('y1', cy3); cl[1].setAttribute('x2', xb); cl[1].setAttribute('y2', cy); } }};
};

/* 8 · door — 门缝光：黑暗里门慢慢开出一条光，光在地板上铺成一块梯形；字躺在光里 */
P.door = function(L, o, C){
  L.classList.add('dk'); var W = C.W, H = C.H, word = o.word || '', LC = {warm: [248, 214, 156], cool: [178, 204, 236]}, lc = LC[o.light] || LC.warm;
  L.style.background = 'linear-gradient(180deg,#120f0d,#0a0807 70%,#0d0b09)';
  var dw = M.min(W * (C.mob ? .4 : .22), H * .42), dh = H * .66, x0 = W * (C.mob ? .26 : .36), y0 = H * .1, fy = y0 + dh;
  var spill = el('div', '', L, 'position:absolute;left:' + (x0 + dw - dw * .5) + 'px;top:' + (y0 - dh * .2) + 'px;width:' + dw * 2.2 + 'px;height:' + dh * 1.5 + 'px;background:radial-gradient(closest-side,' + rgba(lc, .16) + ',transparent);margin-left:' + (-dw * .6) + 'px');
  el('div', '', L, 'position:absolute;left:0;right:0;top:' + fy + 'px;height:1px;background:rgba(255,240,220,.05)');
  var wc = el('canvas', '', L), WS = 4; wc.width = M.ceil(W / WS); wc.height = M.ceil(H / WS); var wx = wc.getContext('2d');
  var wg = wx.createLinearGradient(0, fy / WS, 0, H / WS); wg.addColorStop(0, rgba(lc, .62)); wg.addColorStop(1, rgba(lc, 0));
  var frame = el('div', '', L, 'position:absolute;left:' + (x0 - 3) + 'px;top:' + (y0 - 3) + 'px;width:' + (dw + 6) + 'px;height:' + (dh + 3) + 'px;border:3px solid #1b1714;border-bottom:0;box-shadow:inset 0 0 0 1px rgba(255,255,255,.03)');
  var gap = el('div', '', L, 'position:absolute;top:' + y0 + 'px;height:' + dh + 'px;background:linear-gradient(90deg,' + rgba(lc, .9) + ',#fff8ec 45%,' + rgba(lc, .85) + ');box-shadow:0 0 34px ' + rgba(lc, .45));
  var slab = el('div', '', L, 'position:absolute;left:' + x0 + 'px;top:' + y0 + 'px;height:' + dh + 'px;background:linear-gradient(90deg,#1a1512,#120e0c);box-shadow:inset -1px 0 0 ' + rgba(lc, .55));
  [[.12, .08, .76, .36], [.12, .52, .76, .38]].forEach(function(r){ el('div', '', slab, 'position:absolute;left:' + r[0] * 100 + '%;top:' + r[1] * 100 + '%;width:' + r[2] * 100 + '%;height:' + r[3] * 100 + '%;box-shadow:inset 1px 1px 0 rgba(255,255,255,.035),inset -1px -1px 0 rgba(0,0,0,.4)'); });
  var knob = el('div', '', slab, 'position:absolute;right:9%;top:52%;width:5px;height:5px;border-radius:50%;background:' + rgba(lc, .5));
  var fw = null; if (word){ var fz = fit(word, dw * 1.6, H * .12); fw = el('div', 'w', L, 'left:' + (x0 + dw * 1.02) + 'px;top:' + (fy + (H - fy) * .3) + 'px;transform-origin:0 50%;font-size:' + fz + 'px;font-weight:900;color:rgba(28,20,12,.78);letter-spacing:.2em'); fw.textContent = word; }
  var cv = el('canvas', '', L); cv.width = M.round(W); cv.height = M.round(H); var x = cv.getContext('2d'), R = rng(C.seed + 13), dust = [];
  for (var j = 0; j < 70; j++) dust.push([R(), R(), .4 + R() * 1.1, R()]);
  return {dark: true, update: function(t){
    var k = eo(seg(t, .04, .8)), gw = k * dw * .46 + 1.2, lx0 = x0 + dw - gw, lx1 = x0 + dw;
    gap.style.left = lx0.toFixed(1) + 'px'; gap.style.width = gw.toFixed(1) + 'px';
    slab.style.width = (dw - gw).toFixed(1) + 'px'; spill.style.opacity = (.2 + .8 * k).toFixed(3); knob.style.opacity = k;
    var reach = (H - fy) * (1.0 + 1.6 * k), pts = [[lx0, fy], [lx1, fy], [lx1 + gw * 1.6 + reach * 1.2, fy + (H - fy) * 1.05], [lx0 + reach * .22, fy + (H - fy) * 1.05]];
    wx.clearRect(0, 0, wc.width, wc.height); wx.globalAlpha = .3 + .7 * k; wx.fillStyle = wg; wx.beginPath(); wx.moveTo(pts[0][0] / WS, pts[0][1] / WS);
    for (var z = 1; z < 4; z++) wx.lineTo(pts[z][0] / WS, pts[z][1] / WS); wx.closePath(); wx.fill(); wx.globalAlpha = 1;
    if (fw){ fw.style.transform = 'perspective(' + (H * .9).toFixed(0) + 'px) rotateX(58deg) skewX(-34deg)'; fw.style.opacity = es(seg(t, .45, .85)).toFixed(3);
    }
    x.clearRect(0, 0, W, H);
    for (var d = 0; d < dust.length; d++){ var q = dust[d], u = q[0], v = (q[1] + t * .25 * (q[3] + .2)) % 1, py = y0 + v * (H - y0), w2 = py < fy ? gw : gw + (py - fy) / (H - fy) * reach;
      var px = (py < fy ? lx0 : lx0 + (py - fy) / (H - fy) * reach * .2) + u * w2 * 1.1;
      x.fillStyle = rgba([255, 244, 222], (.15 + .55 * q[3]) * k); x.beginPath(); x.arc(px, py, q[2], 0, 6.283); x.fill(); } }};
};

/* 9 · spoon — 物件特写：一把勺子，金属渐变 + 高光滑过 + 窗形反光 + 投影随光移动；一条细引线做注释 */
P.spoon = function(L, o, C){
  var W = C.W, H = C.H, word = o.word || '', note = o.note || '';
  var bg = el('div', '', L, 'position:absolute;inset:0');
  function setBg(){ bg.style.background = night() ? 'radial-gradient(120% 100% at 25% 0%,#2a2724,#141311 75%)' : 'radial-gradient(120% 100% at 22% 0%,#fbf7f0,#e6ddcd 62%,#d3c8b4)'; }
  setBg();
  var g = sv('svg', {viewBox: C.mob ? '0 0 440 540' : '0 0 800 520', preserveAspectRatio: 'xMidYMid meet'}, L), defs = sv('defs', {}, g), id = 'sp' + C.idx;
  function grad(tag, gid, at, stops){ var gr = sv(tag, Object.assign({id: gid}, at), defs); stops.forEach(function(s){ sv('stop', {offset: s[0], 'stop-color': s[1]}, gr); }); return gr; }
  grad('linearGradient', id + 'h', {x1: 0, y1: 0, x2: 0, y2: 1}, [[0, '#3b3834'], [.26, '#b9b3aa'], [.42, '#fbf9f5'], [.56, '#837d74'], [.8, '#dcd8d1'], [1, '#33302d']]);
  var bowlG = grad('radialGradient', id + 'b', {cx: .4, cy: .38, r: .75}, [[0, '#ffffff'], [.1, '#ece8e1'], [.42, '#86807a'], [.74, '#c9c4bc'], [1, '#3a3632']]);
  var sf = sv('filter', {id: id + 's', x: '-20%', y: '-40%', width: '140%', height: '180%'}, defs); sv('feGaussianBlur', {stdDeviation: 7}, sf);
  var gf = sv('filter', {id: id + 'g'}, defs); sv('feGaussianBlur', {stdDeviation: 1.2}, gf);
  var cp = sv('clipPath', {id: id + 'c'}, defs); sv('ellipse', {cx: 0, cy: 0, rx: 70, ry: 46}, cp);
  var shape = 'M-70 0a70 46 0 1 0 140 0a70 46 0 1 0 -140 0ZM62 -9C120 -11 200 -9 300 -12C318 -12.5 324 -6 324 0C324 6 318 12.5 300 12C200 9 120 11 62 9Z';
  var root = sv('g', {}, g), sh = sv('path', {d: shape, fill: 'rgba(48,34,20,.36)', filter: 'url(#' + id + 's)', transform: 'translate(6,16)'}, root);
  var sp = sv('g', {}, root);
  sv('path', {d: 'M62 -9C120 -11 200 -9 300 -12C318 -12.5 324 -6 324 0C324 6 318 12.5 300 12C200 9 120 11 62 9Z', fill: 'url(#' + id + 'h)', stroke: 'rgba(40,34,28,.35)', 'stroke-width': .7}, sp);
  sv('ellipse', {cx: 0, cy: 0, rx: 70, ry: 46, fill: 'url(#' + id + 'b)', stroke: 'rgba(40,34,28,.4)', 'stroke-width': .8}, sp);
  sv('ellipse', {cx: 0, cy: 2, rx: 62, ry: 39, fill: 'none', stroke: 'rgba(255,255,255,.55)', 'stroke-width': 1.2}, sp);
  var cl = sv('g', {'clip-path': 'url(#' + id + 'c)'}, sp), glint = sv('g', {filter: 'url(#' + id + 'g)'}, cl);
  [[0, 0], [13, 0], [0, 17], [13, 17]].forEach(function(q){ sv('rect', {x: q[0], y: q[1], width: 11, height: 15, rx: 1, fill: 'rgba(255,255,255,.88)'}, glint); });
  var streak = sv('rect', {x: 90, y: -4, width: 60, height: 3, rx: 1.5, fill: 'rgba(255,255,255,.85)', filter: 'url(#' + id + 'g)'}, sp);
  var lead = sv('path', {fill: 'none', stroke: 'var(--ink2)', 'stroke-width': .8}, g), ldot = sv('circle', {r: 2.6, fill: 'var(--safe)'}, g);
  var lt = sv('text', {'font-size': 22, 'font-family': SERIF, 'font-weight': 700, fill: 'var(--ink)', 'letter-spacing': '.2em'}, g); lt.textContent = word;
  var ln = sv('text', {'font-size': 12, fill: 'var(--ink2)', 'letter-spacing': '.3em', 'font-family': 'sans-serif'}, g); ln.textContent = note;
  var cx = C.mob ? 128 : 360, cy = C.mob ? 392 : 300, sc = C.mob ? 1.22 : 1.25, r0 = C.mob ? -54 : -26, r1 = C.mob ? -48 : -20;
  return {theme: setBg, update: function(t){
    var rot = lerp(r0, r1, eio(t));
    root.setAttribute('transform', 'translate(' + cx + ',' + cy + ') rotate(' + rot.toFixed(2) + ') scale(' + sc + ')');
    
    var hx = lerp(.2, .72, eio(seg(t, .05, .9))); bowlG.setAttribute('cx', hx.toFixed(3)); bowlG.setAttribute('cy', lerp(.3, .46, t).toFixed(3));
    var gx = lerp(-46, 22, eio(seg(t, .05, .9))), gy = lerp(-28, -6, t); glint.setAttribute('transform', 'translate(' + gx.toFixed(1) + ',' + gy.toFixed(1) + ') skewX(-14) scale(' + lerp(.8, 1.05, t).toFixed(3) + ',' + lerp(1, .78, t).toFixed(3) + ')');
    streak.setAttribute('x', lerp(80, 230, eio(t)).toFixed(1)); streak.setAttribute('opacity', (.4 + .6 * M.sin(PI * t)).toFixed(3));
    var a = es(seg(t, .45, .75)), rr = rot * PI / 180, px = cx + (gx + 11) * sc * M.cos(rr) - (gy + 15) * sc * M.sin(rr), py = cy + (gx + 11) * sc * M.sin(rr) + (gy + 15) * sc * M.cos(rr);
    var tx = C.mob ? 36 : 590, ty = C.mob ? 96 : 120;
    lead.setAttribute('d', 'M' + px.toFixed(1) + ' ' + py.toFixed(1) + 'L' + (px + (tx - 20 - px) * a).toFixed(1) + ' ' + (py + (ty + 8 - py) * a).toFixed(1) + (a > .98 ? 'H' + (tx + (C.mob ? 110 : 120)) : ''));
    ldot.setAttribute('cx', px.toFixed(1)); ldot.setAttribute('cy', py.toFixed(1)); ldot.setAttribute('opacity', a);
    lt.setAttribute('x', tx); lt.setAttribute('y', ty); lt.setAttribute('opacity', es(seg(t, .62, .85)));
    ln.setAttribute('x', tx); ln.setAttribute('y', ty + 28); ln.setAttribute('opacity', es(seg(t, .72, .95))); }};
};

/* 10 · passing — 夜行车内：路灯一盏盏从头顶扫过，光带斜穿车厢；远处车灯是失焦的光点；玻璃上的雨珠只在灯扫过时亮 */
P.passing = function(L, o, C){
  L.classList.add('dk'); var W = C.W, H = C.H, word = o.word || '', R = rng(C.seed + 17), lamps = M.max(2, M.min(6, +o.lamps || 4));
  L.style.background = 'linear-gradient(180deg,#06070a,#0e1117 46%,#090a0d)';
  var hz = el('div', '', L, 'position:absolute;inset:0'), dots = [];
  for (var i = 0; i < 30; i++){ var s = 4 + R() * 16, kind = R(), cc = kind < .35 ? [255, 70, 50] : kind < .7 ? [255, 190, 110] : [235, 240, 255];
    dots.push([el('div', 'bokeh', hz, 'width:' + s + 'px;height:' + s + 'px;margin:' + (-s / 2) + 'px;top:' + (H * (.44 + R() * .12)) + 'px;background:radial-gradient(circle,' + rgba(cc, .9) + ',' + rgba(cc, .22) + ' 40%,' + rgba(cc, 0) + ' 70%)'), R(), .3 + R() * .9]); }
  var bands = []; for (var j = 0; j < 2; j++) bands.push(el('div', '', L, 'position:absolute;top:-20%;height:140%;width:' + W * .38 + 'px;transform-origin:50% 50%;background:linear-gradient(90deg,rgba(255,168,84,0),rgba(255,176,96,.08) 30%,rgba(255,222,170,.3) 50%,rgba(255,176,96,.07) 70%,rgba(255,168,84,0))'));
  var wash = el('div', '', L, 'position:absolute;inset:0;background:rgba(255,150,70,.5);opacity:0');
  var g = sv('svg', {}, L);
  sv('path', {d: 'M0 0H' + W + 'V' + H * .1 + 'Q' + W / 2 + ' ' + H * .2 + ' 0 ' + H * .1 + 'Z', fill: '#040405'}, g);
  sv('path', {d: 'M0 ' + H * .8 + 'Q' + W * .5 + ' ' + H * .72 + ' ' + W + ' ' + H * .8 + 'V' + H + 'H0Z', fill: '#050506'}, g);
  var dl = sv('path', {d: 'M0 ' + H * .8 + 'Q' + W * .5 + ' ' + H * .72 + ' ' + W + ' ' + H * .8, fill: 'none', stroke: 'rgba(255,190,120,.18)', 'stroke-width': 1}, g);
  sv('ellipse', {cx: W * .5, cy: H * .86, rx: W * .06, ry: H * .02, fill: 'rgba(110,200,220,.10)'}, g);
  var drops = []; if (o.rain !== false){ for (var k = 0; k < 46; k++){ var r = .8 + R() * 2.4; drops.push(sv('circle', {cx: R() * W, cy: H * (.14 + R() * .62), r: r, fill: 'rgba(255,230,200,.5)'}, g)); } }
  var wd = word ? el('div', 'w', L, 'left:' + W * (C.mob ? .08 : .1) + 'px;top:' + H * .62 + 'px;font-size:' + fit(word, W * .36, H * .14) + 'px;font-weight:600;color:#efe7da;letter-spacing:.3em') : null; if (wd) wd.textContent = word;
  return {dark: true, update: function(t){
    var ph = t * lamps, best = 0;
    bands.forEach(function(b, j){ var f = (ph + j * .5) % 1, a = M.pow(M.sin(PI * f), 2); best = M.max(best, a);
      b.style.left = lerp(W * 1.15, -W * .65, f).toFixed(1) + 'px'; b.style.transform = 'skewX(-24deg)'; b.style.opacity = a.toFixed(3); });
    wash.style.opacity = (best * .22).toFixed(3); dl.setAttribute('stroke', 'rgba(255,190,120,' + (.08 + .3 * best).toFixed(3) + ')');
    dots.forEach(function(d){ var x = ((d[1] - t * d[2] * .9) % 1 + 1) % 1; d[0].style.transform = 'translateX(' + (x * W * 1.2 - W * .1).toFixed(1) + 'px)'; });
    drops.forEach(function(d, k){ d.setAttribute('opacity', (.12 + .7 * best * (k % 3 ? 1 : .6)).toFixed(3)); });
    if (wd){ wd.style.opacity = (.2 + .75 * es(seg(t, .3, .7)) * (.55 + .45 * best)).toFixed(3); } }};
};

/* 11 · chart — 情绪读数：本场景真实的紧张/亲密/脸红曲线（来自分镜数值），随滚动画出；直接标注，不用图例框 */
P.chart = function(L, o, C){
  var W = C.W, H = C.H, ser = C.data && C.data.series || {}, want = (o.series && o.series.length ? o.series : ['te', 'in']).filter(function(k){ return ser[k] && ser[k].length > 1; }).slice(0, 3);
  var NAMES = {te: '紧张', 'in': '亲密', bl: '脸红'}, COL = {te: 'var(--ink)', 'in': 'var(--safe)', bl: 'var(--cool)'};
  var ml = W * (C.mob ? .1 : .12), mr = W * (C.mob ? .16 : .16), mt = H * .24, mb = H * .2, pw = W - ml - mr, ph = H - mt - mb;
  var g = sv('svg', {}, L), cid = 'cc' + C.idx, defs = sv('defs', {}, g), clip = sv('rect', {x: ml, y: 0, height: H, width: 0}, sv('clipPath', {id: cid}, defs));
  var ttl = sv('text', {x: ml, y: mt - 34, fill: 'var(--ink)', 'font-size': M.max(15, M.min(19, W * .024)), 'font-family': SERIF, 'font-weight': 700, 'letter-spacing': '.16em'}, g); ttl.textContent = o.title || '情绪读数';
  var sub = sv('text', {x: ml, y: mt - 14, fill: 'var(--ink2)', 'font-size': 11, 'letter-spacing': '.2em', 'font-family': 'sans-serif'}, g); sub.textContent = '本场逐段 · 0–1';
  [0, .25, .5, .75, 1].forEach(function(v){ var y = mt + ph * (1 - v); sv('line', {x1: ml, x2: ml + pw, y1: y, y2: y, stroke: v ? 'var(--ink3)' : 'var(--ink2)', 'stroke-width': v ? .6 : .9, 'stroke-dasharray': v ? '1 4' : ''}, g);
    if (v){ var tx = sv('text', {x: ml - 8, y: y + 3.5, 'text-anchor': 'end', fill: 'var(--ink2)', 'font-size': 10, 'font-family': 'monospace'}, g); tx.textContent = v.toFixed(2).replace(/0$/, ''); } });
  function pts(arr){ return arr.map(function(v, i){ return [ml + pw * i / (arr.length - 1), mt + ph * (1 - clamp(+v || 0))]; }); }
  function path(p){ var d = 'M' + p[0][0].toFixed(1) + ' ' + p[0][1].toFixed(1); for (var i = 0; i < p.length - 1; i++){ var p0 = p[i - 1] || p[i], p1 = p[i], p2 = p[i + 1], p3 = p[i + 2] || p2;
      d += 'C' + (p1[0] + (p2[0] - p0[0]) / 6).toFixed(1) + ' ' + (p1[1] + (p2[1] - p0[1]) / 6).toFixed(1) + ' ' + (p2[0] - (p3[0] - p1[0]) / 6).toFixed(1) + ' ' + (p2[1] - (p3[1] - p1[1]) / 6).toFixed(1) + ' ' + p2[0].toFixed(1) + ' ' + p2[1].toFixed(1); } return d; }
  var S = want.map(function(k){ var p = pts(ser[k]), d = path(p), gg = sv('g', {'clip-path': 'url(#' + cid + ')'}, g);
    if (k === 'in') sv('path', {d: d + 'L' + (ml + pw) + ' ' + (mt + ph) + 'L' + ml + ' ' + (mt + ph) + 'Z', fill: 'var(--safe)', opacity: .07}, gg);
    sv('path', {d: d, fill: 'none', stroke: COL[k], 'stroke-width': k === 'bl' ? 1.1 : 1.7, 'stroke-dasharray': k === 'bl' ? '3 3' : '', 'stroke-linejoin': 'round'}, gg);
    var hd = sv('circle', {r: 3.4, fill: COL[k]}, g), val = sv('text', {fill: COL[k], 'font-size': 10.5, 'font-family': 'monospace'}, g), nm = sv('text', {fill: COL[k], 'font-size': 12.5, 'font-family': SERIF, 'letter-spacing': '.2em'}, g);
    nm.textContent = NAMES[k]; return {k: k, p: p, hd: hd, val: val, nm: nm}; });
  var notes = (o.notes || []).slice(0, 3).map(function(n){ var x = ml + pw * clamp(+n.x || 0), gg = sv('g', {}, g);
    sv('line', {x1: x, x2: x, y1: mt - 4, y2: mt + ph, stroke: 'var(--ink2)', 'stroke-width': .6}, gg);
    var tx = sv('text', {x: x + 5, y: mt + 8, fill: 'var(--ink)', 'font-size': 12.5, 'font-family': SERIF, 'letter-spacing': '.12em'}, gg); tx.textContent = n.word || '';
    if (x + 5 + (n.word || '').length * 14 > ml + pw){ tx.setAttribute('x', x - 5); tx.setAttribute('text-anchor', 'end'); }
    return {x: +n.x || 0, g: gg}; });
  var ax = sv('text', {x: ml + pw, y: mt + ph + 20, 'text-anchor': 'end', fill: 'var(--ink2)', 'font-size': 10.5, 'letter-spacing': '.2em', 'font-family': 'sans-serif'}, g); ax.textContent = '段落 →';
  function yAt(p, x){ for (var i = 0; i < p.length - 1; i++) if (x <= p[i + 1][0]){ var f = (x - p[i][0]) / (p[i + 1][0] - p[i][0]); return lerp(p[i][1], p[i + 1][1], (1 - M.cos(f * PI)) / 2); } return p[p.length - 1][1]; }
  return {update: function(t){
    var u = eio(seg(t, .04, .9)), x = ml + pw * u; clip.setAttribute('width', (pw * u + 1).toFixed(1));
    var ys = S.map(function(s){ return yAt(s.p, x); }), lab = ys.slice();
    for (var it = 0; it < 3; it++) for (var a = 0; a < lab.length; a++) for (var b = 0; b < lab.length; b++) if (a !== b && M.abs(lab[a] - lab[b]) < 15){ var m = (lab[a] + lab[b]) / 2, up = lab[a] <= lab[b] ? -1 : 1; lab[a] = m + up * 7.5; }
    S.forEach(function(s, i){ var y = ys[i], v = 1 - (y - mt) / ph; s.hd.setAttribute('cx', x.toFixed(1)); s.hd.setAttribute('cy', y.toFixed(1));
      s.val.setAttribute('x', (x + 7).toFixed(1)); s.val.setAttribute('y', (lab[i] + 3.5).toFixed(1)); s.val.textContent = v.toFixed(2); s.val.setAttribute('opacity', u < .97 ? 1 : 0);
      s.nm.setAttribute('x', (ml + pw + 8).toFixed(1)); s.nm.setAttribute('y', (yAt(s.p, ml + pw) + 4).toFixed(1)); s.nm.setAttribute('opacity', es(seg(u, .9, 1))); });
    notes.forEach(function(n){ n.g.setAttribute('opacity', es(seg(u, n.x - .02, n.x + .06)).toFixed(3)); }); }};
};

/* ============================================================== engine */
var PRIMS = Object.keys(P);
function mount(comp){
  comp = comp || {}; var shots = (comp.shots || []).filter(function(s){ return s && P[s.prim]; }).slice(0, 6);
  var root = el('div', '', document.body); root.id = 'vrp';
  var grain = el('div', 'vgrain', root); grain.style.backgroundImage = 'url(' + grainURL() + ')'; grain.style.backgroundSize = '192px';
  el('div', 'vvig', root);
  var slug = el('div', 'vslug', root), sl = el('span', '', slug), tc = el('span', '', slug);
  sl.innerHTML = '<b>FIG.' + ('0' + (comp.fig || 1)).slice(-2) + '</b>\u2002' + String(comp.slug || '').replace(/[<>&]/g, '');
  var seconds = +comp.seconds || 12, inst = [], lastP = 0, W = 0, H = 0, built = false, rt = 0;
  // 真实浏览器里 iframe 刚创建时视口可能是 0×0（Firefox、Chrome 进程隔离的沙箱 iframe），之后长到真实尺寸却不一定补发 resize。
  // 所以：尺寸为 0 时不建（延迟到有尺寸）；ResizeObserver + resize + 每次进度消息都核对尺寸，变了就按新尺寸重建。
  function size(){ var de = document.documentElement;
    return [M.round(innerWidth || de.clientWidth || root.clientWidth || 0), M.round(innerHeight || de.clientHeight || root.clientHeight || 0)]; }
  function stale(){ var s = size(); return s[0] >= 2 && s[1] >= 2 && (!built || M.abs(s[0] - W) > 1 || M.abs(s[1] - H) > 1); }
  function build(){
    var s = size(); if (s[0] < 2 || s[1] < 2) return false;
    inst.forEach(function(sh){ sh.L.remove(); }); inst = []; W = s[0]; H = s[1]; built = true;
    root.setAttribute('data-w', W); root.setAttribute('data-h', H);
    shots.forEach(function(s, i){ var L = el('div', 'vl'); root.insertBefore(L, grain); L.style.zIndex = i + 1;
      var C = {W: W, H: H, mob: W < 560, idx: i, seed: hash((s.prim || '') + i + JSON.stringify(s.params || {})), data: comp.data || {}, chars: comp.chars || []};
      var api = null; try { api = P[s.prim](L, s.params || {}, C); } catch (e){ setTimeout(function(){ throw e; }); }
      // 顶层 svg 带上 viewBox：在下一次重建之前尺寸再变，也是整体缩放而不是错位
      Array.prototype.forEach.call(L.querySelectorAll('svg'), function(g){ if (!g.getAttribute('viewBox') && !(g.parentNode && g.parentNode.closest && g.parentNode.closest('svg'))) g.setAttribute('viewBox', '0 0 ' + W + ' ' + H); });
      inst.push({s: s, L: L, api: api || {update: function(){}}, from: clamp(+s.from || 0), to: clamp(s.to == null ? 1 : +s.to)}); });
    // 转场：相邻镜头的重叠区做“先出后进”的交叉（前一镜在重叠区前 60% 淡出，后一镜在后 60% 淡入），没有重叠就在边界 ±0.02 做短交叉
    var ord = inst.slice().sort(function(a, b){ return a.from - b.from; });
    ord.forEach(function(sh, k){ var prev = ord[k - 1], next = ord[k + 1];
      if (sh.from <= .001) sh.fi = null; else { var a0 = prev ? M.min(sh.from, prev.to) : sh.from, b0 = prev ? M.max(sh.from, prev.to) : sh.from; if (b0 - a0 < .04){ var c = (a0 + b0) / 2; a0 = c - .02; b0 = c + .02; } sh.fi = [a0 + (b0 - a0) * .4, b0]; }
      if (sh.to >= .999) sh.fo = null; else { var a1 = next ? M.min(sh.to, next.from) : sh.to, b1 = next ? M.max(sh.to, next.from) : sh.to; if (b1 - a1 < .04){ var c2 = (a1 + b1) / 2; a1 = c2 - .02; b1 = c2 + .02; } sh.fo = [a1, a1 + (b1 - a1) * .6]; } });
    render(lastP, true);
    return true;
  }
  function env(sh, p){ var vin = sh.fi ? es(seg(p, sh.fi[0], sh.fi[1])) : 1, vout = sh.fo ? 1 - es(seg(p, sh.fo[0], sh.fo[1])) : 1; return vin * vout; }
  function render(p, force){
    lastP = p; var dark = 0;
    inst.forEach(function(sh){ var v = env(sh, p); sh.L.style.opacity = v.toFixed(3); sh.L.style.visibility = v > .001 ? 'visible' : 'hidden';
      if (v > .001 || force){ var t = clamp((p - sh.from) / M.max(.01, sh.to - sh.from)); try { sh.api.update(t); } catch (e){ setTimeout(function(){ throw e; }); } }
      if (sh.api.dark && v > .5) dark = 1; });
    slug.classList.toggle('on-dark', !!dark);
    var q = M.floor(p * 48); grain.style.transform = 'translate(' + ((q * 37) % 64) + 'px,' + ((q * 53) % 64) + 'px)';
    var fr = M.round(p * seconds * 24), s = M.floor(fr / 24); tc.textContent = '00:00:' + ('0' + s).slice(-2) + ':' + ('0' + fr % 24).slice(-2);
  }
  function relayout(){ clearTimeout(rt); if (!stale()) return; if (!built) build(); else rt = setTimeout(function(){ if (stale()) build(); }, 90); }
  build();
  VRH.onProgress(function(p){ if (stale()) build(); render(p); });
  VRH.onTheme(function(){ inst.forEach(function(sh){ if (sh.api.theme) try { sh.api.theme(); } catch (e){} }); render(lastP, true); });
  addEventListener('resize', relayout);
  try { new ResizeObserver(relayout).observe(root); } catch (e){}
  // 兜底轮询：没有 ResizeObserver、也没有 resize 事件的环境里，尺寸未知期间每 150ms 看一次（建成后停）
  (function poll(n){ if (built || n > 200) return; setTimeout(function(){ if (stale()) build(); poll(n + 1); }, 150); })(0);
}
window.VRP = {mount: mount, prims: PRIMS};
})();
