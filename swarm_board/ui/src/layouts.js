// ===== layouts.js: lanes flowchart, council seal =====
function lanes(f, B, det) {
  const co = rootOf(f);
  if (!co) return fan(f, B, det);
  const W = B.W, k = B.k, R = Math.round, Sm = det === 'S', Dt = S.details, sc = Sm ? 36 : R(44 * k), rc = sc / 2, HX = Sm ? 36 : R(52 * k);
  const Lx = Sm ? 104 : clamp(0.34 * W, 168, 280 * k), s = Sm ? 18 : R(22 * k), r = s / 2, WX = Lx + (Sm ? 28 : R(36 * k)), rh = Sm ? 56 : R(64 * k), rows = [];
  const wss = (f.workstreams || []).slice(), seenIds = new Set([co.id]);
  const groups = wss.map((ws) => {
    const A = (f.agents || []).filter((a) => a.id !== co.id && a.workstream === ws.id);
    A.forEach((a) => seenIds.add(a.id));
    return { ws, A };
  });
  const rest = (f.agents || []).filter((a) => !seenIds.has(a.id));
  if (rest.length) groups.push({ ws: { id: '_other', name: 'Workers' }, A: rest });
  if (!groups.length) return fan(f, B, det);
  let Ly = 8;
  groups.forEach(({ ws, A }) => {
    const bot = Ly + 44 + Math.max(1, A.length) * rh + 6, cnt = {};
    A.forEach((a) => (cnt[a.lab] = (cnt[a.lab] || 0) + 1));
    const dl = LB(Object.keys(cnt).sort((p, q) => cnt[q] - cnt[p])[0]).wire;
    B.bg += `<rect x="${f1(Lx)}" y="${Ly}" width="${f1(W - Lx)}" height="${bot - Ly}" fill="#111215"/><path d="M${f1(Lx)} ${Ly}H${W}" stroke="${C.dim}"/><rect x="${f1(Lx)}" y="${Ly - 1}" width="32" height="2" fill="${dl}" opacity=".8"/>`;
    B.label({ x: Lx + 16, y: Ly + 24, size: 15, mono: 1, wt: 600, fill: C.mist, ls: 1.2, steps: [String(ws.name || ws.id).toUpperCase()] });
    A.forEach((a, j) => rows.push([a, Ly + 30 + rh / 2 + j * rh]));
    Ly = bot + 14;
  });
  const HY = rows.length ? (rows[0][1] + rows[rows.length - 1][1]) / 2 : 60, x0 = HX + rc + 16, x1 = WX - r - 3, wires = {};
  B.node(co, HX, HY, sc, { med: 1, ring: 1, R: rc + 20 });
  const rnm = aName(co);
  B.label({ x: Math.max(HX, tw('coordinator', 17, 600) / 2 + 1), y: HY + rc + 36, size: 17, wt: 600, anchor: 'middle', for: co.id, steps: [rnm, trunc(rnm, 14), 'coord.'] });
  if (det === 'L' && Dt) B.label({ x: HX, y: HY + rc + 58, size: 15, fill: C.mist, anchor: 'middle', steps: [(co.trace || []).reduce((t, x) => t + x[1], 0) + ' calls'] });
  rows.forEach(([a, wy]) => {
    const dx = x1 - x0, P = [[x0, HY], [x0 + 0.5 * dx, HY], [x1 - 0.5 * dx, wy], [x1, wy]];
    wires[a.id] = P; B.cable(P, a); B.node(a, WX, wy, s);
    const lx = WX + r + 14, sw = stateWord(a), sc2 = isStop(a) ? C.bad : C.mist, short = sw.split(' · ')[0] + (isStop(a) && errN(a) ? ' · ' + errN(a) : ''), nm = aName(a);
    B.label({ x: lx, y: Dt ? (Sm ? wy + 4 : wy - 3) : wy + 6, size: 17, wt: 600, for: a.id, steps: [nm, trunc(nm, 28), trunc(nm, 16)] });
    if (Dt) B.label({ x: lx, y: Sm ? wy + 24 : wy + 17, size: 15, fill: sc2, for: a.id, steps: Sm ? [short] : [det === 'L' && a.last ? [[sw], ['  ' + trunc(a.last, 42), C.mist]] : null, sw, short] });
  });
  const P = f.posts || [];
  if (!P.length) return Ly + 10;
  const yr = Ly + 16, step = Math.min(44 * k, (W - Lx - 28) / Math.max(1, P.length - 1)), per = {}, ring = [], to = {};
  P.forEach((p) => { const o = [...new Set([p.author].concat(p.mentions || []).filter((x) => x !== co.id))]; if (o.length === 1 && wires[o[0]]) { (per[o[0]] = per[o[0]] || []).push(p); to[p.id] = o[0]; } else ring.push(p); });
  if (!Sm) {
    Object.keys(per).forEach((w) => per[w].forEach((p, i, L) => { const q = bz(wires[w], L.length === 1 ? 0.55 : 0.28 + i * 0.5 / (L.length - 1)); B.beads += bead(p, q[0], q[1], w, p.author === co.id); }));
    ring.forEach((p, i) => { const g = (200 + i * 22) * Math.PI / 180; B.beads += bead(p, HX + Math.cos(g) * (rc + 20), HY + Math.sin(g) * (rc + 20), co.id, p.author === co.id); });
  }
  B.bg += `<path d="M${f1(Lx)} ${yr}H${W - 8}" stroke="${C.thin}"/>`;
  B.label({ x: 0, y: yr + 5, size: 15, mono: 1, wt: 600, fill: C.mist, ls: 1.2, steps: ['BOARD'] });
  P.forEach((p, i) => {
    const x = Lx + 14 + i * step;
    B.beads += bead(p, x, yr, to[p.id] || co.id, p.author === co.id);
    if (!Sm || step >= 30) B.label({ x, y: yr + 28, size: 15, mono: 1, fill: C.mist, anchor: 'middle', steps: ['#' + p.id] });
  });
  return yr + 44;
}
function council(f, B, det) {
  const seats = (f.agents || []).slice().sort((a, b) => (b.weight || 0) - (a.weight || 0)), n = seats.length;
  if (!n) return 80;
  const W = B.W, Sm = det === 'S', k = B.k, Dt = S.details, nL = Dt ? 3 : 1, Rs = Sm ? 52 : clamp(0.1 * W, 44, Math.min(84, 64 * k)), Rq = Sm ? Math.min(0.34 * W, 124) : clamp(0.3 * W, 140, Math.min(300, 220 * k)), s = Sm ? 32 : Math.round((det === 'L' ? 44 : 40) * Math.min(k, 1.3)), r = s / 2, R2 = r + 8;
  const cx = W / 2, cy = 0.309 * Rq + (Sm ? 64 : R2 + 42 + nL * 22), rad = (g) => g * Math.PI / 180, pt = (g, R, X = cx, Y = cy) => [X + Math.cos(rad(g)) * R, Y + Math.sin(rad(g)) * R], P = (p) => p.map(f1).join(' ');
  const maxD = Math.max(0, ...seats.map((a) => a.durS || 0)), dial = Math.max(120, Math.ceil(maxD / 30) * 30), docs = cmpDocs(f);
  const wOf = (a) => (a.weight != null ? a.weight : 1 / n);
  let g0 = 90 + wOf(seats[n - 1]) * 180, sec = '', ticks = '', maj = '', H = 0;
  for (let i = 0; i < 72; i++) ticks += i % 6 ? `M${P(pt(i * 5, Rs + 5))}L${P(pt(i * 5, Rs + 8))}` : '';
  for (let i = 0; i < 12; i++) maj += `M${P(pt(i * 30, Rs + 3))}L${P(pt(i * 30, Rs + 10))}`;
  seats.forEach((a) => {
    const L = LB(a.lab), wtN = wOf(a), span = wtN * 360, mid = g0 + span / 2, [sx, sy] = pt(mid, Rq), e0 = g0 + 1.25, e1 = g0 + span - 1.25;
    sec += `<g class="sec" data-to="${esc(a.id)}" fill="none" stroke="${L.base}" stroke-linecap="round">` + [1.3, 1.7, 2.1, 2.5].map((z, j) => { const rr = Rs - 10.5 + j * 2.8, da = 2.9 / rr * 57.3; let d = ''; for (let g = e0 + da / 2; g <= e1; g += da) d += `M${P(pt(g, rr))}h0`; return `<path stroke-width="${z}" d="${d}"/>`; }).join('') + '</g>';
    if (/COND/.test(a.verdict || '')) sec += `<path d="M${P(pt(mid - 3.4 * 57.3 / Rs, Rs + 0.6))}L${P(pt(mid, Rs - 6))}L${P(pt(mid + 3.4 * 57.3 / Rs, Rs + 0.6))}Z" fill="${C.bg}"/>`;
    B.cable(`M${P(pt(mid + 180, r + 12, sx, sy))}L${P(pt(mid, Rs + 14))}`, a, { w: 2 + 6 * wtN, op: 0.75, cls: 'seat' });
    const fr = Math.min(0.999, (a.durS || 0) / dial), end = pt(-90 + fr * 360, R2, sx, sy);
    B.seal += `<circle cx="${f1(sx)}" cy="${f1(sy)}" r="${R2}" fill="none" stroke="${C.thin}" stroke-width="2"/><path d="M${f1(sx)} ${f1(sy - R2)}A${R2} ${R2} 0 ${fr > 0.5 ? 1 : 0} 1 ${P(end)}" fill="none" stroke="${L.base}" stroke-width="2.5" stroke-linecap="round" stroke-dasharray=".01 4.2"/><circle cx="${f1(end[0])}" cy="${f1(end[1])}" r="2.5" fill="${CR}"/>`;
    B.node(a, sx, sy, s, { R: R2 });
    const up = sy < cy, wt = `w ${wtN.toFixed(2)} · ${fmtDur(a.durS) || '—'}`, vd = a.verdict ? '✓ ' + a.verdict : stateWord(a);
    if (Sm) B.label({ x: sx, y: up ? sy - r - 16 : sy + r + 30, size: 17, wt: 700, fill: L.txt, anchor: 'middle', for: a.id, steps: [labN(a.lab)] });
    else {
      const c = { x: sx, anchor: 'middle', for: a.id }, ls = [{ size: 17, wt: 700, fill: L.txt, steps: [labN(a.lab)] }];
      if (Dt) ls.push({ size: 15, mono: 1, fill: C.mist, steps: [a.model || labN(a.lab)] }, { size: 15, fill: C.mist, steps: [wt, wt.split(' · ')[0]] });
      ls.push({ size: 17, wt: 600, steps: [vd, a.verdict || stateWord(a)] });
      const nl = ls.length;
      ls.forEach((o, j) => B.label(Object.assign({ y: up ? sy - R2 - 12 - (nl - 1 - j) * 22 : sy + R2 + 20 + j * 22 }, o, c)));
    }
    H = Math.max(H, sy + (Sm ? r + 48 : R2 + 44 + nL * 22));
    g0 += span;
  });
  const d = f.decision || { qualifier: '', word: '' };
  B.seal = `<g class="seal" data-go="compare" ${BATR(`Decision ${d.qualifier || ''} ${d.word || ''}, open Compare`)}><circle cx="${cx}" cy="${f1(cy)}" r="${Rs + 14}" fill="none" stroke="${CR}" stroke-opacity=".35" stroke-width=".9"/>` +
    `<path d="${ticks}" stroke="${C.line}"/><path d="${maj}" stroke="${CR}" stroke-opacity=".6"/>${sec}<circle cx="${cx}" cy="${f1(cy)}" r="${Rs - 16}" fill="${C.bg}" stroke="${CR}" stroke-opacity=".25" stroke-dasharray="1.7 1.25"/>` +
    (d.qualifier ? `<text x="${cx}" y="${f1(cy - 0.26 * Rs)}" text-anchor="middle" font-size="15" font-weight="600" class="mo" letter-spacing="1.5" fill="${C.acc}">${esc(d.qualifier)}</text>` : '') +
    `<text x="${cx}" y="${f1(cy + (d.qualifier ? 0.28 : 0.12) * Rs)}" text-anchor="middle" font-size="${f1(clamp(0.42 * Rs, 20, 34))}" font-weight="700" fill="${C.ink}">${esc(d.word || '')}</text></g>` + B.seal;
  if (Sm) {
    seats.forEach((a, i) => {
      const y = H + 16 + i * 30, L = LB(a.lab), wt = `w ${wOf(a).toFixed(2).slice(1)}`, v = a.verdict ? '✓ ' + a.verdict : stateWord(a), H6 = [labN(a.lab), L.txt, 0, 600];
      const si = Math.max(0, docs.findIndex((x) => x.a && x.a.id === a.id));
      B.top += `<g class="sr" data-seat="${si}" data-to="${esc(a.id)}" ${BATR(`${labN(a.lab)} seat, open Compare`)}><rect x="0" y="${y - 18}" width="${W}" height="28" fill="transparent"/><g transform="translate(9 ${y - 5})">${tokIn(a, 16)}</g></g>`;
      B.label({ x: 26, y, size: 15, for: a.id, steps: [[H6, [`  ${wt} · ${fmtDur(a.durS) || '—'} · ${v}`, C.mist]], [H6, [`  ${wt} · ${v}`, C.mist]]].slice(S.details ? 0 : 2).concat(S.details ? [] : [[H6, [`  ${v}`, C.mist]]]) });
    });
    H += 16 + n * 30;
  }
  return H;
}
