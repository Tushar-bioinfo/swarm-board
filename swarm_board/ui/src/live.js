// ===== live.js: live HTTP fetches, caching, SSE stream, scroll preservation =====
const FLOW_CACHE = new Map(), FILE_CACHE = new Map(), MSG_CACHE = new Map(), STATS_CACHE = new Map(), LAST_LABS = new Map(), INFLIGHT = new Map();
let SSE = null, SSE_TMR = 0, HOVER_CITE = null;

function capSet(map, k, v, max = 80) { if (map.size >= max) map.delete(map.keys().next().value); map.set(k, v); }
function once(k, fn) { if (INFLIGHT.has(k)) return INFLIGHT.get(k); const p = Promise.resolve().then(fn).finally(() => INFLIGHT.delete(k)); INFLIGHT.set(k, p); return p; }
const rawUrl = (fid, path) => `/api/raw?flow=${EU(fid)}&path=${EU(path)}`;
function fmtNowSnap() {
  const d = new Date(), m = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ')[d.getMonth()];
  return `${d.getDate()} ${m} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function preserveScroll(fn) {
  const l = $('left'), s = $('side'), lt = l ? l.scrollTop : 0, st = s ? s.scrollTop : 0, wy = scrollY;
  const ae = document.activeElement, sw = ae && ae.id === 'swq', ss = sw ? ae.selectionStart : null, se = sw ? ae.selectionEnd : null;
  fn();
  if (l && $('left')) $('left').scrollTop = lt;
  if (s && $('side')) $('side').scrollTop = st;
  if (!$('app').classList.contains('appm') || S.page === 'stats') scrollTo(0, wy);
  if (sw && $('swq')) { $('swq').focus(); if (ss != null) try { $('swq').setSelectionRange(ss, se); } catch (e) { /* ignore */ } }
}
function mergeOutputs(prev, next) {
  if (!Array.isArray(next)) return [];
  const byId = new Map((prev || []).map((o) => [o.id, o]));
  return next.map((o) => {
    const p = byId.get(o.id);
    if (!p) return o;
    const m = { ...o };
    if (m.body == null && p.body != null) { m.body = p.body; m.truncated = p.truncated; m._loaded = p._loaded; }
    if (!m.messages && p.messages) m.messages = p.messages;
    if (m.detail == null && p.detail != null) m.detail = p.detail;
    return m;
  });
}
function mergeFlows(next) {
  const byId = new Map(FL.map((f) => [f.id, f]));
  return (next || []).map((nf) => {
    const pf = byId.get(nf.id), cf = FLOW_CACHE.get(`${nf.id}|${nf.updatedAt || ''}`);
    const base = { ...nf, ...(cf || {}) };
    if (pf) base.outputs = mergeOutputs(pf.outputs, base.outputs);
    base.age = flowAge(base);
    return base;
  });
}
async function fetchJson(url) {
  const r = await fetch(url, { headers: { Accept: 'application/json' } });
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  return { ok: r.ok, status: r.status, data };
}
function staleStats() { STATS_CACHE.forEach((v) => { v.ts = 0; }); }
async function fetchFlows(force = false, isInit = false) {
  try {
    if (force) { FLOW_CACHE.clear(); staleStats(); MSG_CACHE.clear(); }
    const r = await fetchJson(force ? '/api/flows?force=1' : '/api/flows');
    if (!r.ok || !r.data) throw new Error((r.data && r.data.error) || ('HTTP ' + r.status));
    const res = r.data, prevId = (flow() && flow().id) || S.flowId;
    D = { envId: res.envId || null, snapshot: res.snapshot || fmtNowSnap(), flows: res.flows || [], errors: res.errors || {} };
    FL = mergeFlows(D.flows); S.loading = false; S.loadErr = null;
    if (isInit) { readHash(); const vis = visibleFlows(); if (vis.length && !vis.includes(S.fi)) S.fi = vis[0]; }
    else { const idx = prevId ? FL.findIndex((x) => x.id === prevId) : -1; S.fi = idx >= 0 ? idx : Math.min(S.fi, Math.max(0, FL.length - 1)); }
    S.flowId = flow() ? flow().id : null;
    preserveScroll(() => renderAll());
    if (flow()) hydrateFlow(flow(), force);
  } catch (err) {
    S.loading = false; S.loadErr = (err && err.message) || 'Failed to load flows';
    preserveScroll(() => renderAll());
  }
}
function hydrateFlow(f, force = false) {
  if (!f || !f.id) return;
  const ck = `${f.id}|${f.updatedAt || ''}`;
  if (!force && (f._hyd || FLOW_CACHE.has(ck))) {
    if (FLOW_CACHE.has(ck) && !f._hyd) {
      const cf = FLOW_CACHE.get(ck), idx = FL.findIndex((x) => x.id === f.id);
      if (idx >= 0) { FL[idx] = { ...FL[idx], ...cf, outputs: mergeOutputs(FL[idx].outputs, cf.outputs), _hyd: true }; FL[idx].age = flowAge(FL[idx]); }
    }
    return;
  }
  return once('fl:' + ck, async () => {
    try {
      const r = await fetchJson(`/api/flow/${EU(f.id)}`);
      if (!r.ok || !r.data || !r.data.flow) return;
      const full = r.data.flow;
      full.age = flowAge(full); full._hyd = true;
      capSet(FLOW_CACHE, ck, full, 60);
      const idx = FL.findIndex((x) => x.id === f.id);
      if (idx < 0) return;
      full.outputs = mergeOutputs(FL[idx].outputs, full.outputs);
      FL[idx] = full;
      if (flow() && flow().id === f.id) preserveScroll(() => {
        renderHead();
        if (S.page === 'board') { drawMap(); renderOuts(); renderSide(); }
        else if (S.page === 'stats' && S.scope === 'flow') renderStats();
      });
    } catch (e) { /* keep compact flow */ }
  });
}
function loadOutput(f, o) {
  if (!f || !o || o._loaded || o._err || /^(video|audio|cfile|post)$/.test(o.kind)) return;
  return once(`out:${f.id}|${o.id}`, async () => {
    try {
      const r = await fetchJson(`/api/output/${EU(f.id)}/${EU(o.id)}`);
      if (!r.ok || !r.data || r.data.ok === false) o._err = (r.data && r.data.error) || ('HTTP ' + r.status);
      else {
        const res = r.data;
        ['body', 'messages', 'detail', 'size', 'path'].forEach((k) => { if (res[k] != null) o[k] = res[k]; });
        o.truncated = !!res.truncated; o._loaded = true;
      }
    } catch (err) { o._err = (err && err.message) || 'Failed to load output'; }
    const cf = flow();
    if (cf && cf.id === f.id && ((S.view === 'output' && S.arg === o.id) || S.view === 'compare')) preserveScroll(() => renderSide());
  });
}
function fetchCitation(flowId, path, line, ctx) {
  const key = `${flowId}|${path}|${line}|${ctx}`;
  if (FILE_CACHE.has(key)) return Promise.resolve(FILE_CACHE.get(key));
  return once('cit:' + key, async () => {
    let res;
    try {
      const r = await fetchJson(`/api/file?flow=${EU(flowId)}&path=${EU(path)}&line=${EU(line)}&ctx=${EU(ctx)}`);
      res = r.data || { ok: false, error: 'HTTP ' + r.status };
    } catch (err) {
      res = { ok: false, error: (err && err.message) || 'Failed to load file' };
    }
    capSet(FILE_CACHE, key, res, 200);
    return res;
  });
}
function fmtMsgAt(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? String(ts) : `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function msgsHTML(msgs, clipLen) {
  return `<div class="msglist">${msgs.map((m) => {
    const role = m.role || m.createdBy || 'message', at = fmtMsgAt(m.createdAt || m.at || m.updatedAt);
    const txt = String(m.text || m.content || ''), body = clipLen && txt.length > clipLen ? txt.slice(0, clipLen) + '…' : txt;
    return `<div class="msg"><div class="msg-hd"><span>${esc(role)}</span>${at ? `<span>${esc(at)}</span>` : ''}</div><div class="msg-body md">${md(body).html}</div></div>`;
  }).join('')}</div>`;
}
async function loadThreadMsgs(tid, box) {
  if (!box) return;
  if (!tid) { box.innerHTML = EMP('No thread or conversation linked for this agent.'); return; }
  const show = (msgs) => {
    if (!msgs.length) { box.innerHTML = EMP('No thread messages recorded.'); return; }
    box.innerHTML = msgsHTML(msgs, 600);
    if (flow()) { citeify(box, flow()); mentionify(box, flow()); }
  };
  if (MSG_CACHE.has(tid)) { show(MSG_CACHE.get(tid)); return; }
  box.innerHTML = NTE('Loading messages…');
  const res = await once('msg:' + tid, async () => {
    try {
      const r = await fetchJson(`/api/thread/${EU(tid)}/messages?limit=10`);
      if (!r.ok || !r.data || r.data.ok === false) return { err: (r.data && r.data.error) || ('HTTP ' + r.status) };
      const msgs = r.data.messages || [];
      capSet(MSG_CACHE, tid, msgs, 60);
      return { msgs };
    } catch (err) { return { err: (err && err.message) || 'Failed to load messages' }; }
  });
  if (!box.isConnected) return;
  if (res.err) box.innerHTML = EMP(esc(res.err));
  else show(res.msgs);
}
function fetchStats(scope, flowId, rng, lab, force = false) {
  const fid = scope === 'flow' ? (flowId || '') : '', rg = scope === 'all' ? (rng || 'all') : 'all', lb = lab || '';
  const key = `${scope}|${fid}|${rg}|${lb}`, c = !force && STATS_CACHE.get(key);
  if (c && Date.now() - c.ts < 15000) return Promise.resolve(c.data);
  if (lb) { const u = STATS_CACHE.get(`${scope}|${fid}|${rg}|`); if (!u || Date.now() - u.ts >= 15000) fetchStats(scope, fid, rg, '', false); }
  return once('st:' + key, async () => {
    try {
      const r = await fetchJson(`/api/stats?scope=${EU(scope)}&flow_id=${EU(fid)}&rng=${EU(rg)}&lab=${EU(lb)}`);
      if (!r.ok || !r.data || !r.data.stats) return null;
      const st = r.data.stats;
      capSet(STATS_CACHE, key, { ts: Date.now(), data: st }, 40);
      if (!lb) capSet(LAST_LABS, `${scope}|${fid}|${rg}`, st.byModel || [], 20);
      return st;
    } catch (e) { return null; }
    finally {
      if (S.page === 'stats') {
        const curFid = S.scope === 'flow' ? ((flow() && flow().id) || '') : '', curRg = S.scope === 'all' ? (S.rng || 'all') : 'all';
        if (S.scope === scope && curFid === fid && curRg === rg && (S.lab || '') === lb) preserveScroll(() => renderStats());
      }
    }
  });
}
function connectSSE() {
  if (typeof EventSource === 'undefined') { S.conn = 'offline'; return; }
  if (SSE) try { SSE.close(); } catch (e) { /* ignore */ }
  const es = (SSE = new EventSource('/events'));
  const setConn = (c) => { if (S.conn !== c) { S.conn = c; preserveScroll(() => renderBar()); } };
  es.onopen = () => setConn('live');
  es.addEventListener('ready', () => setConn('live'));
  es.addEventListener('update', () => {
    setConn('live');
    clearTimeout(SSE_TMR);
    SSE_TMR = setTimeout(() => { staleStats(); MSG_CACHE.clear(); fetchFlows(false, false); }, 200);
  });
  es.onerror = () => setConn(es.readyState === 2 ? 'offline' : 'reconnecting');
}
addEventListener('online', () => { if (!SSE || SSE.readyState === 2) connectSSE(); fetchFlows(false, false); });
addEventListener('offline', () => { S.conn = 'offline'; preserveScroll(() => renderBar()); });
function bootLive() {
  readHash(); renderAll(); fetchFlows(false, true); connectSSE();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { if (flow() && S.page === 'board') drawMap(); });
}
