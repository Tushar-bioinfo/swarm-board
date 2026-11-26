// ===== stats2.js =====
const SG = { cached: ['Cached input', '#2f97c1'], uncached: ['Uncached input', '#1ccad8'], output: ['Output', '#F5EAD3'], running: ['Running', '#1ccad8'], done: ['Done', '#2f97c1'], stopped: ['Stopped', '#ff6b9a'], interrupted: ['Interrupted', '#F5EAD3'] },
  KM_ = { cost: ['Cost by type', 'Cost by model'], tok: ['Tokens by type', 'Tokens by model'], dur: ['Time by state', 'Time by model'] },
  tdn = (v, a) => `<td class="num"${a || ''}>${v}</td>`, tkn = (v) => (v ? fTok(v) : '—'), du = (s) => (s ? fDur(s) : '—'),
  usdg = (g, e) => (g.r || g.cost ? money(g.cost, e) : '—'),
  thd = (h, n = 1) => `<div class="tws"><table class="tbl"><thead><tr>${JN(h, (x, i) => `<th${i >= n ? ' class="num"' : ''}>${x}</th>`)}</tr></thead><tbody>`;
function segs(P, fmt) {
  const t = P.reduce((s, p) => s + p.v, 0), op = {};
  if (!(t > 0)) return D_('meta', 'No data for this selection');
  return D_('segb', JN(P.filter((p) => p.v > 0), (p) => {
    const o = (op[p.c] = (op[p.c] || 0) + 1);
    return `<i class="sseg" style="flex:${p.v / t} 1 2px;background:${p.c};opacity:${MX(.4, 1.3 - .3 * o)}" data-tip="${E(p.l)}" data-val="${E(fmt(p.v))} · ${(100 * p.v / t).toFixed(1)}%" data-c="${p.c}"></i>`;
  }));
}
function tBar(V) {
  const k = V.key, T = V.T, P = k === 'cost' ? ['cached', 'uncached', 'output'].map((q) => ({ q, v: V.typesCost[q] || 0 })) : k === 'tok' ? [['cached', T.cache], ['uncached', T.in], ['output', T.out]].map(([q, v]) => ({ q, v })) : STS.map((q) => ({ q, v: T.st[q] || 0 }));
  P.forEach((p) => ([p.l, p.c] = SG[p.q]));
  const t = P.reduce((s, p) => s + p.v, 0), fmt = (v) => fM(v, k, V.est);
  return D_('sbar', D_('sh', KM_[k][0]) + segs(P, fmt) + D_('lg', JN(P.filter((p) => p.v > 0), (p) => `<span><i style="background:${p.c}"></i>${p.l} ${t > 0 ? (100 * p.v / t).toFixed(1) : '0.0'}%</span>`)));
}
function mBar(V) {
  const k = V.key, M = V.models.filter((m) => m[k] > 0), top = M.slice(0, 8), rest = M.slice(8).reduce((s, m) => s + m[k], 0), fmt = (v) => fM(v, k, V.est),
    P = top.map((m) => ({ l: m.m, v: m[k], c: labC(m.lab) })).concat(rest > 0 ? [{ l: `${M.length - 8} other models`, v: rest, c: C.mist }] : []);
  return D_('sbar', D_('sh', S_('', KM_[k][1]) + (top[0] ? S_('meta', `${E(top[0].m)} · ${fmt(top[0][k])}`) : '')) + segs(P, fmt));
}
function drl(V) {
  const f = V.f, e = V.est, T = V.T;
  if (S.stab === 'flow' && f) {
    const tools = {}; (f.agents || []).forEach((a) => (a.trace || []).forEach((t) => { if (typeof t[1] === 'number') tools[t[0]] = (tools[t[0]] || 0) + t[1]; }));
    const TL = Object.entries(tools).sort((p, q) => q[1] - p[1]);
    return D_('sdp', (f.goal ? `<p class="prose md">${inl(f.goal)}</p>` : '') + D_('lnk', S_('kick', 'Project root') + S_('id', E(f.projectRoot || '—'))) + (TL.length ? KK('Tool calls', 'mt20') + trc(TL) : ''));
  }
  if (S.stab === 'agent') {
    const sk = { spend: (g) => (g.r ? g.cost : -1), tokens: (g) => g.tok, time: (g) => g.dur, calls: (g) => g.x }[S.sr] || ((g) => g.cost),
      b = (v) => `<button class="s" data-ssort="${v}"${ap(S.sr === v)}>${v[0].toUpperCase() + v.slice(1)}</button>`;
    return D_('seats sso', JN(['spend', 'tokens', 'time', 'calls'], b) + (V.all ? S_('meta', `Top ${V.agents.length} of ${V.st.agentsCount} agents by spend`) : '')) +
      thd(['Agent', 'Model', 'State', 'Time', 'Calls', 'In · Cached · Out', 'API est.'], 3) +
      JN([...V.agents].sort((p, q) => sk(q.g) - sk(p.g)), ({ a, id, model, lab, state, flow: fl, flowId, agentId, g }) => {
        const jump = flowId && agentId ? `<button class="tbtn an" data-agent="${E(agentId)}" data-flowid="${E(flowId)}" style="color:${labC(lab)}">${E(id)}</button>` : `<span class="an" style="color:${labC(lab)}">${E(id)}</span>`;
        return `<tr><td>${S_('mn', sIc(state, isStop({ state }) ? C.bad : labC(lab)) + jump)}${fl && V.all ? D_('meta', E(fl)) : ''}</td><td class="mono mist">${E(model || '—')}</td><td style="color:${isStop({ state }) ? C.bad : C.ink}">${E(stOf(state))}</td>${tdn(du(g.dur), a && state === 'running' && g.dur ? ` data-tick=${g.dur}` : '')}${tdn(cnt(g.x))}${tdn(g.tok ? `${tkn(g.in)} · ${tkn(g.cache)} · ${tkn(g.out)}` : '—')}${tdn(usdg(g, e))}</tr>`;
      }) + `<tr class="tot"><td>Total${V.all ? ' · range' : ` · ${V.agents.length} agents`}</td><td></td><td></td>${tdn(du(T.dur))}${tdn(cnt(T.calls))}${tdn(`${tkn(T.in)} · ${tkn(T.cache)} · ${tkn(T.out)}`)}${tdn(T.cost ? money(T.cost, e) : '—')}</tr></tbody></table></div>`;
  }
  return thd(['Model', 'Agents', 'Time', 'Calls', 'Input', 'Cached', 'Output', 'Total', 'API est.', 'Cache hit'], 1) +
    JN(V.models, (m) => `<tr><td>${S_('mn', emb(m.lab, 'done') + `<span style="color:${labC(m.lab)}">${E(m.m)}</span>`)}</td>${tdn(m.n)}${tdn(du(m.dur))}${tdn(cnt(m.calls))}${tdn(tkn(m.in))}${tdn(tkn(m.cache))}${tdn(tkn(m.out))}${tdn(tkn(m.tok))}${tdn(usdg(m, e || m.e))}${tdn(pct(m.hit))}</tr>`) +
    `<tr class="tot"><td>Total</td>${tdn(V.models.reduce((s, m) => s + m.n, 0))}${tdn(du(T.dur))}${tdn(cnt(T.calls))}${tdn(tkn(T.in))}${tdn(tkn(T.cache))}${tdn(tkn(T.out))}${tdn(tkn(T.tok))}${tdn(T.cost ? money(T.cost, e) : '—')}${tdn(pct(T.hit))}</tr></tbody></table></div>`;
}
const nMax = (v) => { const p = Math.pow(10, FLR(Math.log10(v))); return [1, 2, 2.5, 5, 10].map((x) => x * p).find((x) => x >= v * 1.04) || 10 * p; };
function mnS(P) {
  const n = P.length, d = [], m = [];
  if (n < 2) return n ? `M${f1(P[0][0])},${f1(P[0][1])}` : '';
  for (let i = 0; i < n - 1; i++) { const dx = P[i + 1][0] - P[i][0]; d[i] = dx ? (P[i + 1][1] - P[i][1]) / dx : 0; }
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : 2 / (1 / d[i - 1] + 1 / d[i]);
  let s = `M${f1(P[0][0])},${f1(P[0][1])}`;
  for (let i = 0; i < n - 1; i++) { const [x, y] = P[i], [u, v] = P[i + 1], h = (u - x) / 3; s += `C${f1(x + h)},${f1(y + m[i] * h)} ${f1(u - h)},${f1(v - m[i + 1] * h)} ${f1(u)},${f1(v)}`; }
  return s;
}
const NS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 259200, 604800];
function dCh() {
  const el = $('schart'), V = SV;
  if (!el || !V) return;
  const W = (cW = el.clientWidth), B = V.B, k = V.key, H = W < 560 ? 230 : 300, labs = V.order.filter((l) => !S.lab || l === S.lab);
  if (!B.length || W < 100) { el.innerHTML = D_('meta', 'No timestamps for this selection'); return; }
  const ser = labs.map((l) => B.map((b) => (b.labs[l] || {})[k] || 0)), mx = nMax(MX(1e-6, ...ser.flat())),
    fy = (v) => (k === 'cost' ? '$' + (v === 0 ? '0' : v < 1 ? v.toFixed(v < 0.1 ? 3 : 2) : v.toFixed(v < 10 ? 2 : 0)) : k === 'tok' ? fTok(v) : v === 0 ? '0' : v < 60 ? RD(v) + 's' : v >= 3600 ? +(v / 3600).toFixed(1) + 'h' : RD(v / 60) + 'm'),
    yl = [0, mx / 2, mx], pl = MX(...yl.map((v) => fy(v).length)) * 9 + 14, pt = 12, pb = 36, pw = W - pl - 6, ph = H - pt - pb, n = B.length,
    X = (i) => pl + (n > 1 ? (i * pw) / (n - 1) : pw / 2), Y = (v) => pt + ph - (ph * v) / mx, dx = n > 1 ? pw / (n - 1) : pw,
    day = (s) => s >= 86400, lw = (s) => (day(s) ? 6 : 5) * 9,
    ts = NS.find((s) => s >= (B[n - 1].ts - B[0].ts >= 172800 ? 86400 : V.step) && s % V.step === 0 && (s / V.step) * dx >= lw(s) * 1.35 + 16) || 604800;
  let xt = '';
  B.forEach((b, i) => { if (n > 1 && b.ts % ts) return; const x = X(i), w = lw(ts) / 2; if (x - w < 0 || x + w > W) return; xt += `<text x="${x.toFixed(1)}" y="${H - 10}" text-anchor="middle">${day(ts) ? dayL(b.ts) : hmL(b.ts)}</text>`; });
  const lines = JN(labs, (l, j) => {
    const P = ser[j].map((v, i) => [X(i), Y(v)]);
    if (P.length === 1) return `<circle cx="${f1(P[0][0])}" cy="${f1(P[0][1])}" r="4" fill="${labC(l)}"/>`;
    const d = mnS(P);
    return `<path d="${d}L${f1(X(n - 1))},${Y(0)}L${pl},${Y(0)}Z" fill="${labC(l)}" fill-opacity=".11" stroke="none"/><path d="${d}"${FN} stroke="${labC(l)}" ${SW}="1.75" stroke-linejoin="round"/>`;
  });
  el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Stats chart"><defs><clipPath id="scl"><rect x="${pl}" y="0" width="${pw + 4}" height="${pt + ph + 1}"/></clipPath></defs>${JN(yl, (v) => `<line x1="${pl}" x2="${W - 2}" y1="${Y(v)}" y2="${Y(v)}" stroke="#262c33"/><text x="0" y="${Y(v) + 5}">${fy(v)}</text>`)}<g clip-path="url(#scl)">${lines}</g>${xt}<line class="xh" x1="0" x2="0" y1="${pt}" y2="${pt + ph}" stroke="#aab4bf" ${SO}=".6" visibility="hidden"/><g class="xm"></g><rect class="hov" x="${pl}" y="${pt}" width="${pw}" height="${ph}" fill="transparent"/></svg>`;
  const svg = el.firstChild, hl_ = q1('.xh', svg), gm = q1('.xm', svg), hv = q1('.hov', svg),
    off = () => { SA(hl_, 'visibility', 'hidden'); gm.innerHTML = ''; hPop(); };
  A_(hv, 'pointermove', (ev) => {
    const r = rect(svg), i = clp(RD((ev.clientX - r.left - pl) / (dx || 1)), 0, n - 1), x = X(i), b = B[i];
    SA(hl_, 'x1', x); SA(hl_, 'x2', x); SA(hl_, 'visibility', 'visible');
    gm.innerHTML = JN(labs, (l, j) => `<circle cx="${x}" cy="${Y(ser[j][i])}" r="3.5" fill="${labC(l)}" stroke="#0b0c0e" ${SW}="1.5"/>`);
    const t1 = b.ts + V.step, rows = JN(labs, (l, j) => (ser[j][i] ? `<div><span style="color:${labC(l)}">${E(labN(l))}</span> ${fM(ser[j][i], k, V.est)}</div>` : '')) || D_('mist', 'No activity');
    sPop(hl_, D_('nm', `${b.day} · ${hmL(b.ts)}–${hmL(t1)} UTC`) + rows, C.mist);
  });
  A_(hv, 'pointerleave', off);
}
ON('mouseover', (e) => { const g = e.target.closest && e.target.closest('.sseg'); if (g) sPop(g, D_('nm', E(g.dataset.tip)) + D_('mist', E(g.dataset.val)), g.dataset.c); });
ON('mouseout', (e) => { if (e.target.closest && e.target.closest('.sseg')) hPop(); });