// ===== layouts2.js: canopy pods + FLIP unfold, fan flowchart =====
function canopy(f, B, det) {
  const root = rootOf(f), leads = root ? kids(f, root.id) : [];
  if (!root || !leads.length || leads.every((l) => !kids(f, l.id).length)) return fan(f, B, det);
  const W = B.W, k = B.k, R = Math.round, Dt = S.details, Sm = det === 'S', Lg = det === 'L', sn = shortNames(leads), rs = Sm ? 28 : R(32 * k), ry = R(36 * k);
  const P = Sm ? 12 : Lg ? 24 : 20, colW = (W - 2 * P) / (Sm ? Math.min(4, leads.length) : leads.length), uni = leads.every((l) => tw(sn[l.id], 15, 400, 1) <= colW - 8), full = Dt && leads.every((l) => tw(aName(l), 15, 400, 1) <= colW - 8), ls = R((Lg ? 26 : 24) * k), pw = R((Lg ? 24 : 20) * k), pp = R((Lg ? 18 : 16) * k), ps = Lg ? Math.min(13, R(12 * k)) : 10, u = leads.findIndex((l) => l.id === S.unfold);
  B.node(root, W / 2, ry, rs, { med: 1, R: rs / 2 + 16 });
  B.label({ x: W / 2 + rs / 2 + 20, y: ry + 6, size: 17, wt: 600, for: root.id, steps: [trunc(aName(root), 26), 'root'] });
  const pos = leads.map((l, i) => {
    if (Sm) return [P + ((i % 4) + 0.5) * colW, i < 4 ? 104 : 296, i < 4 ? 0 : 1];
    const x = P + (i + 0.5) * colW, q = leads.length > 1 ? (x - W / 2) / (W / 2 - P) : 0;
    return [x, R((Lg ? 120 : 112) * k) + (Lg ? 28 : 20) * k * q * q, 0];
  });
  let H = 0;
  const ghost = {};
  leads.forEach((l, i) => {
    const [x, y, row] = pos[i], fold = u >= 0 && u !== i, L = LB(l.lab);
    B.cable(row ? `M${W / 2} ${ry + 14}V${y - 60}C${W / 2} ${y - 34} ${f1(x)} ${y - 46} ${f1(x)} ${y - 14}` : `M${W / 2} ${ry + 16}C${W / 2} ${ry + 50} ${f1(x)} ${f1(y - 46)} ${f1(x)} ${f1(y - 16)}`, l, { cls: fold ? 'fold' : '' });
    B.node(l, x, y, ls, { cls: fold ? 'fold' : u === i ? 'pick' : '' });
    const ws = kids(f, l.id).slice().sort((a, b) => (b.state === 'running') - (a.state === 'running')), top = y + ls / 2 + (Dt ? 53 : 33), ph = ws.length * pp + (Lg ? 12 : 10), nr = ws.filter((w) => w.state === 'running').length, ix = String(i + 1);
    B.label({ x, y: y + ls / 2 + 23, size: 15, mono: 1, anchor: 'middle', for: l.id, maxX: x + colW / 2 - 4, steps: full ? [trunc(aName(l), 24), sn[l.id], ix] : uni ? [sn[l.id], ix] : [ix] });
    if (Dt) B.label({ x, y: y + ls / 2 + 43, size: 15, mono: 1, fill: C.mist, anchor: 'middle', for: l.id, steps: [`${nr}/${ws.length}`] });
    B.pods += `<path class="pst" d="M${f1(x)} ${f1(y + ls / 2 + 3)}V${f1(top)}" stroke="${nr ? L.base : C.mist}" stroke-opacity="${nr ? 0.7 : 0.45}" stroke-width="1.6" stroke-linecap="round" stroke-dasharray=".01 3.2"/>` + (nr ? `<path class="march pst" d="M${f1(x)} ${f1(y + ls / 2 + 3)}V${f1(top)}" stroke="${L.hi}"/>` : '');
    const pips = ws.map((w, j) => { const py = top + 13 + j * pp; ghost[w.id] = [x, py]; return `<g class="pip${w.state === 'running' ? ' run' : ''}" transform="translate(${f1(x)} ${f1(py)})">${tokIn(w, ps)}</g>`; }).join('');
    B.pods += `<g class="pod" data-lead="${esc(l.id)}" ${BATR(`${aName(l)}, ${ws.length} workers, ${nr} running. Enter unfolds`)}><rect x="${f1(x - pw / 2)}" y="${f1(top)}" width="${pw}" height="${ph}" rx="${pw / 2}" fill="#111214" stroke="${nr ? L.base : C.mist}" stroke-opacity="${nr ? 0.75 : 0.45}" stroke-width="1.6" stroke-linecap="round" stroke-dasharray=".01 3.2"/>${pips}</g>`;
    H = Math.max(H, top + ph + 24);
  });
  if (u >= 0) {
    const lv = kids(f, S.unfold), m = lv.length, [xc, yc] = pos[u], yD = Sm ? 296 : Math.max(...pos.map((p) => p[1])), sp = Math.min(88, (W - 2 * P) / Math.max(1, m));
    let yb = 0;
    lv.forEach((w, j) => {
      const t = m > 1 ? (j - (m - 1) / 2) / ((m - 1) / 2) : 0, x = W / 2 + (j - (m - 1) / 2) * sp, y = yD + 110 + 16 * (1 - t * t), g = ghost[w.id] || [xc, yc], nm = aName(w);
      B.cable(`M${f1(xc)} ${f1(yc + 15)}C${f1(xc)} ${f1(yc + 70)} ${f1(x)} ${f1(y - 60)} ${f1(x)} ${f1(y - 14)}`, w, { cls: 'lw' });
      B.node(w, x, y, 20, { cls: 'leaf', attr: ` data-fl="${f1(g[0])},${f1(g[1])},${f1(x)},${f1(y)}"` });
      B.label({ x, y: y + 35, size: 17, wt: 600, anchor: 'middle', for: w.id, steps: [trunc(nm, 16), trunc(nm, 10), String(j + 1)] });
      if (Lg && Dt) B.label({ x, y: y + 55, size: 15, fill: C.mist, anchor: 'middle', for: w.id, steps: [stateWord(w)] });
      yb = Math.max(yb, y + (Lg && Dt ? 55 : 35));
    });
    B.label({ x: W / 2, y: yb + 30, size: 15, fill: C.mist, anchor: 'middle', steps: [`${trunc(aName(leads[u]), 24)} · ${m} workers · Esc folds`, `${m} workers · Esc folds`] });
    H = Math.max(H, yb + 46);
  }
  return H;
}
function unfoldAnim(host) {
  const svg = q1('svg', host);
  if (!svg || S.unfold === S.unf0) return;
  S.unf0 = S.unfold;
  if (!S.unfold) { svg.classList.add('podin'); return; }
  const L = qa('.nd.leaf', svg), Wr = qa('.cab.lw', svg);
  L.forEach((g) => { const [gx, gy, x, y] = g.dataset.fl.split(',').map(Number); g.style.transform = `translate(${gx - x}px,${gy - y}px) scale(.5)`; g.style.opacity = 0; });
  Wr.forEach((p) => { const da = p.getAttribute('stroke-dasharray'); p.setAttribute('pathLength', 1); p.setAttribute('stroke-dasharray', 1); p.style.strokeDashoffset = 1; p.classList.add('draw');
    setTimeout(() => { p.removeAttribute('pathLength'); p.classList.remove('draw'); p.style.strokeDashoffset = ''; if (da) p.setAttribute('stroke-dasharray', da); else p.removeAttribute('stroke-dasharray'); }, 640); });
  requestAnimationFrame(() => requestAnimationFrame(() => { L.forEach((g) => { g.style.transform = ''; g.style.opacity = ''; }); Wr.forEach((p) => (p.style.strokeDashoffset = 0)); }));
}
function fan(f, B, det) {
  const ag = f.agents || [];
  if (!ag.length) return 60;
  const ids = new Set(ag.map((a) => a.id)), roots = ag.filter((a) => !a.parent || !ids.has(a.parent));
  const singleRoot = roots.length === 1 && ag.length > 1 ? roots[0] : null;
  const ch = singleRoot ? kids(f, singleRoot.id) : (roots.length ? roots : ag);
  const W = B.W, Sm = det === 'S', k = B.k, R = Math.round, X1 = Sm ? 88 : clamp(0.3 * W, 150, 240 * k), dX = Sm ? 36 : R(44 * k);
  const s1 = Sm ? 20 : R(24 * k), s2 = Sm ? 18 : R(20 * k), pitch = Sm ? 52 : R(60 * k), hs = Sm ? 28 : R(32 * k), HX = Sm ? 26 : R(36 * k);
  const labs = [...new Set(ch.map((a) => a.lab))], band = labs.length > 1, rows = [], pos = {}, seen = new Set(singleRoot ? [singleRoot.id] : []);
  let last = 8 - 34;
  (band ? labs : [null]).forEach((lb) => {
    const grp = ch.filter((a) => !band || a.lab === lb), top = last + 34;
    if (band) {
      const L = LB(lb);
      B.label({ x: X1 - 16, y: top + 20, size: 15, mono: 1, wt: 600, fill: L.txt, ls: 1.2, steps: [`${labN(lb).toUpperCase()} · ${grp.length}`] });
      B.bg += `<path d="M${f1(X1 - 16)} ${top + 28}H${W}" stroke="${C.thin}"/><rect x="${f1(X1 - 16)}" y="${top + 27}" width="32" height="2" fill="${L.wire}"/>`;
    }
    let y = top + (band ? 60 : 28);
    grp.forEach((c) => (function walk(a, d, par) {
      if (seen.has(a.id) || d > 6) return;
      seen.add(a.id);
      rows.push([a, d, y, par]);
      last = y; y += pitch;
      kids(f, a.id).forEach((k2) => walk(k2, d + 1, a.id));
    })(c, 1, singleRoot ? singleRoot.id : null));
  });
  const d1 = rows.filter((r) => r[1] === 1), HY = d1.length ? d1.reduce((t, r) => t + r[2], 0) / d1.length : 40, hr = hs / 2, x0 = HX + (singleRoot ? hr + (Sm ? 6 : 16) : 14);
  if (singleRoot) {
    B.node(singleRoot, HX, HY, hs, { med: !Sm, R: hr + (Sm ? 0 : 16) });
    B.label({ x: HX, y: HY + hr + (Sm ? 26 : 38), size: 17, wt: 600, anchor: 'middle', for: singleRoot.id, steps: [trunc(aName(singleRoot), 16), 'root'] });
  } else {
    B.bg += `<circle cx="${HX}" cy="${f1(HY)}" r="6" fill="${C.bg}" stroke="${C.line}" stroke-width="1.5"/>`;
    B.label({ x: HX, y: HY + 26, size: 14, mono: 1, fill: C.mist, anchor: 'middle', steps: [HARN[f.harness] || f.harness || 'run'] });
  }
  rows.forEach(([a, d, y, par]) => {
    const s = d === 1 ? s1 : s2, r = s / 2, X = X1 + (d - 1) * dX;
    pos[a.id] = [X, y, r];
    if (d === 1 || !pos[par]) { const x1 = X - r - 4, dx = x1 - x0; B.cable([[x0, HY], [x0 + 0.5 * dx, HY], [x1 - 0.5 * dx, y], [x1, y]], a); }
    else { const p = pos[par]; B.cable(`M${f1(p[0])} ${f1(p[1] + p[2] + 4)}V${f1(y - 10)}Q${f1(p[0])} ${f1(y)} ${f1(p[0] + 10)} ${f1(y)}H${f1(X - r - 4)}`, a); }
    B.node(a, X, y, s);
    const lx = X + r + 14, L = LB(a.lab), sw = stateWord(a), sep = [' · ', C.mist], stt = [sw, isStop(a) ? C.bad : C.mist], lab = [labN(a.lab), L.txt], mo = [a.model || '', C.mist, 1], nm = aName(a);
    const J = (...p) => p.filter((q) => q[0]).flatMap((q, i) => (i ? [sep, q] : [q]));
    B.label({ x: lx, y: S.details ? y - 3 : y + 6, size: 17, wt: 600, for: a.id, steps: [nm, trunc(nm, 44), trunc(nm, 28), trunc(nm, 16)] });
    if (S.details) B.label({ x: lx, y: y + 17, size: 15, fill: C.mist, for: a.id, steps: Sm ? [[stt]] : isStop(a) ? [J(stt, lab, mo), J(stt, lab), [stt]] : [J(lab, mo, stt), J(lab, mo), J(mo), J(lab)] });
  });
  return Math.max(last + 40, HY + hr + 50);
}
