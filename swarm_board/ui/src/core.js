// ===== core.js: data, state, icons, needs-you, formatting =====
let D = { envId: null, snapshot: '', flows: [], errors: {} };
let FL = [];
const QS = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);
const EU = encodeURIComponent, CE = (t) => document.createElement(t), q1 = (s, r = document) => r.querySelector(s), qa = (s, r = document) => [...r.querySelectorAll(s)], ON = (t, f, c) => document.addEventListener(t, f, c);
const LX = '<span class="lbox"></span>', EMP = (m) => `<p class="empty">${m}</p>`, NTE = (m) => `<p class="note">${m}</p>`, BATR = (l) => `tabindex="0" role="button" aria-label="${esc(l)}"`, PRE = 'controls preload="metadata"';
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const C = { bg: '#0b0c0e', card: '#121315', thin: '#262c33', line: '#587291', dim: '#34404d', ink: '#f2f2f0', mist: '#aab4bf', haze: '#97a2ae', acc: '#1ccad8', bad: '#ff6b9a' };
const LABC = { google: '#0cf574', openai: '#c3cdd9', anthropic: '#D97757', xai: '#f4f4f2', t3: '#2f97c1', other: '#aab4bf' };
const LABN = { google: 'Google', openai: 'OpenAI', anthropic: 'Anthropic', xai: 'xAI', t3: 'T3 Code', other: 'Other' };
const HARN = { agy: 'Antigravity swarm', t3: 'T3', committee: 'Committee', orchestrate: 'Orchestrate', delegate: 'Agent delegate', claude: 'Claude', codex: 'Codex', grok: 'Grok' };
const labC = (l) => LABC[l] || '#aab4bf';
const labN = (l) => LABN[l] || l || 'Other';
const store = {
  get(k, d) { try { const v = localStorage.getItem('cb.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('cb.' + k, JSON.stringify(v)); } catch (e) { /* storage blocked */ } },
};
const S = {
  fi: 0, flowId: null, view: 'overview', arg: '', sel: null, dim: null, filt: {}, size: store.get('size', 18), wrap: false,
  split: store.get('split', 0), cover: null, details: store.get('details', false), seat: 0, sbs: false, align: false,
  pick: null, done: store.get('done', {}), find: false, outline: false, unfold: null, unf0: null,
  page: 'board', scope: 'flow', ssort: 'spend', met: 'cost', rng: 'all', lab: null, stab: 'model',
  loading: true, loadErr: null, conn: 'connecting', swq: '',
};
const flow = () => FL[S.fi] || null;
const aName = (a) => (a && (a.name || a.id)) || '';
const trunc = (s, n) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const rootOf = (f) => (f && f.agents && f.agents.length ? f.agents.find((a) => !a.parent) || f.agents.find((a) => a.role === 'root') || f.agents[0] : null);
function flowAge(f) {
  if (!f) return '';
  if ((f.agents || []).some((a) => a.state === 'running')) return 'live';
  const ts = Date.parse(f.updatedAt || f.startedAt || '');
  if (!Number.isFinite(ts)) return '';
  const d = Math.max(0, (Date.now() - ts) / 1000);
  if (d < 60) return '<1 min';
  if (d < 3600) return Math.max(1, Math.round(d / 60)) + ' min';
  if (d < 86400) return Math.floor(d / 3600) + ' h';
  return Math.floor(d / 86400) + ' d';
}
function geo() {
  const w = S.appW || 1100, gut = w >= 1060 ? 24 : w >= 680 ? 20 : 16, inner = Math.min(w, 1440) - 2 * gut, wide = w >= 1060, p = S.split || (w >= 1400 ? 46 : 50);
  const app = $('app').classList.contains('appm') ? 12 : 0;
  return { inner, p, mapW: !wide ? inner : S.cover === 'map' ? inner - 20 : (inner - 20) * p / 100, sideW: (!wide ? inner : S.cover === 'side' ? inner - 20 : (inner - 20) * (100 - p) / 100) - app };
}
const agentOf = (f, id) => (f && f.agents ? f.agents.find((a) => a.id === id) : null);
const kids = (f, id) => (f && f.agents ? f.agents.filter((a) => a.parent === id) : []);
const outOf = (f, id) => (f && f.outputs ? f.outputs.find((o) => o.id === id) : null);
const errN = (a) => ((a && a.errors) || []).reduce((s, e) => s + (e.count || 0), 0);
const isStop = (a) => !!a && (a.state === 'killed' || a.state === 'error');
function fmtTok(n) { if (n == null || !Number.isFinite(+n)) return '0'; n = +n; if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 1 : 2) + 'B'; if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 1 : 2) + 'M'; if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K'; return String(Math.round(n)); }
function fmtDur(s) { if (s == null || !Number.isFinite(+s)) return ''; s = Math.max(0, +s); if (s < 60) return s.toFixed(1) + 's'; const t = Math.floor(s), p = (n) => String(n).padStart(2, '0'); return t < 3600 ? Math.floor(t / 60) + 'm ' + p(t % 60) + 's' : Math.floor(t / 3600) + 'h ' + p(Math.floor(t % 3600 / 60)) + 'm'; }
const fmtCost = (u, e) => (u == null ? '—' : (e ? '~' : '') + '$' + Number(u).toFixed(2) + (e ? ' est.' : ''));
function fmtKB(b) { return b == null ? '' : b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : (b / 1024).toFixed(1) + ' KB'; }
function stateWord(a) {
  if (!a) return '';
  const e = errN(a), k = a.errors && a.errors[0] ? String(a.errors[0].kind || '').replace('perm_timeout', 'permission timeout').replace(/_/g, ' ') : '';
  return { running: 'running', done: 'done', interrupted: 'interrupted', killed: 'stopped' + (e ? ' · ' + e + ' ' + k + (e > 1 ? 's' : '') : ''),
    error: 'failed' + (e && k ? ' · ' + e + ' ' + k : ''), stale: 'quiet', waiting: 'waiting for you', queued: 'queued' }[a.state] || a.state;
}
const stateCol = (a) => (isStop(a) ? C.bad : a && a.state === 'waiting' ? C.acc : a && (a.state === 'stale' || a.state === 'queued') ? C.mist : labC(a && a.lab));
// 18-px Halftone Plate state icons
const RG = (x = '') => `<circle cx="9" cy="9" r="7.2" pathLength="38" stroke-dasharray=".01 1.99"${x}/>`;
const TP = (d, w = 1.5) => `<path d="${d}" transform="translate(9.8 9.8)" stroke-opacity=".75" stroke-width="${w}"/><path d="${d}" transform="translate(9 9)" stroke="#F5EAD3" stroke-width="${w}"/>`;
const CK = 'M-3 .3L-1 2.2L3 -1.9';
const SI = {
  running: `<g class="pr">${RG()}</g><circle cx="9" cy="9" r="2.6" fill="currentColor" stroke="none"/>`,
  done: RG() + TP(CK),
  interrupted: RG(' stroke-opacity=".25"') + '<path d="M16.2 9A7.2 7.2 0 0 1 1.8 9" pathLength="18" stroke-dasharray=".01 1.99"/>' + TP('M-1.4 -2.2V2.2M1.4 -2.2V2.2'),
  stop: RG() + TP('M-2.2 -2.2L2.2 2.2M2.2 -2.2L-2.2 2.2'),
  waiting: RG(' stroke-opacity=".5"') + TP('M-2.8 0h0M0 0h0M2.8 0h0', 2),
  queued: '<circle cx="9" cy="9" r="7.2" pathLength="38" stroke-dasharray=".01 3.99" stroke-opacity=".7"/>',
  quiet: RG(' stroke-opacity=".45"') + '<path d="M6 9h0M9 9h0M12 9h0" stroke-width="2" stroke-opacity=".6"/>',
  cond: RG() + TP(CK) + '<path d="M13.8 2.6a1.6 1.6 0 1 0 3.2 0a1.6 1.6 0 1 0-3.2 0" fill="currentColor" fill-opacity=".9" stroke="none"/>',
};
const SIK = { killed: 'stop', error: 'stop', stale: 'quiet' };
const sIcon = (st, col, s = 18) => `<svg class="si" width="${s}" height="${s}" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="color:${col}">${SI[SIK[st] || st] || SI.done}</svg>`;
const flowIcon = (g, s = 16) => { const st = flowState(g); return sIcon(st, st === 'running' ? C.acc : st === 'killed' ? C.bad : C.mist, s); };
function flowState(f) {
  const a = (f && f.agents) || [];
  if (a.some((x) => x.state === 'running')) return 'running';
  if (a.some(isStop)) return 'killed';
  if (a.some((x) => x.state === 'interrupted')) return 'interrupted';
  return 'done';
}
function needsOf(f) {
  if (!f || !f.agents) return [];
  const items = [], kinds = {};
  f.agents.filter(isStop).forEach((a) => (a.errors || []).forEach((e) => { (kinds[e.kind] = kinds[e.kind] || { ag: [], n: 0, msg: e.msg }).ag.push(a); kinds[e.kind].n += e.count || 1; }));
  Object.keys(kinds).forEach((k) => {
    const g = kinds[k], w = k === 'perm_timeout' ? 'stopped waiting 60 s for write approval' : 'stopped: ' + k.replace(/_/g, ' ');
    items.push({ key: f.id + ':' + k, st: 'killed', ag: g.ag, text: `${g.ag.length} worker${g.ag.length > 1 ? 's' : ''} ${w} (${g.n})`, acts: ['errors', 'chat', 'resume'] });
  });
  const intr = f.agents.filter((a) => a.state === 'interrupted' && a.role !== 'root');
  const byLab = {};
  intr.forEach((a) => (byLab[a.lab] = byLab[a.lab] || []).push(a));
  Object.keys(byLab).forEach((l) => items.push({ key: f.id + ':int:' + l, st: 'interrupted', ag: byLab[l],
    text: `${byLab[l].length} ${labN(l)} worker${byLab[l].length > 1 ? 's were' : ' was'} interrupted`, acts: ['chat'] }));
  f.agents.filter((a) => a.state === 'waiting').forEach((a) => items.push({ key: f.id + ':w:' + a.id, st: 'waiting', ag: [a], text: `${trunc(aName(a), 32)} is waiting for your approval`, acts: ['chat'] }));
  f.agents.filter((a) => a.state === 'stale').forEach((a) => items.push({ key: f.id + ':s:' + a.id, st: 'stale', ag: [a], text: `${trunc(aName(a), 32)} has been quiet for a while`, acts: ['chat'] }));
  if (f.decision && f.decision.qualifier) items.push({ key: f.id + ':dec', st: 'cond', ag: [], text: 'Decision is conditional: read the conditions', acts: ['compare'] });
  return items.map((i) => Object.assign(i, { flow: f })).filter((i) => !S.done[i.key]);
}
const allNeeds = () => FL.flatMap(needsOf);
function visibleFlows() {
  const F = S.filt;
  return FL.map((f, i) => i).filter((i) => {
    const f = FL[i];
    if (F.live && flowState(f) !== 'running') return false;
    if (F.h && F.h !== f.harness) return false;
    if (F.lab && !(f.agents || []).some((a) => a.lab === F.lab)) return false;
    return true;
  });
}
