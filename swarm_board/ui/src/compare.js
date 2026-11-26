// ===== compare.js =====
function cDocs(f, ids) {
  const outs = ids ? ids.map((i) => oOf(f, i)).filter(Boolean) : (f.outputs || []).filter((o) => o.kind === 'verdict');
  return outs.map((o) => {
    const need = o.body == null && !o._loaded && !o._err && !/^(video|audio|post)$/.test(o.kind);
    if (need) ldOut(f, o);
    const a = agOf(f, o.by), j = o.kind === 'verdict' ? pVerd(o) : null, text = j ? j.response : o.kind === 'post' ? (o.post && o.post.text) || '' : o.body || '';
    return { o, a, j, text, loading: need, name: a && o.kind === 'verdict' ? labN(a.lab) : o.title, col: a ? labC(a.lab) : C.ink, words: text.split(/\s+/).filter(Boolean).length, cites: (text.match(CRE) || []).length };
  });
}
const fPara = (t) => String(t || '').trim().split(/\n\s*\n/)[0].replace(/^\s*\d+[.)]\s+/, ''),
  normH = (t) => new Set(t.toLowerCase().replace(/[\d]+[.)]?/g, ' ').replace(/[^a-z\s]/g, ' ').split(/\s+/).filter((w) => w.length > 1 || w === 'a')),
  jac = (a, b) => { let i = 0; a.forEach((x) => b.has(x) && i++); return i / (a.size + b.size - i || 1); };
function sections(t) {
  const r = md(t), d = CE('div'), out = []; d.innerHTML = r.html;
  let cur = null;
  [...d.children].forEach((el) => {
    const leads = el.matches('ol,ul') ? [...el.children].filter((li) => li.dataset.lead) : [];
    if (leads.length) { leads.forEach((li) => { const hd = r.heads.find((h) => h.id === li.id); if (hd) out.push({ h: hd.text, html: li.innerHTML }); }); cur = null; return; }
    const h = el.id && r.heads.find((x) => x.id === el.id);
    if (h) { cur = { h: h.text, html: el.dataset.lead ? el.outerHTML : '' }; out.push(cur); } else if (cur) cur.html += el.outerHTML;
  });
  return out;
}
function aGrps(docs) {
  const cl = [];
  docs.forEach((d, di) => sections(d.text).forEach((s) => {
    const k = normH(s.h); let c = cl.find((x) => jac(x.k, k) >= 0.5 && !x.by[di]);
    if (!c) cl.push((c = { k, h: s.h, by: {} }));
    c.by[di] = s.html;
  }));
  return cl.filter((c) => OK(c.by).length >= 2);
}
function cDocH(d, f) {
  if (d.o.kind === 'video' || d.o.kind === 'audio' || ((d.o.kind === 'file' || d.o.kind === 'code') && (VEX.test(d.o.path || '') || AEX.test(d.o.path || '')))) return bFor(d.o, f).html;
  return d.loading ? NTE('Loading output…') : d.o._err ? EMP(E(d.o._err)) : d.o.kind === 'code' ? cView(d.text, 1) : md(d.text).html;
}
function cHtml(f, ids) {
  const docs = cDocs(f, ids), rw = geo().sideW, wide = rw >= 900;
  if (!docs.length) return D_('rhead', KK('Compare') + '<h2>Compare outputs</h2>') + EMP('No outputs available to compare.');
  S.st = MN(S.st, docs.length - 1);
  let top = '';
  if (f.decision && !ids) {
    const pos = docs.filter((d) => d.a && /PASS|GO/.test(d.a.verdict || '')).length, cond = docs.every((d) => d.a && /^COND/.test(d.a.verdict || ''));
    top = `<p class="sumline">${pos} of ${docs.length} seats positive · ${cond ? 'all conditional' : 'not all conditional'} · weighted decision <b class="acc">${E((f.decision.qualifier || '') + ' ' + (f.decision.word || ''))}</b></p>`;
  }
  const row = `<div class="vrow${wide && docs.length > 1 ? ' cols3' : ''}" style="${wide ? `grid-template-columns:repeat(${docs.length},minmax(0,1fr))` : ''}">` + JN(docs, (d) => {
    const fp = fPara(d.text), long = fp.length > 520 && !S.uc, mdl = (d.j && d.j.model) || (d.a && d.a.model),
      wt = d.a && d.a.weight != null && isF(+d.a.weight) ? `w ${Number(d.a.weight).toFixed(2)} · ` : '',
      dur = d.a && d.a.durS != null ? `${fDur(d.a.durS)} · ` : '';
    return `<div class="vcol" style="--lc:${d.col}">${D_('ln1', E(d.name))}${mdl ? `<div class="mono mist" style="font-size:15px">${E(mdl)}</div>` : ''}` +
      (d.a ? D_('meta', `${wt}${dur}${d.words.toLocaleString()} words · ${d.cites} citations`) + (d.a.verdict ? D_('vw', `✓ ${E(d.a.verdict)}`) : '') : '') +
      `<div class="fp md${long ? ' clamp' : ''}" style="font-size:17px">${d.loading ? S_('mist', 'Loading…') : inl(fp)}</div>${long ? TB('data-act="unclamp"', LX + ' rest of paragraph') : ''}</div>`;
  }) + '</div>',
    groups = aGrps(docs), canSbs = rw >= 1200 && docs.length > 1;
  if (!canSbs) S.sbs = false;
  if (groups.length < 2) S.al = false;
  const seats = `<div class="seats" role="toolbar" aria-label="Seats">${JN(docs, (d, i) => `<button class="s" style="color:${d.col}" data-seat="${i}"${ap(i === S.st && !S.sbs && !S.al)}>${E(d.name)}</button>`)}<span style="flex:1"></span>${canSbs ? TB(`data-act="sbs"${ap(S.sbs)}`, 'Side by side') : ''}${groups.length >= 2 ? TB(`data-act="align"${ap(S.al)}`, 'Align by section') : ''}</div>`;
  let full;
  if (S.al) full = `<div class="algn" style="--n:${docs.length}">${JN(docs, (d) => `<div class="kick" style="color:${d.col}">${E(d.name)}</div>`)}${JN(groups, (g) => D_('ah', E(g.h)) + JN(docs, (_, i) => D_('md', g.by[i] != null ? g.by[i] : EMP('not covered'))))}</div>`;
  else if (S.sbs) full = D_('sbs', JN(docs, (d) => D_('', `<div class="kick" style="color:${d.col}">${E(d.name)}</div>${D_('md', cDocH(d, f))}`)));
  else { const d = docs[S.st]; full = `<div class="md" id="cmpfull" style="font-size:${S.size}px">${cDocH(d, f)}</div>${d.a && d.j ? KK('Chat &amp; session', 'vk') + lBlk(d.a) : ''}`; }
  return D_('rhead', KK(`Compare · ${docs.length} ${f.decision && !ids ? 'verdicts' : 'outputs'}`) + `<h2>${f.decision && !ids ? 'Verdicts, side by side' : 'Compare outputs'}</h2>`) +
    top + KK('Verdict row', 'vk') + row + D_('fr', KK('Full text') + seats + `<div id="rmain">${full}</div>`) +
    D_('fr', KK('Cited files') + cTbl(docs.map((d) => ({ name: d.name, col: d.col, text: d.text })), f));
}
function normP(f, p) {
  const c = f && f.cited;
  let rel = c && c.alias ? c.alias[p] : undefined;
  if (rel === undefined && f && f.projectRoot && p.startsWith(f.projectRoot + '/')) rel = p.slice(f.projectRoot.length + 1);
  return rel === undefined ? p.replace(/^\.\//, '') : rel;
}
function resP(f, p) { const c = f && f.cited; if (!c) return null; const rel = normP(f, p); return c.files && c.files[rel] ? rel : null; }
function cTbl(docs, f) {
  const rows = {}, ms = docs.map((d) => String(d.text || '').match(CRE) || []);
  ms.forEach((arr, i) => arr.forEach((m) => { const rel = normP(f, m.replace(/:\d+(-\d+)?$/, '')); (rows[rel] = rows[rel] || docs.map(() => 0))[i]++; }));
  const ks = OK(rows).sort((a, b) => rows[b].reduce((s, x) => s + x, 0) - rows[a].reduce((s, x) => s + x, 0));
  if (!ks.length) return EMP('No file citations.');
  return D_('tws', `<table class="tbl"><thead><tr><th>file</th>${JN(docs, (d) => `<th class="num" style="color:${d.col || C.mist}">${E(d.name)}</th>`)}${docs.length > 1 ? '<th class="num">total</th>' : ''}</tr></thead><tbody>${JN(ks, (k) => {
    const first = (ms.map((arr) => arr.find((m) => normP(f, m.replace(/:\d+(-\d+)?$/, '')) === k)).find(Boolean) || '').match(/:(\d+)/);
    return `<tr class="clk" tabindex="0" data-file="${E(k)}" data-line="${first ? first[1] : 1}"><td class="mono" style="font-weight:500;overflow-wrap:anywhere">${E(k)}</td>${JN(rows[k], (n) => `<td class="num">${n || ''}</td>`)}${docs.length > 1 ? `<td class="num">${rows[k].reduce((s, x) => s + x, 0)}</td>` : ''}</tr>`;
  })}</tbody></table>`);
}
const POP = () => $('vpop');
function sPop(target, html, col, cls) {
  const p = POP();
  if (!p || !target) return;
  p.className = 'vpop on' + (cls ? ' ' + cls : ''); p.style.setProperty('--bc', col || C.mist); p.innerHTML = html;
  const r = rect(target), pw = p.offsetWidth, ph = p.offsetHeight, dw = DOC.documentElement.clientWidth,
    x = MX(8, MN(r.left + scrollX + r.width / 2 - pw / 2, dw - pw - 8 + scrollX));
  let y = r.top + scrollY - ph - 10; if (r.top - ph - 10 < 4) y = r.bottom + scrollY + 10;
  p.style.left = x + 'px'; p.style.top = y + 'px';
}
const hPop = () => { HC = null; const p = POP(); if (p) p.className = 'vpop'; },
  cCHtml = (lb, a, z, body) => `<div class="mono" style="overflow-wrap:anywhere">${E(lb)}</div>${D_('mist', a === z ? 'line ' + a : `lines ${a}–${z}`)}<div style="margin-top:6px">${body}</div>`,
  cLHtml = (res, a, z) => (!res || !res.ok ? D_('mist', E((res && res.error) || 'Not found under this project')) : JN(res.lines || [], (ln) => D_(`cl${ln.num >= a && ln.num <= z ? ' hit' : ''}`, `${String(ln.num).padStart(4)}  ${E(ln.text)}`)));
function cCard(b) {
  const f = FL.find((x) => x.id === b.dataset.flow) || flow();
  if (!f) return;
  const rel = resP(f, b.dataset.p), pNorm = rel || normP(f, b.dataset.p), a = +b.dataset.a, z = +b.dataset.b;
  if (rel && f.cited && f.cited.files[rel]) {
    const L = f.cited.files[rel].lines, s = MX(1, a - 4), lines = Array.from({ length: 9 }, (_, i) => s + i).filter((n) => L[n] != null).map((n) => ({ num: n, text: L[n] }));
    sPop(b, cCHtml(rel, a, z, cLHtml({ ok: true, lines }, a, z)), C.acc, 'cit'); return;
  }
  const fk = `${f.id}|${pNorm}|${a}|4`, cached = XC.get(fk);
  if (cached) { sPop(b, cCHtml(pNorm, a, z, cLHtml(cached, a, z)), C.acc, 'cit'); return; }
  HC = b; sPop(b, cCHtml(pNorm, a, z, D_('mist', 'Loading lines…')), C.acc, 'cit');
  getCit(f.id, pNorm, a, 4).then((res) => { if (HC === b && CL(POP()).contains('on')) sPop(b, cCHtml(pNorm, a, z, cLHtml(res, a, z)), C.acc, 'cit'); });
}
function nCard(g) {
  const f = flow(), a = agOf(f, g.dataset.agent);
  if (!a) return;
  const lb = aLb(a), tk = aTk(a), last = a.result || a.last || (a.verdict ? '✓ ' + a.verdict : '');
  sPop(g, D_('nm', E(lb)) + (tk && tk !== lb ? D_('mist cl2', E(tk)) : '') + D_('mono mist fx', `${emb(a.lab, a.state)}${E(labN(a.lab))}${a.model ? ' · ' + E(a.model) : ''}`) +
    `<div class="fx" style="color:${isStop(a) ? C.bad : C.mist}">${sIc(a.state, stCol(a))}${E(stWord(a))}</div>` +
    (a.durS != null || a.costUsd != null ? D_('mist', `${a.durS != null ? fDur(a.durS) + ' · ' : ''}${fUsd(a.costUsd, a.costEstimated)}`) : '') +
    (last ? D_('cl2', E(last)) : '') + D_('ft', 'Click to open · o opens chat'), labC(a.lab));
}