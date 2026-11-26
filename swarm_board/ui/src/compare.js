// ===== compare.js: 3-layer Compare, cited files, hover cards =====
function cmpDocs(f, ids) {
  const outs = ids ? ids.map((i) => outOf(f, i)).filter(Boolean) : (f.outputs || []).filter((o) => o.kind === 'verdict');
  return outs.map((o) => {
    const need = o.body == null && !o._loaded && !o._err && !/^(video|audio|post)$/.test(o.kind);
    if (need) loadOutput(f, o);
    const a = agentOf(f, o.by), j = o.kind === 'verdict' ? parseVerdict(o) : null;
    const text = j ? j.response : o.kind === 'post' ? o.post.text : o.body || '';
    return { o, a, j, text, loading: need, name: a && o.kind === 'verdict' ? labN(a.lab) : o.title, col: a ? labC(a.lab) : C.ink, words: text.split(/\s+/).filter(Boolean).length, cites: (text.match(CITE_RE) || []).length };
  });
}
const firstPara = (t) => String(t || '').trim().split(/\n\s*\n/)[0].replace(/^\s*\d+[.)]\s+/, '');
function normH(t) { return new Set(t.toLowerCase().replace(/[\d]+[.)]?/g, ' ').replace(/[^a-z\s]/g, ' ').split(/\s+/).filter((w) => w.length > 1 || w === 'a')); }
function jac(a, b) { let i = 0; a.forEach((x) => b.has(x) && i++); return i / (a.size + b.size - i || 1); }
function sections(t) {
  const r = md(t), d = CE('div'), out = [];
  d.innerHTML = r.html;
  let cur = null;
  [...d.children].forEach((el) => {
    const leads = el.matches('ol,ul') ? [...el.children].filter((li) => li.dataset.lead) : [];
    if (leads.length) { leads.forEach((li) => { const hd = r.heads.find((h) => h.id === li.id); if (hd) out.push({ h: hd.text, html: li.innerHTML }); }); cur = null; return; }
    const h = el.id && r.heads.find((x) => x.id === el.id);
    if (h) { cur = { h: h.text, html: el.dataset.lead ? el.outerHTML : '' }; out.push(cur); } else if (cur) cur.html += el.outerHTML;
  });
  return out;
}
function alignGroups(docs) {
  const cl = [];
  docs.forEach((d, di) => sections(d.text).forEach((s) => {
    const k = normH(s.h);
    let c = cl.find((x) => jac(x.k, k) >= 0.5 && !x.by[di]);
    if (!c) cl.push((c = { k, h: s.h, by: {} }));
    c.by[di] = s.html;
  }));
  return cl.filter((c) => Object.keys(c.by).length >= 2);
}
function cmpDocHTML(d, f) {
  if (d.o.kind === 'video' || d.o.kind === 'audio' || ((d.o.kind === 'file' || d.o.kind === 'code') && (VID_EXT.test(d.o.path || '') || AUD_EXT.test(d.o.path || '')))) return bodyFor(d.o, f).html;
  if (d.loading) return NTE('Loading output…');
  if (d.o._err) return EMP(esc(d.o._err));
  return d.o.kind === 'code' ? codeView(d.text, 1) : md(d.text).html;
}
function compareHTML(f, ids) {
  const docs = cmpDocs(f, ids), rw = geo().sideW, wide = rw >= 900;
  if (!docs.length) return `<div class="rhead"><div class="kick">Compare</div><h2>Compare outputs</h2></div>${EMP('No outputs available to compare.')}`;
  S.seat = Math.min(S.seat, docs.length - 1);
  let top = '';
  if (f.decision && !ids) {
    const pos = docs.filter((d) => d.a && /PASS|GO/.test(d.a.verdict || '')).length, cond = docs.every((d) => d.a && /^COND/.test(d.a.verdict || ''));
    top = `<p class="sumline">${pos} of ${docs.length} seats positive · ${cond ? 'all conditional' : 'not all conditional'} · weighted decision <b class="acc">${esc((f.decision.qualifier || '') + ' ' + (f.decision.word || ''))}</b></p>`;
  }
  const row = `<div class="vrow${wide && docs.length > 1 ? ' cols3' : ''}" style="${wide ? `grid-template-columns:repeat(${docs.length},minmax(0,1fr))` : ''}">` + docs.map((d) => {
    const fp = firstPara(d.text), long = fp.length > 520 && !S.unclamp;
    const mdl = (d.j && d.j.model) || (d.a && d.a.model);
    const wt = d.a && d.a.weight != null ? `w ${d.a.weight.toFixed(2)} · ` : '';
    const dur = d.a && d.a.durS != null ? `${fmtDur(d.a.durS)} · ` : '';
    return `<div class="vcol" style="--lc:${d.col}"><div class="ln1">${esc(d.name)}</div>${mdl ? `<div class="mono mist" style="font-size:15px">${esc(mdl)}</div>` : ''}
      ${d.a ? `<div class="meta">${wt}${dur}${d.words.toLocaleString()} words · ${d.cites} citations</div>${d.a.verdict ? `<div class="vw">✓ ${esc(d.a.verdict)}</div>` : ''}` : ''}
      <div class="fp md${long ? ' clamp' : ''}" style="font-size:17px">${d.loading ? '<span class="mist">Loading…</span>' : inl(fp)}</div>${long ? `<button class="tbtn" data-act="unclamp">${LX} rest of paragraph</button>` : ''}</div>`;
  }).join('') + `</div>`;
  const groups = alignGroups(docs), canSbs = rw >= 1200 && docs.length > 1;
  if (!canSbs) S.sbs = false;
  if (groups.length < 2) S.align = false;
  const seats = `<div class="seats" role="toolbar" aria-label="Seats">${docs.map((d, i) => `<button class="s" style="color:${d.col}" data-seat="${i}" aria-pressed="${i === S.seat && !S.sbs && !S.align}">${esc(d.name)}</button>`).join('')}
    <span style="flex:1"></span>${canSbs ? `<button class="tbtn" data-act="sbs" aria-pressed="${S.sbs}">Side by side</button>` : ''}${groups.length >= 2 ? `<button class="tbtn" data-act="align" aria-pressed="${S.align}">Align by section</button>` : ''}</div>`;
  let full;
  if (S.align) full = `<div class="algn" style="--n:${docs.length}">${docs.map((d) => `<div class="kick" style="color:${d.col}">${esc(d.name)}</div>`).join('')}${groups.map((g) => `<div class="ah">${esc(g.h)}</div>` + docs.map((_, i) => `<div class="md">${g.by[i] != null ? g.by[i] : EMP('not covered')}</div>`).join('')).join('')}</div>`;
  else if (S.sbs) full = `<div class="sbs">${docs.map((d) => `<div><div class="kick" style="color:${d.col}">${esc(d.name)}</div><div class="md">${cmpDocHTML(d, f)}</div></div>`).join('')}</div>`;
  else { const d = docs[S.seat]; full = `<div class="md" id="cmpfull" style="font-size:${S.size}px">${cmpDocHTML(d, f)}</div>${d.a && d.j ? `<div class="vk kick">Chat &amp; session</div>${linksBlock(d.a)}` : ''}`; }
  return `<div class="rhead"><div class="kick">Compare · ${docs.length} ${f.decision && !ids ? 'verdicts' : 'outputs'}</div><h2>${f.decision && !ids ? 'Verdicts, side by side' : 'Compare outputs'}</h2></div>
    ${top}<div class="kick vk">Verdict row</div>${row}<div class="fr"><div class="kick">Full text</div>${seats}<div id="rmain">${full}</div></div>
    <div class="fr"><div class="kick">Cited files</div>${citedTable(docs.map((d) => ({ name: d.name, col: d.col, text: d.text })), f)}</div>`;
}
function normCitePath(f, p) {
  const c = f && f.cited;
  let rel = c && c.alias ? c.alias[p] : undefined;
  if (rel === undefined && f && f.projectRoot && p.startsWith(f.projectRoot + '/')) rel = p.slice(f.projectRoot.length + 1);
  if (rel === undefined) rel = p.replace(/^\.\//, '');
  return rel;
}
function resolveCite(f, p) {
  const c = f && f.cited;
  if (!c) return null;
  const rel = normCitePath(f, p);
  return c.files && c.files[rel] ? rel : null;
}
function citedTable(docs, f) {
  const rows = {}, ms = docs.map((d) => String(d.text || '').match(CITE_RE) || []);
  ms.forEach((arr, i) => arr.forEach((m) => { const p = m.replace(/:\d+(-\d+)?$/, ''), rel = normCitePath(f, p); (rows[rel] = rows[rel] || docs.map(() => 0))[i]++; }));
  const ks = Object.keys(rows).sort((a, b) => rows[b].reduce((s, x) => s + x, 0) - rows[a].reduce((s, x) => s + x, 0));
  if (!ks.length) return EMP('No file citations.');
  return `<div class="tws"><table class="tbl"><thead><tr><th>file</th>${docs.map((d) => `<th class="num" style="color:${d.col || C.mist}">${esc(d.name)}</th>`).join('')}${docs.length > 1 ? '<th class="num">total</th>' : ''}</tr></thead><tbody>${ks.map((k) => {
    const first = (ms.map((arr) => arr.find((m) => normCitePath(f, m.replace(/:\d+(-\d+)?$/, '')) === k)).find(Boolean) || '').match(/:(\d+)/);
    return `<tr class="clk" tabindex="0" data-file="${esc(k)}" data-line="${first ? first[1] : 1}"><td class="mono" style="font-weight:500;overflow-wrap:anywhere">${esc(k)}</td>${rows[k].map((n) => `<td class="num">${n || ''}</td>`).join('')}${docs.length > 1 ? `<td class="num">${rows[k].reduce((s, x) => s + x, 0)}</td>` : ''}</tr>`;
  }).join('')}</tbody></table></div>`;
}
const POP = () => $('vpop');
function showPop(target, html, col, cls) {
  const p = POP();
  p.className = 'vpop on' + (cls ? ' ' + cls : '');
  p.style.setProperty('--bc', col || C.mist);
  p.innerHTML = html;
  const r = target.getBoundingClientRect(), pw = p.offsetWidth, ph = p.offsetHeight, dw = document.documentElement.clientWidth;
  let x = Math.max(8, Math.min(r.left + scrollX + r.width / 2 - pw / 2, dw - pw - 8 + scrollX)), y = r.top + scrollY - ph - 10;
  if (r.top - ph - 10 < 4) y = r.bottom + scrollY + 10;
  p.style.left = x + 'px'; p.style.top = y + 'px';
}
function hidePop() { HOVER_CITE = null; POP().className = 'vpop'; }
function citeCardHTML(label, a, z, body) {
  return `<div class="mono" style="overflow-wrap:anywhere">${esc(label)}</div><div class="mist">${a === z ? 'line ' + a : `lines ${a}–${z}`}</div><div style="margin-top:6px">${body}</div>`;
}
function citeLinesHTML(res, a, z) {
  if (!res || !res.ok) return `<div class="mist">${esc((res && res.error) || 'Not found under this project')}</div>`;
  return (res.lines || []).map((ln) => `<div class="cl${ln.num >= a && ln.num <= z ? ' hit' : ''}">${String(ln.num).padStart(4)}  ${esc(ln.text)}</div>`).join('');
}
function citeCard(b) {
  const f = FL.find((x) => x.id === b.dataset.flow) || flow();
  if (!f) return;
  const rel = resolveCite(f, b.dataset.p), pNorm = rel || normCitePath(f, b.dataset.p), a = +b.dataset.a, z = +b.dataset.b;
  if (rel && f.cited && f.cited.files[rel]) {
    const L = f.cited.files[rel].lines, s = Math.max(1, a - 4);
    const lines = Array.from({ length: 9 }, (_, i) => s + i).filter((n) => L[n] != null).map((n) => ({ num: n, text: L[n] }));
    showPop(b, citeCardHTML(rel, a, z, citeLinesHTML({ ok: true, lines }, a, z)), C.acc, 'cit');
    return;
  }
  const fk = `${f.id}|${pNorm}|${a}|4`, cached = FILE_CACHE.get(fk);
  if (cached) { showPop(b, citeCardHTML(pNorm, a, z, citeLinesHTML(cached, a, z)), C.acc, 'cit'); return; }
  HOVER_CITE = b;
  showPop(b, citeCardHTML(pNorm, a, z, '<div class="mist">Loading lines…</div>'), C.acc, 'cit');
  fetchCitation(f.id, pNorm, a, 4).then((res) => {
    if (HOVER_CITE === b && POP().classList.contains('on')) showPop(b, citeCardHTML(pNorm, a, z, citeLinesHTML(res, a, z)), C.acc, 'cit');
  });
}
function nodeCard(g) {
  const f = flow(), a = agentOf(f, g.dataset.agent);
  if (!a) return;
  const lb = aLabel(a), tk = aTask(a), last = a.result || a.last || (a.verdict ? '✓ ' + a.verdict : '');
  showPop(g, `<div class="nm">${esc(lb)}</div>${tk && tk !== lb ? `<div class="mist cl2">${esc(tk)}</div>` : ''}<div class="mono mist fx">${emb(a.lab, a.state)}${esc(labN(a.lab))}${a.model ? ' · ' + esc(a.model) : ''}</div>
    <div class="fx" style="color:${isStop(a) ? C.bad : C.mist}">${sIcon(a.state, stateCol(a))}${esc(stateWord(a))}</div>
    ${a.durS != null || a.costUsd != null ? `<div class="mist">${a.durS != null ? fmtDur(a.durS) + ' · ' : ''}${fmtCost(a.costUsd, a.costEstimated)}</div>` : ''}
    ${last ? `<div class="cl2">${esc(last)}</div>` : ''}<div class="ft">Click to open · o opens chat</div>`, labC(a.lab));
}
