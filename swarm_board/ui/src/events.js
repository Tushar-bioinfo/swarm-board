// ===== events.js =====
function copy(text, btn) {
  window.__copied = text;
  const done = () => { if (btn) { const l = btn.dataset.ol || (btn.dataset.ol = btn.textContent); clearTimeout(btn._ct); btn.textContent = 'Copied'; btn._ct = setTimeout(() => (btn.textContent = l), 1200); } };
  try { navigator.clipboard.writeText(text).then(done, () => { fbCopy(text); done(); }); } catch (e) { fbCopy(text); done(); }
}
const fbCopy = (t) => { const a = CE('textarea'); a.value = t; a.style.position = 'absolute'; a.style.left = '-9999px'; DOC.body.append(a); a.select(); try { DOC.execCommand('copy'); } catch (e) {} a.remove(); },
  oChat = (a) => { const f = flow(); if (!f) return null; const r = rOf(f), t = ((a && a.links) || []).concat((r && r.links) || []).find((l) => l.kind === 'thread' && safeUrl(l.href)); if (t) { window.__chat = t.href; location.href = t.href; } return t; };
ON('click', (e) => {
  let t = e.target.closest('button,[data-post],a,tr[data-file],.nd,.pod,.spk,.seal,[data-seat],text[data-for]:not([data-for=""])');
  if (!t) { if (S.uf && e.target.closest('.mapw svg')) { S.uf = null; dMap(); } return; }
  if (t.matches('text')) { t = q1(`.nd[data-agent="${CSS.escape(t.dataset.for)}"]`, t.closest('svg')); if (!t) return; }
  const d = t.dataset, f = flow();
  if (f && t.closest('.mapw') && mClk(t, d, f)) return;
  if (CL(t).contains('cite')) { const fl = FL.find((x) => x.id === d.flow) || f; if (!fl) return; hPop(); go('file', `${normP(fl, d.p)}:${d.a}${d.b !== d.a ? '-' + d.b : ''}`); return; }
  if (CL(t).contains('nd')) { go('agent', d.agent); return; }
  const targetFi = d.flowid ? FL.findIndex((x) => x.id === d.flowid) : null, tfi = targetFi != null && targetFi >= 0 ? targetFi : undefined;
  if (d.flow != null && !CL(t).contains('cite')) { S.sl = false; S.sq = ''; go(pgv(), '', +d.flow); return; }
  if (d.step) { const vis = visFl(); if (!vis.length) return; const p = MX(0, vis.indexOf(S.fi)); go(pgv(), '', vis[(p + +d.step + vis.length) % vis.length]); return; }
  if (d.page) { go(d.page === 'board' ? 'overview' : d.page); return; }
  if (d.atab) { S.at = d.atab; rAct(); return; }
  if (d.inspect) { const fi = FL.findIndex((x) => x.id === d.inspect); if (fi >= 0) { if (!visFl().includes(fi)) S.filt = {}; go('overview', '', fi); } else { S.filt = {}; S.fi = -1; S.fid = d.inspect; go('overview'); getFl(true, false); } return; }
  if (d.scope) { S.scope = d.scope; S.lab = null; rStats(); return; }
  if (d.met || d.rng || d.stab) { Object.assign(S, d.met ? { met: d.met } : d.rng ? { rng: d.rng } : { stab: d.stab }); rStats(); return; }
  if (d.slab) { S.lab = S.lab === d.slab ? null : d.slab; hPop(); rStats(); return; }
  if (d.ssort) { S.sr = d.ssort; rStats(); return; }
  if (d.copy) { copy(d.copy === 'pre' ? t.closest('pre').dataset.raw : d.copy, t); return; }
  if (d.sec) { copy(RD_.secs[d.sec], t); return; }
  if (d.goto) { const el = $(d.goto); if (el) scrTo(el); return; }
  if (d.done) { S.dn[d.done] = 1; store.set('done', S.dn); rAll(); return; }
  if (d.filt) { S.filt[d.filt] = S.filt[d.filt] === (d.filt === 'live' ? true : d.val) ? undefined : (d.filt === 'live' ? true : d.val); if (t.closest('details.lfold')) S.fopen = true; const v = visFl(); if (v.length && !v.includes(S.fi)) { S.fi = v[0]; S.fid = FL[S.fi].id; hydFl(FL[S.fi], false); } rAll(); return; }
  if (d.dim) { S.dm = S.dm === d.dim ? null : d.dim; rHd(); dMap(); return; }
  if (d.seat != null) { S.st = +d.seat; S.sbs = S.al = false; rSide(); return; }
  if (d.post) { go('output', 'post-' + d.post); return; }
  if (d.file) { go('file', `${d.file}:${d.line}`); return; }
  if (d.out) { if (S.pk) { const i = S.pk.indexOf(d.out); i < 0 ? S.pk.length < 3 && S.pk.push(d.out) : S.pk.splice(i, 1); rOuts(); return; } go('output', d.out); return; }
  if (d.agent) { S.er = !!d.errs || t.textContent === 'Read errors'; go('agent', d.agent, tfi); return; }
  if (d.go) { go(d.go, d.arg || '', tfi); return; }
  if (d.act) act(d.act, t);
});
function act(k, t) {
  const A = {
    copy: () => copy(RD_ ? RD_.raw : '', t), find: () => { S.fn = !S.fn; S.fn ? oFind() : cFind(); SA(t, 'aria-pressed', S.fn); },
    aminus: () => { S.size = S.size === 20 ? 18 : 17; store.set('size', S.size); rSide(); }, aplus: () => { S.size = S.size === 17 ? 18 : 20; store.set('size', S.size); rSide(); },
    wrap: () => { S.wr = !S.wr; rSide(); }, raw: () => { S.raw = !S.raw; rSide(); }, outline: () => { S.ol = !S.ol; rSide(); },
    full: () => { S.cv = S.cv === 'side' ? null : 'side'; apSplit(); rSide(); }, close: () => { S.cv = null; go('overview'); }, top: () => scrTo(q1('.crumb', $('side'))),
    fnext: () => sFind(1), fprev: () => sFind(-1), fclose: cFind,
    swl: () => { S.sl = !S.sl; if (!S.sl) S.sq = ''; rBar(); if (S.sl && $('swq')) $('swq').focus(); },
    clrfilt: () => { S.filt = {}; const v = visFl(); if (v.length) { S.fi = v[0]; S.fid = FL[S.fi].id; hydFl(FL[S.fi], false); } rAll(); },
    refresh: () => { getFl(true, false); getAct(true); if (S.page === 'stats') { stSt(); rStats(); } },
    cmppick: () => { S.pk = []; rOuts(); }, cmpx: () => { S.pk = null; rOuts(); }, cmpgo: () => { const p = S.pk; S.pk = null; go('compare', p.join(',')); },
    sbs: () => { S.sbs = !S.sbs; S.al = false; rSide(); }, align: () => { S.al = !S.al; S.sbs = false; rSide(); }, unclamp: () => { S.uc = true; rSide(); },
    details: () => { S.dt = !S.dt; store.set('details', S.dt); const b = q1('[data-act=details]'); if (b) SA(b, 'aria-pressed', S.dt); dMap(); },
    key: () => { S.key = !S.key; rMap(); },
  };
  if (A[k]) A[k]();
}
ON('input', (e) => { const tg = e.target; if (!tg) return; if (tg.id === 'swq') { S.sq = tg.value; rSwl(); } else if (tg.id === 'aq') { S.aq = tg.value; rActL(); } });
ON('toggle', (e) => {
  const tg = e.target, d = tg.dataset && tg.dataset.fold;
  if (d) S[d] = tg.open;
  if (tg.dataset && tg.dataset.msgs != null && tg.open && !tg.dataset.loaded) { tg.dataset.loaded = '1'; ldMsg(tg.dataset.msgs, q1('.msgbox', tg)); }
}, true);
const MQ = '.mapw :is(.nd,.bd,.pod,.spk,.seal,.sr,text[data-for]:not([data-for=""]))',
  pOn = (e) => { if (!e.target.closest) return; const c = e.target.closest('.cite'); let m = e.target.closest(MQ); if (m && m.matches('text')) m = q1(`.nd[data-agent="${CSS.escape(m.dataset.for)}"]`, m.closest('svg')); if (c) cCard(c); else if (m) mCard(m); },
  pOff = (e) => { if (e.target.closest && e.target.closest('.cite,' + MQ)) { hPop(); unhl(); } };
function mClk(t, d, f) {
  const c = CL(t);
  if (c.contains('spk')) { S.er = true; go('agent', d.agent); return true; }
  if (c.contains('pod')) { S.uf = S.uf === d.lead ? null : d.lead; hPop(); dMap(); return true; }
  if (d.seat != null) { S.st = +d.seat; go('compare'); return true; }
  if (!c.contains('nd')) return false;
  const a = agOf(f, d.agent);
  if (f.kind === 'tree' && a && a.role !== 'root' && kids(f, a.id).length && S.uf !== a.id) { S.uf = a.id; hPop(); dMap(); return true; }
  if (f.kind === 'committee') { S.st = MX(0, cDocs(f).findIndex((x) => x.a && x.a.id === d.agent)); go('compare'); return true; }
  return false;
}
['mouseover', 'focusin'].forEach((e) => ON(e, pOn));
['mouseout', 'focusout'].forEach((e) => ON(e, pOff));
ON('keydown', (e) => {
  const tg = e.target, typing = tg.matches && tg.matches('input,textarea,select');
  if (e.key === 'Escape' && S.sl) { S.sl = false; S.sq = ''; rBar(); return; }
  if (e.key === 'Escape' && tg.id === 'aq') { if (S.aq) { S.aq = ''; tg.value = ''; rActL(); } else tg.blur(); return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const f = flow(), k = e.key, ord = f ? (f.agents || []).map((a) => a.id) : [];
  if (tg.classList && CL(tg).contains('nd') && /^Arrow/.test(k) && ord.length) {
    e.preventDefault();
    const i = ord.indexOf(tg.dataset.agent), n = ord[(i + (k === 'ArrowRight' || k === 'ArrowDown' ? 1 : -1) + ord.length) % ord.length], g = q1(`.nd[data-agent="${CSS.escape(n)}"]`);
    if (g) g.focus(); return;
  }
  if (tg.closest && (tg.closest(MQ) || tg.matches('tr[data-file]')) && (k === 'Enter' || k === ' ')) { e.preventDefault(); tg.dispatchEvent(new MouseEvent('click', { bubbles: true })); return; }
  if (tg.id === 'split' && (k === 'ArrowLeft' || k === 'ArrowRight')) { mvSpl(k === 'ArrowLeft' ? -5 : 5); return; }
  if (/^[1-9]$/.test(k)) { const vis = visFl(), ti = vis[+k - 1]; if (ti != null && FL[ti]) go(pgv(), '', ti); return; }
  if (k === 'a') { go(S.page === 'active' ? 'overview' : 'active'); return; }
  if (k === 's') { go(S.page === 'stats' ? 'overview' : 'stats'); return; }
  if (k === '?') { go('keys'); return; }
  if (k === 'Escape' && S.page !== 'board') { go('overview'); return; }
  if (k === '/' && S.page === 'active' && $('aq')) { e.preventDefault(); $('aq').focus(); return; }
  if (S.page !== 'board') return;
  if ((k === 'j' || k === 'k') && ord.length) { const i = ord.indexOf(S.sel), n = ord[(i + (k === 'j' ? 1 : -1) + ord.length) % ord.length]; go('agent', n); const g = q1(`.nd[data-agent="${CSS.escape(n)}"]`); if (g) g.focus({ preventScroll: true }); return; }
  if (k === 'o' && f) { oChat(agOf(f, S.sel)); return; }
  if (k === 'c' && f) { if (f.decision) go('compare'); else act('cmppick'); return; }
  if (k === '/') { if ($('rbody')) { e.preventDefault(); S.fn = true; oFind(); } return; }
  if (k === 'f' && APP.dataset.w === 'wide') { S.cv = S.cv === 'side' ? null : 'side'; apSplit(); rSide(); return; }
  if (k === '[' || k === ']') { mvSpl(k === '[' ? -5 : 5); return; }
  if ((k === 'ArrowLeft' || k === 'ArrowRight') && S.view === 'compare' && f) { const n = cDocs(f, S.arg ? S.arg.split(',') : null).length; if (n) { S.st = (S.st + (k === 'ArrowRight' ? 1 : -1) + n) % n; S.sbs = S.al = false; rSide(); } return; }
  if (k === 'd' && f) { act('details'); return; }
  if (k === 'Escape' && S.uf && !S.fn) { S.uf = null; dMap(); return; }
  if (k === 'Escape') { if (S.fn) cFind(); else if (S.cv) { S.cv = null; apSplit(); rSide(); } else if (S.view !== 'overview') go('overview'); }
});
A_(window, 'hashchange', () => { rdH(); rAll(); if (S.page === 'active') getAct(false); else if (flow()) hydFl(flow(), false); });
A_(window, 'scroll', prog, { passive: true });
A_($('side'), 'scroll', prog, { passive: true });
bLive();