// ===== live.js =====
const FC = new Map(), XC = new Map(), MC_ = new Map(), STATS_CACHE = new Map(), LL = new Map(), IFL = new Map();
let SSE = null, ST0 = 0, SR0 = 0, SB0 = 1000, HC = null;
const cSet = (m, k, v, max = 80) => { if (m.size >= max) m.delete(m.keys().next().value); m.set(k, v); },
  once = (k, fn) => { if (IFL.has(k)) return IFL.get(k); const p = Promise.resolve().then(fn).finally(() => IFL.delete(k)); IFL.set(k, p); return p; },
  rUrl = (fid, p) => `/api/raw?flow=${EU(fid)}&path=${EU(p)}`,
  fSnap = () => { const d = new Date(); return `${d.getDate()} ${MO[d.getMonth()]} ${p2(d.getHours())}:${p2(d.getMinutes())}`; },
  stSt = () => STATS_CACHE.forEach((v) => { v.ts = 0; });
function keepSc(fn) {
  const l = $('left'), s = $('side'), lt = l ? l.scrollTop : 0, st = s ? s.scrollTop : 0, wy = scrollY,
    ae = DOC.activeElement, aid = ae && /^(swq|aq|fq)$/.test(ae.id) ? ae.id : '', ss = aid ? ae.selectionStart : null, se = aid ? ae.selectionEnd : null;
  fn();
  if (l && $('left')) $('left').scrollTop = lt;
  if (s && $('side')) $('side').scrollTop = st;
  if (!CL($('app')).contains('appm') || S.page !== 'board') scrollTo(0, wy);
  const el = aid && $(aid);
  if (el) { el.focus({ preventScroll: true }); if (ss != null) try { el.setSelectionRange(ss, se); } catch (e) {} }
}
function mOuts(pv, nx, sv) {
  if (!isArr(nx)) return [];
  const byId = new Map((pv || []).map((o) => [o.id, o]));
  return nx.map((o) => {
    const p = byId.get(o.id); if (!p) return o;
    const m = { ...o };
    if (sv && (p.size == null || m.size == null || p.size === m.size) && (p.at == null || m.at == null || p.at === m.at)) {
      if (m.body == null && p.body != null) { m.body = p.body; m.truncated = p.truncated; m._loaded = p._loaded; }
      if (!m.messages && p.messages) m.messages = p.messages;
      if (m.detail == null && p.detail != null) m.detail = p.detail;
    }
    return m;
  });
}
function mFlows(nx) {
  const byId = new Map(FL.map((f) => [f.id, f]));
  return (nx || []).map((nf) => {
    const pf = byId.get(nf.id);
    if (pf && pf.updatedAt && pf.updatedAt !== nf.updatedAt) FC.delete(`${pf.id}|${pf.updatedAt}`);
    const cf = nf.updatedAt ? FC.get(`${nf.id}|${nf.updatedAt}`) : null, sv = !!(pf && nf.updatedAt && pf.updatedAt === nf.updatedAt),
      b = { ...nf, ...(cf && cf.updatedAt === nf.updatedAt ? cf : {}) };
    if (pf) b.outputs = mOuts(pf.outputs, b.outputs, sv);
    b.age = flAge(b);
    return b;
  });
}
function dAct() {
  return FL.map((f) => {
    const r = rOf(f), ag = f.agents || [], st = flSt(f), lv = isFL(f),
      tl = ((r && r.links) || []).find((l) => l.kind === 'thread' && l.href) || ag.flatMap((a) => a.links || []).find((l) => l.kind === 'thread' && l.href),
      ch = r ? ag.filter((a) => a.id !== r.id) : ag, settled = f.settled != null ? !!f.settled : (!lv && st === 'done'),
      last = (r && (r.last || r.result)) || (ag.find((a) => a.last || a.result) || {}).last || '';
    return {
      id: (tl && tl.id) || (r && r.id) || f.id, flowId: f.id, rootThreadId: (r && r.id) || null, parentThreadId: (r && r.parent) || null,
      title: f.title || f.id, project: f.project || '', projectRoot: f.projectRoot || '', harness: f.harness || '', state: st,
      isLive: lv, settled, model: (r && r.model) || '', lab: (r && r.lab) || 'other', startedAt: f.startedAt || '', updatedAt: f.updatedAt || '',
      durS: r && r.durS != null ? r.durS : f.durS, activeChildren: ch.filter((a) => isLS(a.state)).length, totalChildren: ch.length,
      last, href: (tl && tl.href) || '', links: (r && r.links) || [],
    };
  }).filter((t) => t.isLive || !t.settled).sort((a, b) => (b.isLive - a.isLive) || ((Date.parse(b.updatedAt || b.startedAt || '') || 0) - (Date.parse(a.updatedAt || a.startedAt || '') || 0)));
}
async function getJ(u) { const r = await fetch(u, { headers: { Accept: 'application/json' } }); let d = null; try { d = await r.json(); } catch (e) {} return { ok: r.ok, status: r.status, data: d }; }
async function getFl(force = false, init = false) {
  try {
    if (force) { FC.clear(); stSt(); MC_.clear(); }
    const r = await getJ('/api/flows' + (force ? '?force=1' : ''));
    if (!r.ok || !r.data) throw new Error((r.data && r.data.error) || ('HTTP ' + r.status));
    const res = r.data, pId = (flow() && flow().id) || S.fid, mg = mFlows(res.flows || []),
      ac = res.activeCount != null ? +res.activeCount : mg.filter(isFL).length;
    D = { envId: res.envId || null, snapshot: res.snapshot || fSnap(), flows: res.flows || [], activeCount: ac, errors: res.errors || {} };
    FL = mg; S.ld = false; S.le = null;
    if (init) {
      const fi = S.fid ? FL.findIndex((x) => x.id === S.fid) : -1;
      if (fi >= 0) S.fi = fi;
      const vis = visFl();
      if (vis.length && !vis.includes(S.fi)) S.fi = vis[0];
    } else {
      const idx = pId ? FL.findIndex((x) => x.id === pId) : -1;
      S.fi = idx >= 0 ? idx : MX(0, MN(S.fi, FL.length - 1));
    }
    S.fid = flow() ? flow().id : null;
    if (AT_.fallback) { const th = dAct(); AT_ = { ...AT_, threads: th, liveCount: th.filter((t) => t.isLive).length }; }
    keepSc(rAll);
    if (flow() && (S.page === 'board' || (S.page === 'stats' && S.scope === 'flow'))) hydFl(flow(), force);
  } catch (err) { S.ld = false; S.le = (err && err.message) || 'Failed to load flows'; keepSc(rAll); }
}
function getAct(force = false) {
  return once(force ? 'act:1' : 'act', async () => {
    AT_.loading = true;
    if (!AT_.loaded && S.page === 'active') keepSc(rAct);
    try {
      const r = await getJ('/api/active' + (force ? '?force=1' : ''));
      if (r.ok && r.data && isArr(r.data.threads)) {
        const lc = r.data.liveCount != null ? +r.data.liveCount : r.data.threads.filter((t) => t.isLive).length;
        AT_ = { loaded: true, loading: false, err: null, threads: r.data.threads, liveCount: lc, fallback: false };
        if (r.data.snapshot) D.snapshot = r.data.snapshot;
        D.activeCount = lc;
      } else {
        const th = dAct();
        AT_ = { loaded: true, loading: false, err: r.status === 404 ? null : ((r.data && r.data.error) || ('HTTP ' + r.status)), threads: th, liveCount: th.filter((t) => t.isLive).length, fallback: true };
      }
    } catch (err) {
      const th = dAct();
      AT_ = { loaded: true, loading: false, err: (err && err.message) || 'Failed to load active threads', threads: th, liveCount: th.filter((t) => t.isLive).length, fallback: true };
    }
    keepSc(() => { rBar(); if (S.page === 'active') rAct(); });
  });
}
function hydFl(f, force = false) {
  if (!f || !f.id) return;
  const ck = f.updatedAt ? `${f.id}|${f.updatedAt}` : '';
  if (!force && (f._hyd || (ck && FC.has(ck)))) {
    if (ck && FC.has(ck) && !f._hyd) {
      const cf = FC.get(ck), idx = FL.findIndex((x) => x.id === f.id);
      if (idx >= 0) { FL[idx] = { ...FL[idx], ...cf, outputs: mOuts(FL[idx].outputs, cf.outputs, true), _hyd: true }; FL[idx].age = flAge(FL[idx]); }
    }
    return;
  }
  return once('fl:' + (ck || f.id), async () => {
    try {
      const r = await getJ(`/api/flow/${EU(f.id)}`);
      if (!r.ok || !r.data || !r.data.flow) return;
      const full = r.data.flow;
      full.age = flAge(full); full._hyd = true;
      const nck = (full.updatedAt || f.updatedAt) ? `${f.id}|${full.updatedAt || f.updatedAt}` : '';
      if (nck) cSet(FC, nck, full, 60);
      const idx = FL.findIndex((x) => x.id === f.id);
      if (idx < 0) return;
      full.outputs = mOuts(FL[idx].outputs, full.outputs, true);
      FL[idx] = full;
      if (flow() && flow().id === f.id) keepSc(() => {
        if (S.page === 'board') { rHd(); dMap(); rOuts(); rSide(); }
        else if (S.page === 'stats' && S.scope === 'flow') rStats();
      });
    } catch (e) {}
  });
}
function ldOut(f, o) {
  if (!f || !o || o._loaded || o._err || /^(video|audio|cfile|post)$/.test(o.kind)) return;
  return once(`out:${f.id}|${o.id}`, async () => {
    try {
      const r = await getJ(`/api/output/${EU(f.id)}/${EU(o.id)}`);
      if (!r.ok || !r.data || r.data.ok === false) o._err = (r.data && r.data.error) || ('HTTP ' + r.status);
      else { ['body', 'messages', 'detail', 'size', 'path'].forEach((k) => { if (r.data[k] != null) o[k] = r.data[k]; }); o.truncated = !!r.data.truncated; o._loaded = true; }
    } catch (err) { o._err = (err && err.message) || 'Failed to load output'; }
    const cf = flow();
    if (cf && cf.id === f.id && S.page === 'board' && ((S.view === 'output' && S.arg === o.id) || S.view === 'compare')) keepSc(rSide);
  });
}
function getCit(fid, p, ln, ctx) {
  const k = `${fid}|${p}|${ln}|${ctx}`;
  if (XC.has(k)) return Promise.resolve(XC.get(k));
  return once('cit:' + k, async () => {
    let res;
    try { const r = await getJ(`/api/file?flow=${EU(fid)}&path=${EU(p)}&line=${EU(ln)}&ctx=${EU(ctx)}`); res = r.data || { ok: false, error: 'HTTP ' + r.status }; }
    catch (err) { res = { ok: false, error: (err && err.message) || 'Failed to load file' }; }
    cSet(XC, k, res, 200); return res;
  });
}
const fMAt = (ts) => { if (!ts) return ''; const d = new Date(ts); return Number.isNaN(d.getTime()) ? String(ts) : `${p2(d.getHours())}:${p2(d.getMinutes())}`; },
  mHtml = (ms, clip) => D_('msglist', JN(ms, (m) => {
    const r = m.role || m.createdBy || 'message', at = fMAt(m.createdAt || m.at || m.updatedAt), txt = String(m.text || m.content || '');
    return D_('msg', D_('msg-hd', S_('', E(r)) + (at ? S_('', E(at)) : '')) + D_('msg-body md', md(clip && txt.length > clip ? txt.slice(0, clip) + '…' : txt).html));
  }));
async function ldMsg(tid, box) {
  if (!box) return;
  if (!tid) { box.innerHTML = EMP('No thread or conversation linked for this agent.'); return; }
  const show = (ms) => { if (!ms.length) { box.innerHTML = EMP('No thread messages recorded.'); return; } box.innerHTML = mHtml(ms, 600); if (flow()) { cify(box, flow()); mify(box, flow()); } };
  if (MC_.has(tid)) { show(MC_.get(tid)); return; }
  box.innerHTML = NTE('Loading messages…');
  const res = await once('msg:' + tid, async () => {
    try {
      const r = await getJ(`/api/thread/${EU(tid)}/messages?limit=10`);
      if (!r.ok || !r.data || r.data.ok === false) return { err: (r.data && r.data.error) || ('HTTP ' + r.status) };
      const ms = r.data.messages || []; cSet(MC_, tid, ms, 60); return { ms };
    } catch (err) { return { err: (err && err.message) || 'Failed to load messages' }; }
  });
  if (!box.isConnected) return;
  if (res.err) box.innerHTML = EMP(E(res.err)); else show(res.ms);
}
function getSt(sc, fid0, rg0, lab, force = false) {
  const fid = sc === 'flow' ? (fid0 || '') : '', rg = sc === 'all' ? (rg0 || 'all') : 'all', lb = lab || '',
    k = `${sc}|${fid}|${rg}|${lb}`, c = !force && STATS_CACHE.get(k);
  if (c && Date.now() - c.ts < 15000) return Promise.resolve(c.data || null);
  if (lb) { const u = STATS_CACHE.get(`${sc}|${fid}|${rg}|`); if (!u || Date.now() - u.ts >= 15000) getSt(sc, fid, rg, '', false); }
  return once('st:' + k, async () => {
    try {
      const r = await getJ(`/api/stats?scope=${EU(sc)}&flow_id=${EU(fid)}&rng=${EU(rg)}&lab=${EU(lb)}`);
      if (!r.ok || !r.data || !r.data.stats) { cSet(STATS_CACHE, k, { ts: Date.now(), err: (r.data && r.data.error) || ('HTTP ' + r.status) }, 40); return null; }
      const st = r.data.stats;
      cSet(STATS_CACHE, k, { ts: Date.now(), data: st }, 40);
      if (!lb) cSet(LL, `${sc}|${fid}|${rg}`, st.byModel || [], 20);
      return st;
    } catch (e) { cSet(STATS_CACHE, k, { ts: Date.now(), err: (e && e.message) || 'Failed to load stats' }, 40); return null; }
    finally {
      if (S.page === 'stats') {
        const cf = S.scope === 'flow' ? ((flow() && flow().id) || '') : '', cr = S.scope === 'all' ? (S.rng || 'all') : 'all';
        if (S.scope === sc && cf === fid && cr === rg && (S.lab || '') === lb) keepSc(rStats);
      }
    }
  });
}
function cSSE() {
  clearTimeout(SR0); SR0 = 0;
  if (typeof EventSource === 'undefined') { S.conn = 'offline'; return; }
  if (SSE) try { SSE.close(); } catch (e) {}
  const es = (SSE = new EventSource('/events')), setC = (c) => { if (S.conn !== c) { S.conn = c; keepSc(rBar); } };
  es.onopen = () => { SB0 = 1000; setC('live'); };
  A_(es, 'ready', () => { SB0 = 1000; setC('live'); });
  A_(es, 'update', () => { SB0 = 1000; setC('live'); clearTimeout(ST0); ST0 = setTimeout(() => { stSt(); MC_.clear(); getFl(false, false); if (S.page === 'active') getAct(false); }, 200); });
  es.onerror = () => {
    if (es.readyState === 2) {
      if (typeof navigator !== 'undefined' && navigator.onLine === false) { setC('offline'); return; }
      setC('reconnecting'); clearTimeout(SR0); SR0 = setTimeout(cSSE, SB0); SB0 = MN(15000, SB0 * 2);
    } else setC('reconnecting');
  };
}
A_(window, 'online', () => { SB0 = 1000; clearTimeout(SR0); if (!SSE || SSE.readyState === 2) cSSE(); getFl(false, false); if (S.page === 'active') getAct(false); });
A_(window, 'offline', () => { clearTimeout(SR0); S.conn = 'offline'; keepSc(rBar); });
function bLive() {
  rdH(); if (!sW()) rAll(); getFl(false, true); if (S.page === 'active') getAct(false); cSSE();
  if (DOC.fonts && DOC.fonts.ready) DOC.fonts.ready.then(() => { if (flow() && S.page === 'board') dMap(); });
}