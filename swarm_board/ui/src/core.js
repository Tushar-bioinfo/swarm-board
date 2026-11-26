// ===== core.js =====
let D = { envId: null, snapshot: '', flows: [], activeCount: null, errors: {} },
  AT_ = { loaded: false, loading: false, err: null, threads: [], liveCount: 0, fallback: false }, FL = [];
const QS = new URLSearchParams(location.search), $ = (i) => document.getElementById(i),
  EU = encodeURIComponent, CE = (t) => document.createElement(t), q1 = (s, r = document) => r.querySelector(s),
  qa = (s, r = document) => [...r.querySelectorAll(s)], ON = (t, f, c) => document.addEventListener(t, f, c),
  MX = Math.max, MN = Math.min, RD = Math.round, FLR = Math.floor, OK = Object.keys, isF = Number.isFinite,
  Jp = JSON.parse, Js = JSON.stringify, isArr = Array.isArray,
  E = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  LX = '<span class="lbox"></span>', EMP = (m) => `<p class="empty">${m}</p>`, NTE = (m) => `<p class="note">${m}</p>`,
  BATR = (l) => `tabindex="0" role="button" aria-label="${E(l)}"`, PRE = 'controls preload="metadata"',
  ap = (b) => ` aria-pressed="${!!b}"`, TB = (a, t) => `<button class="tbtn" ${a}>${t}</button>`,
  KK = (t, c = '') => `<div class="${c ? c + ' ' : ''}kick">${t}</div>`,
  D_ = (c, h) => `<div${c ? ` class="${c}"` : ''}>${h}</div>`,
  S_ = (c, h) => `<span${c ? ` class="${c}"` : ''}>${h}</span>`,
  JN = (a, f, s = '') => a.map(f).join(s),
  A_ = (e, t, f, o) => e.addEventListener(t, f, o), SA = (e, k, v) => e.setAttribute(k, v),
  CL = (e) => e.classList, TG = (e, c, b) => e.classList.toggle(c, b),
  IH = (i, h) => { const e = $(i); if (e) e.innerHTML = h; }, RAF = requestAnimationFrame,
  RM = () => matchMedia('(prefers-reduced-motion: reduce)').matches, DOC = document,
  SW = 'stroke-width', SO = 'stroke-opacity', SD = 'stroke-dasharray', FN = ' fill="none"',
  rect = (e) => e.getBoundingClientRect(),
  C = { bg: '#0b0c0e', card: '#121315', thin: '#262c33', line: '#587291', dim: '#34404d', ink: '#f2f2f0', mist: '#aab4bf', haze: '#97a2ae', acc: '#1ccad8', bad: '#ff6b9a' },
  LAB = {};
[['google', '#0cf574', '#0cd265'], ['anthropic', '#D97757', '#ba674c'], ['openai', '#c3cdd9', '#70767e'],
  ['xai', '#f4f4f2', '#8b8c8b'], ['t3', '#2f97c1', '#2a82a6'], ['other', '#aab4bf', '#6f7c8a']].forEach(([k, b, l]) => (LAB[k] = { k, txt: b, base: b, lo: l, wire: b, hi: b }));
const LN = { google: 'Google', openai: 'OpenAI', anthropic: 'Anthropic', xai: 'xAI', t3: 'T3 Code', other: 'Other' },
  HN = { agy: 'Antigravity swarm', t3: 'T3', committee: 'Committee', orchestrate: 'Orchestrate', delegate: 'Agent delegate', claude: 'Claude', codex: 'Codex', grok: 'Grok' },
  labC = (l) => (LAB[l] || LAB.other).base, labN = (l) => LN[l] || l || 'Other',
  MO = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' '),
  p2 = (n) => String(n).padStart(2, '0'), OKU = /^(https?:\/\/|t3-thread:\/\/)/i, safeUrl = (u) => (u && OKU.test(u) ? u : ''),
  store = {
    get(k, d) { try { const v = localStorage.getItem('cb.' + k); return v == null ? d : Jp(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('cb.' + k, Js(v)); } catch (e) {} },
  },
  S = {
    fi: 0, fid: null, view: 'overview', arg: '', sel: null, dm: null, filt: {}, size: store.get('size', 18), wr: false,
    sp: store.get('split', 0), cv: null, dt: store.get('details', false), st: 0, sbs: false, al: false,
    pk: null, dn: store.get('done', {}), fn: false, ol: false, uf: null, u0: null,
    page: 'board', scope: 'flow', sr: 'spend', met: 'cost', rng: 'all', lab: null, stab: 'model',
    ld: true, le: null, conn: 'connecting', sq: '', at: 'all', aq: '',
  },
  flow = () => FL[S.fi] || null,
  aNm = (a) => (a && (a.name || a.id)) || '', aLb = (a) => (a && (a.label || a.name || a.id)) || '', aTk = (a) => (a && (a.task || a.name || a.id)) || '',
  trunc = (s, n) => ((s = String(s || '')).length > n ? s.slice(0, n - 1) + '…' : s),
  rOf = (f) => (f && f.agents && f.agents.length ? f.agents.find((a) => !a.parent) || f.agents.find((a) => a.role === 'root') || f.agents[0] : null),
  LST = { running: 1, waiting: 1, queued: 1 }, isLS = (s) => !!LST[s],
  isFL = (f) => !!f && (!!f.isLive || (f.agents || []).some((a) => isLS(a.state))),
  lvCnt = () => (D.activeCount != null ? D.activeCount : FL.filter(isFL).length),
  fAge = (t) => { const s = Date.parse(t || ''); if (!isF(s)) return ''; const d = MX(0, (Date.now() - s) / 1000); return d < 60 ? '<1 min' : d < 3600 ? MX(1, RD(d / 60)) + ' min' : d < 86400 ? FLR(d / 3600) + ' h' : FLR(d / 86400) + ' d'; },
  flAge = (f) => (!f ? '' : isFL(f) ? 'live' : fAge(f.updatedAt || f.startedAt)),
  geo = () => {
    const w = S.aw || 1100, g = w >= 1060 ? 24 : w >= 680 ? 20 : 16, inn = MN(w, 1440) - 2 * g, wd = w >= 1060, p = S.sp || (w >= 1400 ? 46 : 50), m = CL($('app')).contains('appm') ? 12 : 0;
    return { inner: inn, p, mapW: !wd ? inn : S.cv === 'map' ? inn - 20 : (inn - 20) * p / 100, sideW: (!wd ? inn : S.cv === 'side' ? inn - 20 : (inn - 20) * (100 - p) / 100) - m };
  },
  agOf = (f, id) => (f && f.agents ? f.agents.find((a) => a.id === id) : null),
  kids = (f, id) => (f && f.agents ? f.agents.filter((a) => a.parent === id) : []),
  oOf = (f, id) => (f && f.outputs ? f.outputs.find((o) => o.id === id) : null),
  errN = (a) => ((a && a.errors) || []).reduce((s, e) => s + (e.count || 0), 0),
  isStop = (a) => !!a && (a.state === 'killed' || a.state === 'error'),
  fTok = (n) => { if (n == null || !isF(+n)) return '0'; n = +n; return n >= 1e9 ? (n / 1e9).toFixed(n >= 1e10 ? 1 : 2) + 'B' : n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 1 : 2) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'K' : String(RD(n)); },
  fDur = (s) => { if (s == null || !isF(+s)) return ''; s = MX(0, +s); if (s < 60) return s.toFixed(1) + 's'; const t = FLR(s); return t < 3600 ? FLR(t / 60) + 'm ' + p2(t % 60) + 's' : FLR(t / 3600) + 'h ' + p2(FLR(t % 3600 / 60)) + 'm'; },
  fUsd = (u, e) => (u == null || !isF(+u) ? '—' : (e ? '~' : '') + '$' + Number(u).toFixed(2) + (e ? ' est.' : '')),
  fKB = (b) => (b == null ? '' : b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : (b / 1024).toFixed(1) + ' KB'),
  stWord = (a) => {
    if (!a) return '';
    const e = errN(a), k = a.errors && a.errors[0] ? String(a.errors[0].kind || '').replace('perm_timeout', 'permission timeout').replace(/_/g, ' ') : '';
    return { running: 'running', done: 'done', interrupted: 'interrupted', killed: 'stopped' + (e ? ' · ' + e + ' ' + k + (e > 1 ? 's' : '') : ''),
      error: 'failed' + (e && k ? ' · ' + e + ' ' + k : ''), stale: 'quiet', waiting: 'waiting for you', queued: 'queued' }[a.state] || a.state;
  },
  stCol = (a) => (isStop(a) ? C.bad : a && a.state === 'waiting' ? C.acc : a && (a.state === 'stale' || a.state === 'queued') ? C.mist : labC(a && a.lab)),
  RG = (x = '') => `<circle cx="9" cy="9" r="7.2" pathLength="38" ${SD}=".01 1.99"${x}/>`,
  TP = (d, w = 1.5) => `<path d="${d}" transform="translate(9.8 9.8)" ${SO}=".75" ${SW}="${w}"/><path d="${d}" transform="translate(9 9)" stroke="#F5EAD3" ${SW}="${w}"/>`,
  CK = 'M-3 .3L-1 2.2L3 -1.9',
  SI = {
    running: `<g class="pr">${RG()}</g><circle cx="9" cy="9" r="2.6" fill="currentColor" stroke="none"/>`,
    done: RG() + TP(CK),
    interrupted: RG(` ${SO}=".25"`) + `<path d="M16.2 9A7.2 7.2 0 0 1 1.8 9" pathLength="18" ${SD}=".01 1.99"/>` + TP('M-1.4 -2.2V2.2M1.4 -2.2V2.2'),
    stop: RG() + TP('M-2.2 -2.2L2.2 2.2M2.2 -2.2L-2.2 2.2'),
    waiting: RG(` ${SO}=".5"`) + TP('M-2.8 0h0M0 0h0M2.8 0h0', 2),
    queued: `<circle cx="9" cy="9" r="7.2" pathLength="38" ${SD}=".01 3.99" ${SO}=".7"/>`,
    quiet: RG(` ${SO}=".45"`) + `<path d="M6 9h0M9 9h0M12 9h0" ${SW}="2" ${SO}=".6"/>`,
    cond: RG() + TP(CK) + '<path d="M13.8 2.6a1.6 1.6 0 1 0 3.2 0a1.6 1.6 0 1 0-3.2 0" fill="currentColor" fill-opacity=".9" stroke="none"/>',
  },
  SK = { killed: 'stop', error: 'stop', stale: 'quiet' },
  sIc = (st, col, s = 18) => `<svg class="si" width="${s}" height="${s}" viewBox="0 0 18 18" fill="none" stroke="currentColor" ${SW}="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="color:${col}">${SI[SK[st] || st] || SI.done}</svg>`,
  flSt = (f) => { const a = (f && f.agents) || []; return a.some((x) => x.state === 'running') ? 'running' : a.some((x) => x.state === 'waiting') ? 'waiting' : a.some((x) => x.state === 'queued') ? 'queued' : a.some(isStop) ? 'killed' : a.some((x) => x.state === 'interrupted') ? 'interrupted' : 'done'; },
  flIc = (g, s = 16) => { const st = flSt(g); return sIc(st, st === 'running' || st === 'waiting' ? C.acc : st === 'killed' ? C.bad : C.mist, s); },
  nOf = (f) => {
    if (!f || !f.agents) return [];
    const it = [], ks = {};
    f.agents.filter(isStop).forEach((a) => (a.errors || []).forEach((e) => { (ks[e.kind] = ks[e.kind] || { ag: [], n: 0 }).ag.push(a); ks[e.kind].n += e.count || 1; }));
    OK(ks).forEach((k) => { const g = ks[k], w = k === 'perm_timeout' ? 'stopped waiting 60 s for write approval' : 'stopped: ' + k.replace(/_/g, ' '); it.push({ key: f.id + ':' + k, st: 'killed', ag: g.ag, text: `${g.ag.length} worker${g.ag.length > 1 ? 's' : ''} ${w} (${g.n})`, acts: ['errors', 'chat', 'resume'] }); });
    const byL = {};
    f.agents.filter((a) => a.state === 'interrupted' && a.role !== 'root').forEach((a) => (byL[a.lab] = byL[a.lab] || []).push(a));
    OK(byL).forEach((l) => it.push({ key: f.id + ':int:' + l, st: 'interrupted', ag: byL[l], text: `${byL[l].length} ${labN(l)} worker${byL[l].length > 1 ? 's were' : ' was'} interrupted`, acts: ['chat'] }));
    f.agents.filter((a) => a.state === 'waiting').forEach((a) => it.push({ key: f.id + ':w:' + a.id, st: 'waiting', ag: [a], text: `${trunc(aLb(a), 32)} is waiting for your approval`, acts: ['chat'] }));
    f.agents.filter((a) => a.state === 'stale').forEach((a) => it.push({ key: f.id + ':s:' + a.id, st: 'stale', ag: [a], text: `${trunc(aLb(a), 32)} has been quiet for a while`, acts: ['chat'] }));
    if (f.decision && f.decision.qualifier) it.push({ key: f.id + ':dec', st: 'cond', ag: [], text: 'Decision is conditional: read the conditions', acts: ['compare'] });
    return it.map((i) => Object.assign(i, { flow: f })).filter((i) => !S.dn[i.key]);
  },
  allN = () => FL.flatMap(nOf),
  visFl = () => {
    const F = S.filt;
    return FL.map((f, i) => i).filter((i) => {
      const f = FL[i];
      return !(F.live && !isFL(f)) && !(F.h && F.h !== f.harness) && !(F.lab && !(f.agents || []).some((a) => a.lab === F.lab));
    });
  };