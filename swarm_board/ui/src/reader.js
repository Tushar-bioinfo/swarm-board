// ===== reader.js =====
const KN = { markdown: 'markdown', brief: 'brief', verdict: 'verdict', json: 'json', table: 'data', code: 'text', file: 'file', video: 'video', audio: 'audio', post: 'board post', result: 'result', transcript: 'transcript', cfile: 'source file' },
  IC = { markdown: '▤', brief: '▤', verdict: '⚖', json: '{}', table: '{}', code: '▤', file: '▤', video: '▷', audio: '∿', post: '#', result: '▤', transcript: '☰' };
let RD_ = null;
function byL(f, id) {
  const a = id && agOf(f, id);
  return a ? `by <span style="color:${labC(a.lab)}">${E(aLb(a))}</span> · ${E(labN(a.lab))}${a.model ? ` ${S_('mono', E(a.model))}` : ''}` : '';
}
function pVerd(o) {
  if (!o || o.body == null) return null;
  const b = String(o.body);
  try { const j = Jp(b); if (j && typeof j === 'object' && typeof j.response === 'string') return j; } catch (e) {}
  return { response: b };
}
function cView(text, start, hit, sparse) {
  let h = '', prev = 0;
  const rows = sparse ? OK(sparse).map(Number).sort((a, b) => a - b).map((n) => [n, sparse[n]]) : String(text).split('\n').map((l, i) => [start + i, l]);
  rows.forEach(([n, l]) => {
    if (prev && n > prev + 1) h += D_('gap', `lines ${prev + 1}–${n - 1} not in this sample`);
    h += `<div class="ln${hit && n >= hit[0] && n <= hit[1] ? ' hit' : ''}" data-ln="${n}"><i>${n}</i><span>${E(l)}</span></div>`;
    prev = n;
  });
  return D_(`code${S.wr ? ' wrap' : ''}`, h);
}
function jTree(v, d) {
  if (v === null || typeof v !== 'object') return typeof v === 'string' ? S_('s', `"${E(v)}"`) : S_('n', E(String(v)));
  const arr = isArr(v), ks = arr ? v.map((_, i) => i) : OK(v), shown = arr ? ks.slice(0, 20) : ks,
    li = JN(shown, (k) => `<li>${S_('k', (arr ? k : E(k)) + ':')} ${jTree(v[k], d + 1)}</li>`),
    more = arr && ks.length > 20 ? `<li><details class="lfold"><summary>${LX}${ks.length - 20} more</summary><ul>${JN(ks.slice(20), (k) => `<li>${S_('k', k + ':')} ${jTree(v[k], d + 1)}</li>`)}</ul></details></li>` : '',
    body = `<ul>${li}${more}</ul>`;
  return d < 2 ? (arr ? '[' : '{') + body + (arr ? ']' : '}') : `<details class="lfold" style="display:inline-block;margin:0"><summary>${LX}${arr ? ks.length + ' items' : ks.length + ' keys'}</summary>${body}</details>`;
}
function tView(rows) {
  const keys = [...new Set(rows.flatMap((r) => OK(r || {})))].filter((k) => k !== 'file'),
    num = keys.map((k) => rows.every((r) => typeof r[k] === 'number' || r[k] == null)), cf = flow(),
    cell = (r, k, i) => {
      const v = r[k];
      if (k === 'name' && /\.(wav|mp3|ogg|m4a)$/i.test(String(v)) && cf) return `<td>${E(v)}<audio ${PRE} aria-label="Play ${E(v)}" src="${E(rUrl(cf.id, r.file || v))}"></audio></td>`;
      return `<td${num[i] ? ' class="num"' : ''}>${E(v == null ? '' : typeof v === 'object' ? Js(v) : String(v))}</td>`;
    };
  return D_('tw', `<table><thead><tr>${JN(keys, (k, i) => `<th${num[i] ? ' class="num"' : ''}>${E(k)}</th>`)}</tr></thead><tbody>${JN(rows, (r) => `<tr>${JN(keys, (k, i) => cell(r, k, i))}</tr>`)}</tbody></table>`);
}
function lBlk(a) {
  const L = (a && a.links) || [];
  if (!L.length) return NTE('No thread or session id recorded.');
  return JN(L, (l) => {
    if (l.kind === 'thread') { const u = safeUrl(l.href); return D_('lnk', u ? `<a href="${E(u)}">${E(l.label)} ↗</a>${S_('id', E(u))}${TB(`data-copy="${E(u)}"`, 'Copy link')}` : S_('id', E(l.label || l.id || l.href))); }
    if (l.kind === 'resume') return D_('lnk', `<code>${E(l.cmd)}</code>${TB(`data-copy="${E(l.cmd)}"`, 'Copy')}`);
    return D_('lnk', S_('id', `session ${E(l.id)}`) + TB(`data-copy="${E(l.id)}"`, 'Copy session ID'));
  });
}
function bFor(o, f) {
  if (o._err) return { html: EMP(E(o._err)), heads: [], raw: '' };
  const pth = o.path || '', isV = o.kind === 'video' || ((o.kind === 'file' || o.kind === 'code') && VEX.test(pth)), isA = !isV && (o.kind === 'audio' || ((o.kind === 'file' || o.kind === 'code') && AEX.test(pth)));
  if (isV || isA) {
    const src = pth ? rUrl(f.id, pth) : '', tag = isV ? `<video ${PRE} src="${E(src)}"></video>` : `<audio ${PRE} src="${E(src)}"></audio>`;
    return { html: src ? D_('media', `${tag}<p class="meta mono">${E(pth)}${o.size ? ' · ' + fKB(o.size) : ''}</p>`) : EMP(`${isV ? 'Video' : 'Audio'} path not available.`), heads: [], raw: pth };
  }
  if (o.kind === 'post') { const p = o.post, a = agOf(f, p.author), r = md(p.text); return { html: D_('post', `<div class="by" style="color:${labC(a && a.lab)}">${E(a ? aLb(a) : p.author)}${S_('t', `#${p.id}${p.at ? ' · ' + E(p.at) : ''}`)}</div>${D_('md', r.html)}`), heads: [], raw: p.text }; }
  if (o.kind === 'cfile') {
    let sp = o.cf && o.cf.lines, note = o.cf ? `${E(o.path)} (${o.cf.n} lines)` : '';
    if (!sp) {
      const fk = `${f.id}|${o.path}|${o.line}|40`, res = XC.get(fk);
      if (!res) { getCit(f.id, o.path, o.line, 40).then(() => { if (flow() && flow().id === f.id && S.view === 'file' && S.arg === o.arg) keepSc(() => rSide()); }); return { html: NTE('Loading file excerpt…'), heads: [], raw: '' }; }
      if (!res.ok) return { html: EMP(E(res.error || 'Not found under this project.')), heads: [], raw: '' };
      sp = {}; (res.lines || []).forEach((ln) => { sp[ln.num] = ln.text; }); note = E(res.path);
    }
    return { html: NTE(note) + cView('', 0, o.hit, sp), heads: [], raw: Object.values(sp).join('\n') };
  }
  if (o.body == null && !o.messages) { ldOut(f, o); return { html: NTE('Loading output…'), heads: [], raw: '' }; }
  const b = o.body || '';
  if (o.kind === 'transcript' && isArr(o.messages) && o.messages.length) {
    const msgs = o.messages.slice(-100);
    return { html: mHtml(msgs, 0), heads: [], raw: b || JN(msgs, (m) => `[${m.role || ''}] ${m.text || m.content || ''}`, '\n\n') };
  }
  if (/^(markdown|result|brief)$/.test(o.kind)) { const r = md(b); return { html: (o.note ? NTE(E(o.note)) : '') + D_('md', r.html), heads: r.heads, secs: r.secs, raw: b }; }
  if (o.kind === 'verdict') {
    const j = pVerd(o) || { response: '' }, r = md(j.response), a = agOf(f, o.by),
      rawFold = j.cli || j.model ? `<details class="lfold vk"><summary>${LX}Raw JSON</summary>${cView(b, 1)}</details>` : '';
    return { html: D_('md', r.html) + KK('Chat &amp; session', 'vk') + lBlk(a) + rawFold, heads: r.heads, secs: r.secs, raw: j.response, j };
  }
  if (o.kind === 'table') {
    try {
      const j = Jp(b), tk = o.tableKey || (isArr(j) ? null : OK(j).find((k) => isArr(j[k]))), rows = isArr(j) ? j : (tk && j[tk]) || [],
        other = isArr(j) ? [] : OK(j).filter((k) => k !== tk),
        meta = JN(other, (k) => D_('meta', `${S_('mono', E(k))} · ${E(typeof j[k] === 'object' ? (j[k] && j[k].notes) || Js(j[k]).slice(0, 120) + '…' : j[k])}`));
      return { html: `${meta}${NTE(rows.length + ' rows.')}<div class="md" data-tv="1">${S.raw ? cView(b, 1) : tView(rows)}</div>`, heads: [], raw: b, table: 1 };
    } catch (e) { return { html: cView(b, 1), heads: [], raw: b }; }
  }
  if (o.kind === 'json') { let j; try { j = Jp(b); } catch (e) { return { html: cView(b, 1), heads: [], raw: b }; } return { html: D_('jt', jTree(j, 0)), heads: [], raw: b }; }
  const tr = o.truncated ? `${NTE(`Showing the first ${fKB(b.length)} of ${fKB(o.size)}.`)}${TB(`data-copy="${E(pth)}"`, 'Copy path')}` : '';
  return { html: tr + cView(b, 1, o.hit), heads: [], raw: b };
}
function rHtml(o, f) {
  const R = bFor(o, f);
  RD_ = { o, raw: R.raw, heads: R.heads, secs: R.secs || {} };
  const a = agOf(f, o.by), kind = o.kind === 'verdict' ? 'verdict' : KN[o.kind] || o.kind;
  let meta = [E(o.path || ''), byL(f, o.by), E(o.at || '')].filter(Boolean).join(' · ');
  if (o.kind === 'verdict' && R.j) {
    const j = R.j, w = j.response.split(/\s+/).filter(Boolean).length, c = (j.response.match(CRE) || []).length;
    if (j.cli) meta = `<span style="color:${labC(a && a.lab)}">${E(j.cli)}</span> · ${S_('mono', E(j.model || ''))}${j.effort ? ` · ${E(j.effort)} effort` : ''}${j.duration_seconds != null && isF(+j.duration_seconds) ? ` · ${Number(j.duration_seconds).toFixed(1)} s` : ''} · ${w.toLocaleString()} words · ${c} citations`;
    else if (a) meta = `${byL(f, o.by)}${a.durS != null ? ` · ${fDur(a.durS)}` : ''} · ${w.toLocaleString()} words · ${c} citations`;
  }
  const rw = geo().sideW, rail = R.heads.length >= 2 && rw >= 900,
    ab = (k, t, p, al) => TB(`data-act="${k}"${p != null ? ap(p) : ''}${al ? ` aria-label="${al}"` : ''}`, t),
    ol = (cls) => `<nav class="${cls}" aria-label="Outline"><ol>${JN(R.heads, (h) => `<li class="l${MN(h.lv, 4)}" data-oid="${h.id}"><button data-goto="${h.id}">${E(h.text)}</button></li>`)}</ol></nav>`;
  return D_('rhead', KK(`${o.kind === 'post' ? 'Board post' : 'Output'} · ${E(kind)}${o.size ? ' · ' + fKB(o.size) : ''}`) +
    `<h2>${E(o.title)}</h2>${D_('meta', meta)}` +
    D_('acts', ab('copy', 'Copy') + ab('find', 'Find', S.fn) + ab('aminus', 'A−', null, 'Smaller text') + ab('aplus', 'A+', null, 'Larger text') + ab('wrap', 'Wrap', S.wr) + (R.table ? ab('raw', 'Raw', S.raw) : '') + (R.heads.length >= 2 && !rail ? ab('outline', 'Outline', S.ol) : '') + ($('app').dataset.w === 'wide' ? ab('full', '⤢ Full', S.cv === 'side') : '') + ab('close', '×', null, 'Close')) +
    `<div id="findrow"></div>${S.ol && !rail ? D_('ofold lfb', ol('')) : ''}${D_('prog', '<i id="progi"></i>')}`) +
    `<div class="rbody${rail ? ' rail' : ''}" id="rbody" style="--t-read:${S.size}px">${rail ? ol('orail') : ''}<div id="rmain">${R.html}${D_('endnav', ab('top', '↑ Top') + ab('close', '← Back'))}</div></div>`;
}
function afRd(f) {
  const m = $('rmain');
  if (!m) return;
  const n = cify(m, f);
  if (RD_ && RD_.o.post) mify(m, f);
  const en = q1('.endnav', m);
  if (n >= 5 && RD_.o.kind !== 'cfile' && en) en.insertAdjacentHTML('beforebegin', KK('Cited files', 'vk') + cTbl([{ name: RD_.o.title, text: RD_.raw }], f));
  if (S.fn) oFind();
  const hit = q1('.ln.hit', m);
  if (hit) RAF(() => scrTo(hit));
}