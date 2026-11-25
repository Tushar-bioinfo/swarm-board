// ===== events.js: copy, clicks, hover cards, keyboard, boot =====
function copy(text, btn) {
  window.__copied = text;
  const done = () => { if (btn) { const l = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => (btn.textContent = l), 1200); } };
  try { navigator.clipboard.writeText(text).then(done, () => { fallbackCopy(text); done(); }); } catch (e) { fallbackCopy(text); done(); }
}
function fallbackCopy(t) { const a = CE('textarea'); a.value = t; a.style.position = 'absolute'; a.style.left = '-9999px'; document.body.append(a); a.select(); try { document.execCommand('copy'); } catch (e) { /* blocked */ } a.remove(); }
function openChat(a) { const f = flow(); if (!f) return null; const r = rootOf(f); const t = ((a && a.links) || []).concat((r && r.links) || []).find((l) => l.kind === 'thread' && l.href); if (t) { window.__chat = t.href; location.href = t.href; } return t; }
ON('click', (e) => {
  let t = e.target.closest('button,[data-post],a,tr[data-file],.nd,.pod,.spk,.seal,[data-seat],text[data-for]:not([data-for=""])');
  if (!t) { if (S.unfold && e.target.closest('.mapw svg')) { S.unfold = null; drawMap(); } return; }
  if (t.matches('text')) { t = q1(`.nd[data-agent="${CSS.escape(t.dataset.for)}"]`, t.closest('svg')); if (!t) return; }
  const d = t.dataset, f = flow();
  if (f && t.closest('.mapw') && mapClick(t, d, f)) return;
  if (t.classList.contains('cite')) { const fl = FL.find((x) => x.id === d.flow) || f; if (!fl) return; const rel = normCitePath(fl, d.p); hidePop(); go('file', `${rel}:${d.a}${d.b !== d.a ? '-' + d.b : ''}`); return; }
  if (t.classList.contains('nd')) { go('agent', d.agent); return; }
  const targetFi = d.flowid ? FL.findIndex((x) => x.id === d.flowid) : null;
  const tfi = targetFi != null && targetFi >= 0 ? targetFi : undefined;
  if (d.flow != null && !t.classList.contains('cite')) { S.swl = false; S.swq = ''; go(pgv(), '', +d.flow); return; }
  if (d.step) { const vis = visibleFlows(); if (!vis.length) return; const p = Math.max(0, vis.indexOf(S.fi)); go(pgv(), '', vis[(p + +d.step + vis.length) % vis.length]); return; }
  if (d.page) { go(d.page === 'stats' ? 'stats' : 'overview'); return; }
  if (d.scope) { S.scope = d.scope; S.lab = null; renderStats(); return; }
  if (d.met || d.rng || d.stab) { Object.assign(S, d.met ? { met: d.met } : d.rng ? { rng: d.rng } : { stab: d.stab }); renderStats(); return; }
  if (d.slab) { S.lab = S.lab === d.slab ? null : d.slab; hidePop(); renderStats(); return; }
  if (d.ssort) { S.ssort = d.ssort; renderStats(); return; }
  if (d.copy) { copy(d.copy === 'pre' ? t.closest('pre').dataset.raw : d.copy, t); return; }
  if (d.sec) { copy(READ.secs[d.sec], t); return; }
  if (d.goto) { const el = document.getElementById(d.goto); if (el) scrollToEl(el); return; }
  if (d.done) { S.done[d.done] = 1; store.set('done', S.done); renderAll(); return; }
  if (d.filt) { S.filt[d.filt] = S.filt[d.filt] === (d.filt === 'live' ? true : d.val) ? undefined : (d.filt === 'live' ? true : d.val); S.fopen = true; const v = visibleFlows(); if (v.length && !v.includes(S.fi)) { S.fi = v[0]; S.flowId = FL[S.fi].id; hydrateFlow(FL[S.fi], false); } renderAll(); return; }
  if (d.dim) { S.dim = S.dim === d.dim ? null : d.dim; renderHead(); drawMap(); return; }
  if (d.seat != null) { S.seat = +d.seat; S.sbs = S.align = false; renderSide(); return; }
  if (d.post) { go('output', 'post-' + d.post); return; }
  if (d.file) { go('file', `${d.file}:${d.line}`); return; }
  if (d.out) { if (S.pick) { const i = S.pick.indexOf(d.out); i < 0 ? S.pick.length < 3 && S.pick.push(d.out) : S.pick.splice(i, 1); renderOuts(); return; } go('output', d.out); return; }
  if (d.agent) { S.errs = !!d.errs || t.textContent === 'Read errors'; go('agent', d.agent, tfi); return; }
  if (d.go) { go(d.go, d.arg || '', tfi); return; }
  if (d.act) act(d.act, t);
});
function act(k, t) {
  const A = {
    copy: () => copy(READ ? READ.raw : '', t), find: () => { S.find = !S.find; S.find ? openFind() : closeFind(); t.setAttribute('aria-pressed', S.find); },
    aminus: () => { S.size = S.size === 20 ? 18 : 17; store.set('size', S.size); renderSide(); }, aplus: () => { S.size = S.size === 17 ? 18 : 20; store.set('size', S.size); renderSide(); },
    wrap: () => { S.wrap = !S.wrap; renderSide(); }, raw: () => { S.raw = !S.raw; renderSide(); }, outline: () => { S.outline = !S.outline; renderSide(); },
    full: () => { S.cover = S.cover === 'side' ? null : 'side'; applySplit(); renderSide(); }, close: () => { S.cover = null; go('overview'); }, top: () => scrollToEl(q1('.crumb', $('side'))),
    fnext: () => stepFind(1), fprev: () => stepFind(-1), fclose: closeFind,
    swl: () => { S.swl = !S.swl; if (!S.swl) S.swq = ''; renderBar(); if (S.swl && $('swq')) $('swq').focus(); },
    clrfilt: () => { S.filt = {}; const v = visibleFlows(); if (v.length) S.fi = v[0]; renderAll(); },
    refresh: () => fetchFlows(true, false),
    cmppick: () => { S.pick = []; renderOuts(); }, cmpx: () => { S.pick = null; renderOuts(); }, cmpgo: () => { const p = S.pick; S.pick = null; go('compare', p.join(',')); },
    sbs: () => { S.sbs = !S.sbs; S.align = false; renderSide(); }, align: () => { S.align = !S.align; S.sbs = false; renderSide(); }, unclamp: () => { S.unclamp = true; renderSide(); },
    details: () => { S.details = !S.details; store.set('details', S.details); const b = q1('[data-act=details]'); if (b) b.setAttribute('aria-pressed', S.details); drawMap(); },
    key: () => { S.key = !S.key; renderMapFrame(); },
  };
  if (A[k]) A[k]();
}
ON('input', (e) => { if (e.target && e.target.id === 'swq') { S.swq = e.target.value; renderSwlList(); } });
ON('toggle', (e) => {
  const tg = e.target, d = tg.dataset && tg.dataset.fold;
  if (d) S[d] = tg.open;
  if (tg.dataset && tg.dataset.msgs != null && tg.open && !tg.dataset.loaded) {
    tg.dataset.loaded = '1';
    loadThreadMsgs(tg.dataset.msgs, q1('.msgbox', tg));
  }
}, true);
const MAPQ = '.mapw :is(.nd,.bd,.pod,.spk,.seal,.sr,text[data-for]:not([data-for=""]))';
const popOn = (e) => { if (!e.target.closest) return; const c = e.target.closest('.cite'); let m = e.target.closest(MAPQ); if (m && m.matches('text')) m = q1(`.nd[data-agent="${CSS.escape(m.dataset.for)}"]`, m.closest('svg')); if (c) citeCard(c); else if (m) mapCard(m); };
const popOff = (e) => { if (e.target.closest && e.target.closest('.cite,' + MAPQ)) { hidePop(); unhl(); } };
function mapClick(t, d, f) {
  const c = t.classList;
  if (c.contains('spk')) { S.errs = true; go('agent', d.agent); return true; }
  if (c.contains('pod')) { S.unfold = S.unfold === d.lead ? null : d.lead; hidePop(); drawMap(); return true; }
  if (d.seat != null) { S.seat = +d.seat; go('compare'); return true; }
  if (!c.contains('nd')) return false;
  const a = agentOf(f, d.agent);
  if (f.kind === 'tree' && a && a.role !== 'root' && kids(f, a.id).length && S.unfold !== a.id) { S.unfold = a.id; hidePop(); drawMap(); return true; }
  if (f.kind === 'committee') { S.seat = Math.max(0, cmpDocs(f).findIndex((x) => x.a && x.a.id === d.agent)); go('compare'); return true; }
  return false;
}
['mouseover', 'focusin'].forEach((e) => ON(e, popOn));
['mouseout', 'focusout'].forEach((e) => ON(e, popOff));
ON('keydown', (e) => {
  const tg = e.target, typing = tg.matches && tg.matches('input,textarea,select');
  if (e.key === 'Escape' && S.swl) { S.swl = false; S.swq = ''; renderBar(); return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const f = flow(), k = e.key, ord = f ? (f.agents || []).map((a) => a.id) : [];
  if (tg.classList && tg.classList.contains('nd') && /^Arrow/.test(k) && ord.length) {
    e.preventDefault();
    const i = ord.indexOf(tg.dataset.agent), n = ord[(i + (k === 'ArrowRight' || k === 'ArrowDown' ? 1 : -1) + ord.length) % ord.length];
    const g = q1(`.nd[data-agent="${CSS.escape(n)}"]`); if (g) g.focus(); return;
  }
  if (tg.closest && tg.closest(MAPQ) && (k === 'Enter' || k === ' ')) { e.preventDefault(); tg.dispatchEvent(new MouseEvent('click', { bubbles: true })); return; }
  if (tg.id === 'split' && (k === 'ArrowLeft' || k === 'ArrowRight')) { moveSplit(k === 'ArrowLeft' ? -5 : 5); return; }
  if (/^[1-9]$/.test(k)) { const vis = visibleFlows(), ti = vis[+k - 1]; if (ti != null && FL[ti]) go(pgv(), '', ti); return; }
  if (k === 's') { go(S.page === 'stats' ? 'overview' : 'stats'); return; }
  if (k === 'Escape' && S.page === 'stats') { go('overview'); return; }
  if ((k === 'j' || k === 'k') && ord.length) { const i = ord.indexOf(S.sel), n = ord[(i + (k === 'j' ? 1 : -1) + ord.length) % ord.length]; go('agent', n); const g = q1(`.nd[data-agent="${CSS.escape(n)}"]`); if (g) g.focus({ preventScroll: true }); return; }
  if (k === 'o' && f) { openChat(agentOf(f, S.sel)); return; }
  if (k === 'c' && f) { if (f.decision) go('compare'); else act('cmppick'); return; }
  if (k === '/') { if ($('rbody')) { e.preventDefault(); S.find = true; openFind(); } return; }
  if (k === 'f' && APP.dataset.w === 'wide') { S.cover = S.cover === 'side' ? null : 'side'; applySplit(); renderSide(); return; }
  if (k === '[' || k === ']') { moveSplit(k === '[' ? -5 : 5); return; }
  if ((k === 'ArrowLeft' || k === 'ArrowRight') && S.view === 'compare' && f) { const n = cmpDocs(f, S.arg ? S.arg.split(',') : null).length; if (n) { S.seat = (S.seat + (k === 'ArrowRight' ? 1 : -1) + n) % n; S.sbs = S.align = false; renderSide(); } return; }
  if (k === 'd' && f) { act('details'); return; }
  if (k === '?') { go('keys'); return; }
  if (k === 'Escape' && S.unfold && !S.find) { S.unfold = null; drawMap(); return; }
  if (k === 'Escape') { if (S.find) closeFind(); else if (S.cover) { S.cover = null; applySplit(); renderSide(); } else if (S.view !== 'overview') go('overview'); }
});
addEventListener('hashchange', () => { readHash(); renderAll(); if (flow()) hydrateFlow(flow(), false); });
addEventListener('scroll', progress, { passive: true });
$('side').addEventListener('scroll', progress, { passive: true });
setW();
bootLive();
