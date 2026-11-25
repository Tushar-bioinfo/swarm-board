// ===== stats.js: Live Stats page powered by GET /api/stats =====
const pgv = () => (S.page === 'stats' ? 'stats' : 'overview');
const aCalls = (a) => ((a && a.trace) || []).reduce((s, y) => s + y[1], 0);
const STS = ['running', 'done', 'stopped', 'interrupted'], MON = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ');
const stOf = (s) => ({ killed: 'stopped', error: 'stopped', waiting: 'running', stale: 'done', queued: 'done' }[s] || s);
const money = (v, e) => (e ? '~' : '') + '$' + Number(v || 0).toFixed(2).replace(/\B(?=(\d{3})+\.)/g, ',');
const p2 = (n) => String(n).padStart(2, '0');
const dOf = (ts) => new Date(ts * 1000), dayL = (ts) => { const d = dOf(ts); return MON[d.getUTCMonth()] + ' ' + d.getUTCDate(); };
const hmL = (ts) => { const d = dOf(ts); return p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()); };
const Z = () => ({ cost: 0, tok: 0, in: 0, cache: 0, out: 0, dur: 0, sav: 0, n: 0, r: 0, st: {} });
function addTo(g, v) { ['cost', 'tok', 'in', 'cache', 'out', 'dur', 'sav'].forEach((k) => (g[k] += v[k] || 0)); for (const s in v.st || {}) g.st[s] = (g.st[s] || 0) + v.st[s]; return g; }
function bucketsOf(w, lo, hi) {
  if (!w || !w.b || !w.b.length || !w.step || hi < lo) return [];
  if (hi - lo > 2000) lo = hi - 2000;
  const M = new Map(w.b.map((r) => [r.t, r.l])), out = [], states = w.states || STS;
  for (let i = lo; i <= hi; i++) {
    const ts = w.t0 + i * w.step, l = M.get(i) || {}, labs = {};
    for (const k in l) { const a = l[k], st = {}; states.forEach((s, j) => (st[s] = a[5 + j] || 0)); labs[k] = { cost: a[0] || 0, in: a[1] || 0, cache: a[2] || 0, out: a[3] || 0, sav: a[4] || 0, tok: (a[1] || 0) + (a[2] || 0) + (a[3] || 0), dur: a.slice(5).reduce((p, q) => p + (q || 0), 0), st }; }
    out.push({ ts, label: dayL(ts) + ' ' + hmL(ts), day: dayL(ts), labs });
  }
  return out;
}
let SV = null;
function labsFromModels(models, buckets) {
  const L = {};
  (models || []).forEach((m) => {
    const lb = m.lab || 'other', g = (L[lb] = L[lb] || Z());
    addTo(g, { cost: m.costUsd || 0, in: m.in || 0, cache: m.cache || 0, out: m.out || 0, tok: (m.in || 0) + (m.cache || 0) + (m.out || 0), dur: m.durS || 0, sav: m.cacheSav || 0 });
    g.n += m.agents || 0; g.r += m.rated || 0;
  });
  (buckets || []).forEach((b) => { for (const lb in b.labs) { const g = (L[lb] = L[lb] || Z()); for (const s in b.labs[lb].st || {}) g.st[s] = (g.st[s] || 0) + b.labs[lb].st[s]; } });
  return L;
}
function statsModel(st, f) {
  const all = S.scope === 'all', lab = S.lab, fid = all ? '' : ((f && f.id) || ''), rg = all ? (S.rng || 'all') : 'all';
  const V = { all, f, now: st.now || Math.floor(Date.now() / 1000), st };
  const w = st.buckets;
  let B = [];
  if (w && w.t0 > 0 && w.step > 0 && w.b && w.b.length) {
    const maxT = Math.max(...w.b.map((r) => r.t));
    let lo = 0, hi = maxT;
    if (all && rg !== 'all') {
      const span = rg === '24h' ? 86400 : 7 * 86400, win0 = V.now - span;
      lo = Math.max(0, Math.floor((win0 - w.t0) / w.step));
      hi = Math.max(lo, Math.floor((V.now - w.t0) / w.step), maxT);
    }
    B = bucketsOf(w, lo, hi);
  }
  const L = labsFromModels(st.byModel, B);
  const allModels = LAST_LABS.get(`${S.scope}|${fid}|${rg}`);
  const allLabs = allModels ? labsFromModels(allModels, null) : L;
  if (lab && !allLabs[lab] && L[lab]) allLabs[lab] = L[lab];
  const T = Z();
  for (const k in L) if (!lab || k === lab) addTo(T, L[k]);
  if (st.costUsd != null) T.cost = st.costUsd;
  if (st.cacheSavingsUsd != null) T.sav = st.cacheSavingsUsd;
  if (!all && f && !Object.keys(T.st).length) (f.agents || []).forEach((a) => { if (!lab || a.lab === lab) { const s = stOf(a.state); T.st[s] = (T.st[s] || 0) + (a.durS || 0); } });
  const MD = {};
  (st.byModel || []).forEach((m) => {
    if (lab && m.lab !== lab) return;
    MD[m.model] = { m: m.model, lab: m.lab, n: m.agents || 0, dur: m.durS || 0, in: m.in || 0, cache: m.cache || 0, out: m.out || 0, tok: (m.in || 0) + (m.cache || 0) + (m.out || 0), cost: m.costUsd || 0, sav: m.cacheSav || 0, r: m.rated || 0, e: m.costEstimated !== false };
  });
  const typesCost = st.costByType || { cached: 0, uncached: 0, output: 0 };
  const agents = (st.topAgents || []).map((a) => {
    const flId = a[12] || fid, agId = a[13] || '';
    const ag = (f && flId === f.id ? agentOf(f, agId) : null) || (!all && f ? agentOf(f, agId) : null);
    return { a: ag, id: a[0] || agId, flow: a[1] || '', model: a[2], lab: a[3] || 'other', state: a[4] || 'done', flowId: flId, agentId: agId,
      g: { dur: a[5] || 0, in: a[6] || 0, cache: a[7] || 0, out: a[8] || 0, tok: (a[6] || 0) + (a[7] || 0) + (a[8] || 0), cost: a[9] || 0, r: a[9] != null ? 1 : 0, x: ag ? aCalls(ag) : 0 } };
  });
  const key = S.met, est = (st.byModel || []).length ? st.byModel.some((m) => m.costEstimated !== false) : true;
  V.est = est ? 1 : 0;
  V.sub = all ? `${st.flowsCount} flows · ${st.agentsCount} agents · ${st.ratedAgents} rated${st.dupRows ? ` · ${st.dupRows} deduped` : ''}${est ? ' · API estimate' : ''}`
    : `${st.agentsCount} agent${st.agentsCount === 1 ? '' : 's'} · ${st.ratedAgents} rated${st.ratedAgents && est ? ' · API estimate' : ''}`;
  V.labs = allLabs;
  V.order = Object.keys(allLabs).sort((p, q) => allLabs[q][key] - allLabs[p][key] || allLabs[q].tok - allLabs[p].tok);
  V.t0 = w && w.t0 > 0 ? w.t0 : (B[0] ? B[0].ts : 0);
  Object.assign(V, { B, step: w && w.step ? w.step : 3600, T, key, models: Object.values(MD).sort((p, q) => q[key] - p[key] || q.tok - p.tok), typesCost, agents });
  return (SV = V);
}
const fmtM = (v, k, e) => (k === 'cost' ? money(v, e) : k === 'tok' ? fmtTok(v) : fmtDur(v) || '0s');
const MET = { cost: 'cost', tok: 'tokens', dur: 'time' };
let ST = 0, SRO = null, SW = 0;
function renderStats() {
  const on = S.page === 'stats', el = $('statsPage');
  APP.classList.toggle('stp', on); el.hidden = !on; clearInterval(ST);
  if (!on) return void (el.innerHTML = '');
  const f = flow(), b = (n, v, l) => `<button class="s" data-${n}="${v}" aria-pressed="${S[n] === v}">${l}</button>`;
  const fid = S.scope === 'flow' ? (f && f.id) || '' : '', rg = S.scope === 'all' ? (S.rng || 'all') : 'all', lb = S.lab || '';
  const shell = (msg) => `<div class="sctl"><nav class="sbc" aria-label="Stats scope"><span class="ink">Stats</span><span class="sl">/</span><span class="sg">${b('scope', 'flow', 'This flow')}${b('scope', 'all', 'All flows')}</span></nav><div class="sgs"><button class="tbtn" data-page="board">← Board</button></div></div>${NTE(msg)}`;
  if (S.scope === 'flow' && !f) { el.innerHTML = shell(S.loading ? 'Loading flows…' : 'No flows available yet.'); return; }
  const ckey = `${S.scope}|${fid}|${rg}|${lb}`, cached = STATS_CACHE.get(ckey);
  if (!cached || Date.now() - cached.ts >= 15000) fetchStats(S.scope, fid, rg, lb, false);
  if (!cached) { el.innerHTML = shell('Loading stats…'); return; }
  const V = statsModel(cached.data, f), { T, key: k, all } = V, LB = S.lab ? ' · ' + esc(labN(S.lab)) : '';
  if (S.stab === 'flow' && all) S.stab = 'model';
  const sum = Object.values(V.labs).reduce((s, x) => s + x[k], 0), lr = V.order.map((l) => {
    const g = V.labs[l], pc = sum ? (100 * g[k] / sum) : 0;
    const other = k === 'tok' ? (g.cost ? money(g.cost, V.est) : 'unrated') : fmtTok(g.tok) + ' tokens';
    return `<button class="slab" data-slab="${l}" aria-pressed="${S.lab === l}"><span class="l1"><i style="background:${labC(l)}"></i>${emb(l, 'done', 16)}<b>${esc(labN(l))}</b>${g.n ? `<span class="meta">${g.n} agent${g.n === 1 ? '' : 's'}</span>` : ''}<span class="v">${fmtM(g[k], k, V.est)}</span></span>
<span class="l2">${pc > 0 && pc < 0.1 ? '<0.1' : pc.toFixed(1)}% of ${MET[k]} · ${other}</span></button>`;
  }).join('');
  const tot = (l, v) => `<div><span class="meta">${l}</span><b>${v}</b></div>`;
  const hero = k === 'cost' ? (T.cost > 0 ? money(T.cost, V.est) : '—') : fmtM(T[k], k);
  const ttl = { cost: all ? 'Hourly cost' : 'Flow cost', tok: 'Processed tokens', dur: 'Agent time' }[k];
  const rngTxt = V.t0 > 0 ? `${dayL(V.t0)} → ${dayL(V.now)}` : 'all recorded runs';
  el.innerHTML = `<div class="sctl"><nav class="sbc" aria-label="Stats scope"><span class="ink">Stats</span><span class="sl">/</span><span class="sg">${b('scope', 'flow', 'This flow')}${b('scope', 'all', 'All flows')}</span><span class="sl">/</span>${S.lab ? `<button class="s" data-slab="${S.lab}" aria-pressed="true">${esc(labN(S.lab))} ×</button>` : '<span class="ink">All labs</span>'}</nav>
<div class="sgs">${all ? `<span class="sg">${b('rng', '24h', 'Past 24h')}${b('rng', '7d', '7 days')}${b('rng', 'all', 'All')}</span>` : ''}<span class="sg">${b('met', 'cost', 'Cost')}${b('met', 'tok', 'Tokens')}${b('met', 'dur', 'Time')}</span><button class="tbtn" data-page="board">← Board</button></div></div>
<div class="ssub meta">${esc(all ? `All flows · ${rngTxt}` : `${f.title} · ${f.project || HARN[f.harness] || f.harness} · ${HARN[f.harness] || f.harness}`)}</div>
<div class="shero"><div class="sleft"><div class="hnum">${hero}</div><div class="meta hsub">${esc(V.sub)}</div><div class="slabs">${lr || '<p class="meta">No lab telemetry in this range.</p>'}</div></div>
<div class="sright"><div class="ctl">${ttl}${LB}</div><div class="schart" id="schart"></div></div></div>
<div class="stot"><div class="sh">Totals${LB}</div><div class="tg">${tot('Processed tokens', fmtTok(T.tok))}${tot('Cached input', fmtTok(T.cache))}${tot('Uncached input', fmtTok(T.in))}${tot('Output', fmtTok(T.out))}${tot('Cache savings', T.sav > 0 ? money(T.sav, 1) : '—')}</div></div>
<div class="sbars">${typeBar(V)}${modelBar(V)}</div>
<div class="sdd"><div class="seats" role="tablist">${b('stab', 'model', 'By model')}${b('stab', 'agent', 'By agent')}${all ? '' : b('stab', 'flow', 'Flow details')}</div>${drill(V)}</div>`;
  drawChart();
  if (!SRO) { SRO = new ResizeObserver(() => { const c = $('schart'); if (c && c.clientWidth !== SW) requestAnimationFrame(drawChart); }); SRO.observe(el); }
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !q1('[data-tick]', el)) return;
  const t0 = Date.now();
  ST = setInterval(() => qa('[data-tick]', el).forEach((e) => (e.textContent = fmtDur(+e.dataset.tick + (Date.now() - t0) / 1000))), 1000);
}
