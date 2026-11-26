// ===== stats.js =====
const pgv = () => (S.page === 'stats' ? 'stats' : 'overview'),
  aCalls = (a) => ((a && a.trace) || []).reduce((s, y) => s + (typeof y[1] === 'number' ? y[1] : 0), 0),
  pct = (v) => (v == null ? '—' : Number(v).toFixed(1) + '%'), cnt = (v) => (v > 0 ? RD(v).toLocaleString() : '—'),
  STS = ['running', 'done', 'stopped', 'interrupted'],
  stOf = (s) => ({ killed: 'stopped', error: 'stopped', waiting: 'running', stale: 'done', queued: 'done' }[s] || s),
  money = (v, e) => (e ? '~' : '') + '$' + Number(v || 0).toFixed(2).replace(/\B(?=(\d{3})+\.)/g, ','),
  dOf = (ts) => new Date(ts * 1000), dayL = (ts) => { const d = dOf(ts); return MO[d.getUTCMonth()] + ' ' + d.getUTCDate(); },
  hmL = (ts) => { const d = dOf(ts); return p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()); },
  Z = () => ({ cost: 0, tok: 0, in: 0, cache: 0, cr: 0, out: 0, dur: 0, sav: 0, calls: 0, n: 0, r: 0, st: {} });
function addTo(g, v) { ['cost', 'tok', 'in', 'cache', 'cr', 'out', 'dur', 'sav', 'calls'].forEach((k) => (g[k] += v[k] || 0)); for (const s in v.st || {}) g.st[s] = (g.st[s] || 0) + v.st[s]; return g; }
function bOf(w, lo, hi) {
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
function lMod(models, buckets) {
  const L = {};
  (models || []).forEach((m) => {
    const lb = m.lab || 'other', g = (L[lb] = L[lb] || Z());
    addTo(g, { cost: m.costUsd || 0, in: m.in || 0, cache: m.cache || 0, cr: m.cacheRead != null ? m.cacheRead : (m.cache || 0), out: m.out || 0, tok: (m.in || 0) + (m.cache || 0) + (m.out || 0), dur: m.durS || 0, sav: m.cacheSav || 0, calls: m.toolCalls || 0 });
    g.n += m.agents || 0; g.r += m.rated || 0;
  });
  (buckets || []).forEach((b) => { for (const lb in b.labs) { const g = (L[lb] = L[lb] || Z()); for (const s in b.labs[lb].st || {}) g.st[s] = (g.st[s] || 0) + b.labs[lb].st[s]; } });
  return L;
}
function sMod(st, f) {
  const all = S.scope === 'all', lab = S.lab, fid = all ? '' : ((f && f.id) || ''), rg = all ? (S.rng || 'all') : 'all',
    V = { all, f, now: st.now || FLR(Date.now() / 1000), st }, w = st.buckets;
  let B = [];
  if (w && w.t0 > 0 && w.step > 0 && w.b && w.b.length) {
    const maxT = MX(...w.b.map((r) => r.t)); let lo = 0, hi = maxT;
    if (all && rg !== 'all') { const span = rg === '24h' ? 86400 : 7 * 86400; lo = MX(0, FLR((V.now - span - w.t0) / w.step)); hi = MX(lo, FLR((V.now - w.t0) / w.step), maxT); }
    B = bOf(w, lo, hi);
  }
  const L = lMod(st.byModel, B), allModels = LL.get(`${S.scope}|${fid}|${rg}`), allLabs = allModels ? lMod(allModels, null) : L;
  if (lab && !allLabs[lab] && L[lab]) allLabs[lab] = L[lab];
  const T = Z();
  for (const k in L) if (!lab || k === lab) addTo(T, L[k]);
  if (st.costUsd != null) T.cost = st.costUsd;
  if (st.cacheSavingsUsd != null) T.sav = st.cacheSavingsUsd;
  if (st.toolCalls != null) T.calls = st.toolCalls;
  T.hit = st.cacheHitPct != null ? st.cacheHitPct : (T.in + T.cache > 0 ? 100 * T.cr / (T.in + T.cache) : null);
  if (!all && f && !OK(T.st).length) (f.agents || []).forEach((a) => { if (!lab || a.lab === lab) { const s = stOf(a.state); T.st[s] = (T.st[s] || 0) + (a.durS || 0); } });
  const MD = {};
  (st.byModel || []).forEach((m) => {
    if (lab && m.lab !== lab) return;
    const mi = m.in || 0, mc = m.cache || 0, mcr = m.cacheRead != null ? m.cacheRead : mc;
    MD[m.model] = { m: m.model, lab: m.lab, n: m.agents || 0, dur: m.durS || 0, in: mi, cache: mc, cr: mcr, out: m.out || 0, tok: mi + mc + (m.out || 0), cost: m.costUsd || 0, sav: m.cacheSav || 0, calls: m.toolCalls || 0, hit: m.cacheHitPct != null ? m.cacheHitPct : (mi + mc > 0 ? 100 * mcr / (mi + mc) : null), r: m.rated || 0, e: m.costEstimated !== false };
  });
  const typesCost = st.costByType || { cached: 0, uncached: 0, output: 0 },
    agents = (st.topAgents || []).map((a) => {
      const flId = a[12] || fid, agId = a[13] || '', ag = (f && flId === f.id ? agOf(f, agId) : null) || (!all && f ? agOf(f, agId) : null);
      return { a: ag, id: a[0] || agId, flow: a[1] || '', model: a[2], lab: a[3] || 'other', state: a[4] || 'done', flowId: flId, agentId: agId,
        g: { dur: a[5] || 0, in: a[6] || 0, cache: a[7] || 0, out: a[8] || 0, tok: (a[6] || 0) + (a[7] || 0) + (a[8] || 0), cost: a[9] || 0, r: a[9] != null ? 1 : 0, x: a[14] != null ? a[14] : (ag ? aCalls(ag) : 0) } };
    }),
    key = S.met, est = (st.byModel || []).length ? st.byModel.some((m) => m.costEstimated !== false) : true;
  V.est = est ? 1 : 0;
  V.sub = all ? `${st.flowsCount} flows · ${st.agentsCount} agents · ${st.ratedAgents} rated${st.dupRows ? ` · ${st.dupRows} deduped` : ''}${est ? ' · API estimate' : ''}`
    : `${st.agentsCount} agent${st.agentsCount === 1 ? '' : 's'} · ${st.ratedAgents} rated${st.ratedAgents && est ? ' · API estimate' : ''}`;
  V.labs = allLabs; V.order = OK(allLabs).sort((p, q) => allLabs[q][key] - allLabs[p][key] || allLabs[q].tok - allLabs[p].tok);
  V.t0 = w && w.t0 > 0 ? w.t0 : (B[0] ? B[0].ts : 0);
  Object.assign(V, { B, step: w && w.step ? w.step : 3600, T, key, models: Object.values(MD).sort((p, q) => q[key] - p[key] || q.tok - p.tok), typesCost, agents });
  return (SV = V);
}
const fM = (v, k, e) => (k === 'cost' ? money(v, e) : k === 'tok' ? fTok(v) : fDur(v) || '0s'),
  MET = { cost: 'cost', tok: 'tokens', dur: 'time' };
let ST = 0, SRO = null, cW = 0;
function rStats() {
  const on = S.page === 'stats', el = $('statsPage');
  TG(APP, 'stp', on); el.hidden = !on; clearInterval(ST);
  if (!on) return void (el.innerHTML = '');
  const f = flow(), b = (n, v, l) => `<button class="s" data-${n}="${v}"${ap(S[n] === v)}>${l}</button>`,
    fid = S.scope === 'flow' ? (f && f.id) || '' : '', rg = S.scope === 'all' ? (S.rng || 'all') : 'all', lb = S.lab || '',
    shell = (msg) => D_('sctl', `<nav class="sbc" aria-label="Stats scope">${S_('ink', 'Stats')}${S_('sl', '/')}${S_('sg', b('scope', 'flow', 'This flow') + b('scope', 'all', 'All flows'))}</nav>${D_('sgs', TB('data-page="board"', '← Board'))}`) + NTE(msg);
  if (S.scope === 'flow' && !f) { el.innerHTML = shell(S.ld ? 'Loading flows…' : 'No flows available yet.'); return; }
  const ckey = `${S.scope}|${fid}|${rg}|${lb}`, cached = STATS_CACHE.get(ckey);
  if (!cached || Date.now() - cached.ts >= 15000) getSt(S.scope, fid, rg, lb, false);
  if (!cached || (!cached.data && Date.now() - cached.ts >= 15000)) { el.innerHTML = shell('Loading stats…'); return; }
  if (cached.err || !cached.data) { el.innerHTML = shell(`${E(cached.err || 'Could not load stats.')} ${TB('data-act="refresh"', '↻ Retry')}`); return; }
  const V = sMod(cached.data, f), { T, key: k, all } = V, LB_ = S.lab ? ' · ' + E(labN(S.lab)) : '';
  if (S.stab === 'flow' && all) S.stab = 'model';
  const sum = Object.values(V.labs).reduce((s, x) => s + x[k], 0), lr = JN(V.order, (l) => {
    const g = V.labs[l], pc = sum ? (100 * g[k] / sum) : 0, other = k === 'tok' ? (g.cost ? money(g.cost, V.est) : 'unrated') : fTok(g.tok) + ' tokens';
    return `<button class="slab" data-slab="${l}"${ap(S.lab === l)}>${S_('l1', `<i style="background:${labC(l)}"></i>${emb(l, 'done', 16)}<b>${E(labN(l))}</b>${g.n ? S_('meta', `${g.n} agent${g.n === 1 ? '' : 's'}`) : ''}${S_('v', fM(g[k], k, V.est))}`)}${S_('l2', `${pc > 0 && pc < 0.1 ? '<0.1' : pc.toFixed(1)}% of ${MET[k]} · ${other}`)}</button>`;
  }),
    tot = (l, v) => D_('', S_('meta', l) + `<b>${v}</b>`),
    hero = k === 'cost' ? (T.cost > 0 ? money(T.cost, V.est) : '—') : fM(T[k], k),
    ttl = { cost: all ? 'Hourly cost' : 'Flow cost', tok: 'Processed tokens', dur: 'Agent time' }[k],
    rngTxt = V.t0 > 0 ? `${dayL(V.t0)} → ${dayL(V.now)}` : 'all recorded runs';
  el.innerHTML = D_('sctl', `<nav class="sbc" aria-label="Stats scope">${S_('ink', 'Stats')}${S_('sl', '/')}${S_('sg', b('scope', 'flow', 'This flow') + b('scope', 'all', 'All flows'))}${S_('sl', '/')}${S.lab ? `<button class="s" data-slab="${S.lab}" aria-pressed="true">${E(labN(S.lab))} ×</button>` : S_('ink', 'All labs')}</nav>${D_('sgs', (all ? S_('sg', b('rng', '24h', 'Past 24h') + b('rng', '7d', '7 days') + b('rng', 'all', 'All')) : '') + S_('sg', b('met', 'cost', 'Cost') + b('met', 'tok', 'Tokens') + b('met', 'dur', 'Time')) + TB('data-page="board"', '← Board'))}`) +
    D_('ssub meta', E(all ? `All flows · ${rngTxt}` : `${f.title} · ${f.project || HN[f.harness] || f.harness} · ${HN[f.harness] || f.harness}`)) +
    D_('shero', D_('sleft', D_('hnum', hero) + D_('meta hsub', E(V.sub)) + D_('slabs', lr || '<p class="meta">No lab telemetry in this range.</p>')) + D_('sright', D_('ctl', ttl + LB_) + '<div class="schart" id="schart"></div>')) +
    D_('stot', D_('sh', 'Totals' + LB_) + D_('tg', tot('Processed tokens', fTok(T.tok)) + tot('Cached input', fTok(T.cache)) + tot('Uncached input', fTok(T.in)) + tot('Output', fTok(T.out)) + tot('Cache hit', pct(T.hit)) + tot('Tool calls', cnt(T.calls)))) +
    D_('sbars', tBar(V) + mBar(V)) +
    D_('sdd', `<div class="seats" role="tablist">${b('stab', 'model', 'By model')}${b('stab', 'agent', 'By agent')}${all ? '' : b('stab', 'flow', 'Flow details')}</div>${drl(V)}`);
  dCh();
  if (!SRO) { SRO = new ResizeObserver(() => { const c = $('schart'); if (c && c.clientWidth !== cW) RAF(dCh); }); SRO.observe(el); }
  if (RM() || !q1('[data-tick]', el)) return;
  const t0 = Date.now();
  ST = setInterval(() => qa('[data-tick]', el).forEach((e) => (e.textContent = fDur(+e.dataset.tick + (Date.now() - t0) / 1000))), 1000);
}