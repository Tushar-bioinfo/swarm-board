// ===== ui.js =====
const APP = $('app'), IS_APP = (window.self === window.top || QS.has('app')) && !QS.has('doc');
if (!IS_APP && window.self !== window.top) CL(DOC.documentElement).add('emb');
let bY = 0, lMW = 0, mRf = 0;
const encH = (s) => EU(String(s)).replace(/%3A/g, ':').replace(/%2F/g, '/');
function rdH() {
  const h = new URLSearchParams(location.hash.slice(1)), fid = h.get('f'), fi = fid ? FL.findIndex((f) => f.id === fid) : -1,
    v = h.get('v') || 'overview', k = v.indexOf(':');
  if (fid) S.fid = fid;
  if (fi >= 0) S.fi = fi;
  S.view = k < 0 ? v : v.slice(0, k); S.arg = k < 0 ? '' : v.slice(k + 1);
  if (S.view === 'agent') S.sel = S.arg;
  S.page = S.view === 'stats' ? 'stats' : S.view === 'active' ? 'active' : 'board';
  if (S.page !== 'board') S.view = 'overview';
}
function go(view, arg, fi) {
  const was = S.view;
  if (fi != null && fi !== S.fi && FL[fi]) {
    S.fi = fi; S.fid = FL[fi].id;
    S.sel = null; S.dm = null; S.pk = null; S.st = 0; S.uf = S.u0 = null; S.er = false; S.uc = false;
  }
  if (APP.dataset.w !== 'wide' && was === 'overview' && view !== 'overview') bY = scrollY;
  S.page = view === 'stats' ? 'stats' : view === 'active' ? 'active' : 'board';
  if (S.page !== 'board') { view = 'overview'; arg = ''; S.sl = false; S.sq = ''; }
  S.view = view; S.arg = arg || '';
  if (view === 'agent') S.sel = arg;
  if (view !== 'output' && view !== 'file') { S.fn = false; S.ol = false; }
  const cf = flow(), pv = S.page === 'board' ? view : S.page, h = (cf ? `#f=${encH(cf.id)}&` : '#') + `v=${pv}${arg ? ':' + encH(arg) : ''}`;
  try { if (location.hash !== h) history.pushState(null, '', h); } catch (e) {}
  rAll();
  if (S.page === 'active') getAct(false);
  else if (cf && (S.page === 'board' || (S.page === 'stats' && S.scope === 'flow'))) hydFl(cf, false);
  if (S.page === 'board' && APP.dataset.w !== 'wide') { if (view === 'overview' && was !== 'overview') scrollTo(0, bY); else if (view !== 'overview') scrollTo(0, rect(APP).top + scrollY); }
}
function crms(f) {
  const p = [['overview', '', 'Overview']];
  if (S.view === 'agent') { const a = agOf(f, S.arg); p.push(['agent', S.arg, a ? trunc(aLb(a), 28) : S.arg]); }
  if (S.view === 'output' || S.view === 'file') { const o = gOut(f, S.arg, S.view), a = o && o.by ? agOf(f, o.by) : null; if (o && o.by) p.push(['agent', o.by, a ? trunc(aLb(a), 24) : o.by]); p.push([S.view, S.arg, o ? o.title : S.arg]); }
  if (['compare', 'feed', 'needs', 'keys'].includes(S.view)) p.push([S.view, S.arg, { compare: 'Compare', feed: 'Feed', needs: 'Needs you', keys: 'Keyboard' }[S.view]]);
  return `<nav class="crumb" aria-label="Breadcrumb">${JN(p, (x, i) => (i === p.length - 1 ? S_('cur', E(x[2])) : `<button data-go="${x[0]}" data-arg="${E(x[1])}">${E(x[2])}</button><span>›</span>`))}</nav>`;
}
function rSide() {
  clearInterval(TT);
  const f = flow(), w = APP.dataset.w, sheet = w !== 'wide' && S.view !== 'overview';
  TG(APP, 'sheet', sheet);
  if (!f && S.view !== 'needs' && S.view !== 'keys') { IH('side', ''); return; }
  let body = '', reader = false;
  const v = S.view;
  if (v === 'agent' && f && agOf(f, S.arg)) body = aHtml(f, agOf(f, S.arg));
  else if ((v === 'output' || v === 'file') && f && gOut(f, S.arg, v)) { body = rHtml(gOut(f, S.arg, v), f); reader = true; }
  else if (v === 'compare' && f) body = cHtml(f, S.arg ? S.arg.split(',') : null);
  else if (v === 'feed' && f) body = fHtml(f);
  else if (v === 'needs') body = KK('Needs you · all flows', 'mt20') + nHtml(allN(), true);
  else if (v === 'keys') body = kHtml2();
  else if (f) { S.view = 'overview'; body = oHtml(f); }
  const back = sheet ? D_('backbar', TB('data-go="overview"', '← Map')) : '';
  $('side').innerHTML = back + crms(f) + D_('fr mt12', body) + (sheet ? D_('endnav', TB('data-go="overview"', '← Back to map')) : '');
  if (reader && f) afRd(f);
  else if (f) { const m = $('side'); cify(m, f); mify(m, f); }
  if (v === 'agent' && S.er) { const e = $('errs'); if (e) e.scrollIntoView({ block: 'nearest' }); }
}
function apSplit() {
  const c = $('cols'), wide = APP.dataset.w === 'wide', cv = wide ? S.cv : null, p = geo().p, sp = $('split');
  c.style.gridTemplateColumns = !wide ? '' : cv === 'map' ? 'minmax(0,1fr) 20px 0' : cv === 'side' ? '0 20px minmax(0,1fr)' : `minmax(0,${p}fr) 20px minmax(0,${100 - p}fr)`;
  TG(c, 'cover-side', cv === 'side'); TG(c, 'cover-map', cv === 'map');
  $('left').inert = cv === 'side'; $('side').inert = cv === 'map';
  SA(sp, 'aria-valuenow', RD(cv === 'side' ? 0 : cv === 'map' ? 100 : p));
  sp.title = cv ? 'Drag or click to restore the split' : 'Drag to resize · [ ] moves 5%';
  APP.style.setProperty('--grip-top', RD(innerHeight / 2 - 22) + 'px');
}
(function () {
  const sp = $('split');
  let drag = false, x0 = 0, moved = false, s0 = 0;
  A_(sp, 'pointerdown', (e) => { drag = true; x0 = e.clientX; moved = false; s0 = S.sp || geo().p; sp.setPointerCapture(e.pointerId); CL(sp).add('on'); CL(DOC.documentElement).add('splitting'); });
  A_(sp, 'pointermove', (e) => {
    if (!drag) return;
    if (!moved && Math.abs(e.clientX - x0) <= 3) return;
    moved = true;
    const r = rect($('cols'));
    if (!r.width) return;
    const x = e.clientX - r.left;
    if (x < 28 || x > r.width - 28) { S.cv = x < 28 ? 'side' : 'map'; S.sp = s0; } else { S.cv = null; S.sp = clp(RD(1000 * x / r.width) / 10, 8, 92); }
    apSplit();
  });
  const up = () => { if (!drag) return; drag = false; if (!moved && S.cv) S.cv = null; apSplit(); CL(sp).remove('on'); CL(DOC.documentElement).remove('splitting'); store.set('split', S.sp); rAll(); };
  A_(sp, 'pointerup', up); A_(sp, 'pointercancel', up);
})();
function mvSpl(d) { if (S.cv) S.cv = null; else S.sp = clp(RD(geo().p + d), 10, 90); store.set('split', S.sp); apSplit(); rSide(); }
function layApp() {
  if (!CL(APP).contains('appm') || S.page !== 'board') return;
  const ah = MN(innerHeight, 1600), top = rect($('cols')).top - rect(APP).top;
  APP.style.setProperty('--app-h', ah + 'px'); APP.style.setProperty('--colh', MX(320, ah - top - 8) + 'px');
}
function syPg() {
  const sp = S.page === 'stats', ac = S.page === 'active';
  TG(APP, 'stp', sp); TG(APP, 'actp', ac);
  if (S.page !== 'board') { clearInterval(TT); CL(APP).remove('sheet'); }
  if (!sp) { clearInterval(ST); const e = $('statsPage'); if (e) { e.hidden = true; e.innerHTML = ''; } }
  if (!ac) { clearInterval(AT); AT = 0; const e = $('activePage'); if (e) { e.hidden = true; e.innerHTML = ''; } }
}
function rAll() {
  syPg(); rBar();
  if (S.page === 'stats') { rStats(); return; }
  if (S.page === 'active') { rAct(); return; }
  rHd(); apSplit(); rMap(); rOuts(); rSide(); RAF(layApp);
}
function sW() {
  const w = (S.aw = APP.clientWidth), m = w >= 1060 ? 'wide' : w >= 680 ? 'medium' : 'narrow';
  if (APP.dataset.w !== m) { APP.dataset.w = m; if (m !== 'wide') S.cv = null; rAll(); return true; }
  return false;
}
new ResizeObserver(() => { if (!sW() && S.page === 'board') { apSplit(); layApp(); } }).observe(APP);
new ResizeObserver((es) => { if (S.page !== 'board') return; const w = FLR(es[0].contentRect.width); if (w < 60 || w === lMW) return; S.mw = w; cancelAnimationFrame(mRf); mRf = RAF(dMap); }).observe($('mapfr'));
if (IS_APP) {
  CL(APP).add('appm');
  let hist = [];
  A_(window, 'resize', () => {
    const now = performance.now();
    hist = hist.filter((x) => now - x.t < 1000); hist.push({ t: now, h: innerHeight });
    if (hist.length >= 3 && Math.abs(hist[hist.length - 1].h - hist[0].h) > 40 && hist.filter((x, i) => i && Math.abs(x.h - hist[i - 1].h) > 40).length >= 2) CL(APP).remove('appm');
    layApp();
  });
}