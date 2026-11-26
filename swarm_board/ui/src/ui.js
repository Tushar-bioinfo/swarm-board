// ===== ui.js: router + hash, layout modes, split drag =====
const APP = $('app');
const IS_APP = (window.self === window.top || QS.has('app')) && !QS.has('doc');
if (!IS_APP && window.self !== window.top) document.documentElement.classList.add('emb');
let boardY = 0, lastMapW = 0;
const encH = (s) => EU(String(s)).replace(/%3A/g, ':').replace(/%2F/g, '/');
function readHash() {
  const h = new URLSearchParams(location.hash.slice(1)), fid = h.get('f');
  const fi = fid ? FL.findIndex((f) => f.id === fid) : -1;
  const v = h.get('v') || 'overview', k = v.indexOf(':');
  if (fid) S.flowId = fid;
  if (fi >= 0) S.fi = fi;
  S.view = k < 0 ? v : v.slice(0, k); S.arg = k < 0 ? '' : v.slice(k + 1);
  if (S.view === 'agent') S.sel = S.arg;
  S.page = S.view === 'stats' ? 'stats' : 'board';
  if (S.page === 'stats') S.view = 'overview';
}
function go(view, arg, fi) {
  const was = S.view;
  if (fi != null && fi !== S.fi && FL[fi]) {
    S.fi = fi; S.flowId = FL[fi].id;
    S.sel = null; S.dim = null; S.pick = null; S.seat = 0; S.unfold = S.unf0 = null;
    hydrateFlow(FL[fi], false);
  }
  if (APP.dataset.w !== 'wide' && was === 'overview' && view !== 'overview') boardY = scrollY;
  S.page = view === 'stats' ? 'stats' : 'board';
  if (S.page === 'stats') { view = 'overview'; arg = ''; }
  S.view = view; S.arg = arg || '';
  if (view === 'agent') S.sel = arg;
  if (view !== 'output' && view !== 'file') { S.find = false; S.outline = false; }
  const cf = flow();
  const h = (cf ? `#f=${encH(cf.id)}&` : '#') + `v=${S.page === 'stats' ? 'stats' : view}${arg ? ':' + encH(arg) : ''}`;
  try { if (location.hash !== h) history.pushState(null, '', h); } catch (e) { /* sandboxed */ }
  renderAll();
  if (APP.dataset.w !== 'wide') { if (view === 'overview' && was !== 'overview') scrollTo(0, boardY); else if (view !== 'overview') scrollTo(0, APP.getBoundingClientRect().top + scrollY); }
}
function crumbs(f) {
  const p = [['overview', '', 'Overview']];
  if (S.view === 'agent') { const a = agentOf(f, S.arg); p.push(['agent', S.arg, a ? trunc(aLabel(a), 28) : S.arg]); }
  if (S.view === 'output' || S.view === 'file') { const o = getOut(f, S.arg, S.view), a = o && o.by ? agentOf(f, o.by) : null; if (o && o.by) p.push(['agent', o.by, a ? trunc(aLabel(a), 24) : o.by]); p.push([S.view, S.arg, o ? o.title : S.arg]); }
  if (['compare', 'feed', 'needs', 'keys'].includes(S.view)) p.push([S.view, S.arg, { compare: 'Compare', feed: 'Feed', needs: 'Needs you', keys: 'Keyboard' }[S.view]]);
  return `<nav class="crumb" aria-label="Breadcrumb">${p.map((x, i) => i === p.length - 1 ? `<span class="cur">${esc(x[2])}</span>` : `<button data-go="${x[0]}" data-arg="${esc(x[1])}">${esc(x[2])}</button><span>›</span>`).join('')}</nav>`;
}
function renderSide() {
  clearInterval(TT);
  const f = flow(), w = APP.dataset.w, sheet = w !== 'wide' && S.view !== 'overview';
  APP.classList.toggle('sheet', sheet);
  if (!f && S.view !== 'needs' && S.view !== 'keys') { $('side').innerHTML = ''; return; }
  let body = '', reader = false;
  const v = S.view;
  if (v === 'agent' && f && agentOf(f, S.arg)) body = agentHTML(f, agentOf(f, S.arg));
  else if ((v === 'output' || v === 'file') && f && getOut(f, S.arg, v)) { body = readerHTML(getOut(f, S.arg, v), f); reader = true; }
  else if (v === 'compare' && f) { body = compareHTML(f, S.arg ? S.arg.split(',') : null); }
  else if (v === 'feed' && f) body = feedHTML(f);
  else if (v === 'needs') body = `<div class="kick mt20">Needs you · all flows</div>` + needsHTML(allNeeds(), true);
  else if (v === 'keys') body = keysHTML();
  else if (f) { S.view = 'overview'; body = overviewHTML(f); }
  const back = sheet ? `<div class="backbar"><button class="tbtn" data-go="overview">← Map</button></div>` : '';
  $('side').innerHTML = back + crumbs(f) + `<div class="fr mt12">${body}</div>` + (sheet ? `<div class="endnav"><button class="tbtn" data-go="overview">← Back to map</button></div>` : '');
  if (reader && f) afterReader(f);
  else if (f) { const m = $('side'); citeify(m, f); mentionify(m, f); }
  if (v === 'agent' && S.errs) { const e = $('errs'); if (e) e.scrollIntoView({ block: 'nearest' }); }
}
function applySplit() {
  const c = $('cols'), wide = APP.dataset.w === 'wide', cv = wide ? S.cover : null;
  const p = geo().p;
  c.style.gridTemplateColumns = !wide ? '' : cv === 'map' ? 'minmax(0,1fr) 20px 0' : cv === 'side' ? '0 20px minmax(0,1fr)' : `minmax(0,${p}fr) 20px minmax(0,${100 - p}fr)`;
  c.classList.toggle('cover-side', cv === 'side');
  c.classList.toggle('cover-map', cv === 'map');
  $('left').inert = cv === 'side';
  $('side').inert = cv === 'map';
  const sp = $('split');
  sp.setAttribute('aria-valuenow', Math.round(cv === 'side' ? 0 : cv === 'map' ? 100 : p));
  sp.title = cv ? 'Drag or click to restore the split' : 'Drag to resize · [ ] moves 5%';
  APP.style.setProperty('--grip-top', Math.round(innerHeight / 2 - 22) + 'px');
}
(function () {
  const sp = $('split');
  let drag = false, x0 = 0, moved = false, s0 = 0;
  sp.addEventListener('pointerdown', (e) => { drag = true; x0 = e.clientX; moved = false; s0 = S.split || geo().p; sp.setPointerCapture(e.pointerId); sp.classList.add('on'); document.documentElement.classList.add('splitting'); });
  sp.addEventListener('pointermove', (e) => {
    if (!drag) return;
    if (!moved && Math.abs(e.clientX - x0) <= 3) return;
    moved = true;
    const r = $('cols').getBoundingClientRect(), x = e.clientX - r.left;
    if (x < 28 || x > r.width - 28) { S.cover = x < 28 ? 'side' : 'map'; S.split = s0; } else { S.cover = null; S.split = clamp(Math.round(1000 * x / r.width) / 10, 8, 92); }
    applySplit();
  });
  const up = () => { if (!drag) return; drag = false; if (!moved && S.cover) S.cover = null; applySplit(); sp.classList.remove('on'); document.documentElement.classList.remove('splitting'); store.set('split', S.split); renderAll(); };
  sp.addEventListener('pointerup', up); sp.addEventListener('pointercancel', up);
})();
function moveSplit(d) { if (S.cover) { S.cover = null; } else S.split = clamp(Math.round(geo().p + d), 10, 90); store.set('split', S.split); applySplit(); renderSide(); }
function layoutApp() {
  if (!APP.classList.contains('appm')) return;
  const ah = Math.min(innerHeight, 1600), top = $('cols').getBoundingClientRect().top - APP.getBoundingClientRect().top;
  APP.style.setProperty('--app-h', ah + 'px');
  APP.style.setProperty('--colh', Math.max(320, ah - top - 8) + 'px');
}
function renderAll() {
  renderBar();
  if (S.page === 'stats') { clearInterval(TT); renderStats(); return; }
  renderHead(); applySplit(); renderMapFrame(); renderOuts(); renderSide(); renderStats(); requestAnimationFrame(layoutApp);
}
function setW() {
  const w = (S.appW = APP.clientWidth), m = w >= 1060 ? 'wide' : w >= 680 ? 'medium' : 'narrow';
  if (APP.dataset.w !== m) { APP.dataset.w = m; if (m !== 'wide') S.cover = null; renderAll(); return true; }
  return false;
}
new ResizeObserver(() => { if (!setW()) { applySplit(); layoutApp(); } }).observe(APP);
let mapRaf = 0;
new ResizeObserver((es) => { const w = Math.floor(es[0].contentRect.width); if (w < 60 || w === lastMapW) return; S.mapW = w; cancelAnimationFrame(mapRaf); mapRaf = requestAnimationFrame(() => drawMap()); }).observe($('mapfr'));
if (IS_APP) {
  APP.classList.add('appm');
  let hist = [];
  addEventListener('resize', () => {
    const now = performance.now();
    hist = hist.filter((x) => now - x.t < 1000);
    hist.push({ t: now, h: innerHeight });
    if (hist.length >= 3 && Math.abs(hist[hist.length - 1].h - hist[0].h) > 40 && hist.filter((x, i) => i && Math.abs(x.h - hist[i - 1].h) > 40).length >= 2) APP.classList.remove('appm');
    layoutApp();
  });
}
