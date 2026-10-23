// VibeReading · theme bootstrap (loaded synchronously in <head> so the page never flashes the wrong theme).
// Paper (light) / Ink (dark). Stored choice wins; otherwise follow the OS. CSP-safe: external file, no inline script.
(function () {
  var t = null;
  try { t = localStorage.getItem('vr:theme'); } catch (e) {}
  if (t !== 'light' && t !== 'dark') t = (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  var d = document.documentElement; d.setAttribute('data-theme', t);
  var m = document.querySelector('meta[name="color-scheme"]'); if (m && !m.hasAttribute('data-fixed')) m.setAttribute('content', t);
})();
