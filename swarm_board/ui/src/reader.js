// ===== reader.js: the Reader =====
const KINDN = { markdown: 'markdown', brief: 'brief', verdict: 'verdict', json: 'json', table: 'data', code: 'text', file: 'file', video: 'video', audio: 'audio', post: 'board post', result: 'result', transcript: 'transcript', cfile: 'source file' };
const ICON = { markdown: '▤', brief: '▤', verdict: '⚖', json: '{}', table: '{}', code: '▤', file: '▤', video: '▷', audio: '∿', post: '#', result: '▤', transcript: '☰' };
let READ = null;
function byLine(f, id) {
  const a = id && agentOf(f, id);
  return a ? `by <span style="color:${labC(a.lab)}">${esc(aLabel(a))}</span> · ${esc(labN(a.lab))}${a.model ? ` <span class="mono">${esc(a.model)}</span>` : ''}` : '';
}
function parseVerdict(o) {
  if (!o || o.body == null) return null;
  const b = String(o.body);
  try {
    const j = JSON.parse(b);
    if (j && typeof j === 'object' && typeof j.response === 'string') return j;
  } catch (e) { /* plain markdown verdict */ }
  return { response: b };
}
function codeView(text, start, hit, sparse) {
  let h = '', prev = 0;
  const rows = sparse ? Object.keys(sparse).map(Number).sort((a, b) => a - b).map((n) => [n, sparse[n]]) : String(text).split('\n').map((l, i) => [start + i, l]);
  rows.forEach(([n, l]) => {
    if (prev && n > prev + 1) h += `<div class="gap">lines ${prev + 1}–${n - 1} not in this sample</div>`;
    const on = hit && n >= hit[0] && n <= hit[1];
    h += `<div class="ln${on ? ' hit' : ''}" data-ln="${n}"><i>${n}</i><span>${esc(l)}</span></div>`;
    prev = n;
  });
  return `<div class="code${S.wrap ? ' wrap' : ''}">${h}</div>`;
}
function jsonTree(v, d) {
  if (v === null || typeof v !== 'object') return typeof v === 'string' ? `<span class="s">"${esc(v)}"</span>` : `<span class="n">${esc(String(v))}</span>`;
  const arr = Array.isArray(v), ks = arr ? v.map((_, i) => i) : Object.keys(v), shown = arr ? ks.slice(0, 20) : ks;
  const li = shown.map((k) => `<li><span class="k">${arr ? k : esc(k)}:</span> ${jsonTree(v[k], d + 1)}</li>`).join('');
  const more = arr && ks.length > 20 ? `<li><details class="lfold"><summary>${LX}${ks.length - 20} more</summary><ul>${ks.slice(20).map((k) => `<li><span class="k">${k}:</span> ${jsonTree(v[k], d + 1)}</li>`).join('')}</ul></details></li>` : '';
  const body = `<ul>${li}${more}</ul>`;
  return d < 2 ? (arr ? '[' : '{') + body + (arr ? ']' : '}') : `<details class="lfold" style="display:inline-block;margin:0"><summary>${LX}${arr ? ks.length + ' items' : ks.length + ' keys'}</summary>${body}</details>`;
}
function tableView(rows) {
  const keys = [...new Set(rows.flatMap((r) => Object.keys(r || {})))].filter((k) => k !== 'file');
  const num = keys.map((k) => rows.every((r) => typeof r[k] === 'number' || r[k] == null));
  const cf = flow();
  const cell = (r, k, i) => {
    const v = r[k];
    if (k === 'name' && /\.(wav|mp3|ogg|m4a)$/i.test(String(v)) && cf) return `<td>${esc(v)}<audio ${PRE} aria-label="Play ${esc(v)}" src="${esc(rawUrl(cf.id, r.file || v))}"></audio></td>`;
    return `<td${num[i] ? ' class="num"' : ''}>${esc(v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v))}</td>`;
  };
  return `<div class="tw"><table><thead><tr>${keys.map((k, i) => `<th${num[i] ? ' class="num"' : ''}>${esc(k)}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${keys.map((k, i) => cell(r, k, i)).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function linksBlock(a) {
  const L = (a && a.links) || [];
  if (!L.length) return NTE('No thread or session id recorded.');
  return L.map((l) => {
    if (l.kind === 'thread') return `<div class="lnk"><a href="${esc(l.href)}">${esc(l.label)} ↗</a><span class="id">${esc(l.href)}</span><button class="tbtn" data-copy="${esc(l.href)}">Copy link</button></div>`;
    if (l.kind === 'resume') return `<div class="lnk"><code>${esc(l.cmd)}</code><button class="tbtn" data-copy="${esc(l.cmd)}">Copy</button></div>`;
    return `<div class="lnk"><span class="id">session ${esc(l.id)}</span><button class="tbtn" data-copy="${esc(l.id)}">Copy session ID</button></div>`;
  }).join('');
}
function bodyFor(o, f) {
  if (o._err) return { html: EMP(esc(o._err)), heads: [], raw: '' };
  const pth = o.path || '', isVid = o.kind === 'video' || ((o.kind === 'file' || o.kind === 'code') && VID_EXT.test(pth)), isAud = !isVid && (o.kind === 'audio' || ((o.kind === 'file' || o.kind === 'code') && AUD_EXT.test(pth)));
  if (isVid || isAud) {
    const src = pth ? rawUrl(f.id, pth) : '';
    const tag = isVid ? `<video ${PRE} src="${esc(src)}"></video>` : `<audio ${PRE} src="${esc(src)}"></audio>`;
    return { html: src ? `<div class="media">${tag}<p class="meta mono">${esc(pth)}${o.size ? ' · ' + fmtKB(o.size) : ''}</p></div>` : EMP(`${isVid ? 'Video' : 'Audio'} path not available.`), heads: [], raw: pth };
  }
  if (o.kind === 'post') { const p = o.post, a = agentOf(f, p.author), r = md(p.text); return { html: `<div class="post"><div class="by" style="color:${labC(a && a.lab)}">${esc(a ? aLabel(a) : p.author)}<span class="t">#${p.id}${p.at ? ' · ' + esc(p.at) : ''}</span></div><div class="md">${r.html}</div></div>`, heads: [], raw: p.text }; }
  if (o.kind === 'cfile') {
    let sp = o.cf && o.cf.lines, note = o.cf ? `${esc(o.path)} (${o.cf.n} lines)` : '';
    if (!sp) {
      const fk = `${f.id}|${o.path}|${o.line}|40`, res = FILE_CACHE.get(fk);
      if (!res) {
        fetchCitation(f.id, o.path, o.line, 40).then(() => { if (flow() && flow().id === f.id && S.view === 'file' && S.arg === o.arg) preserveScroll(() => renderSide()); });
        return { html: NTE('Loading file excerpt…'), heads: [], raw: '' };
      }
      if (!res.ok) return { html: EMP(esc(res.error || 'Not found under this project.')), heads: [], raw: '' };
      sp = {}; (res.lines || []).forEach((ln) => { sp[ln.num] = ln.text; }); note = esc(res.path);
    }
    return { html: NTE(note) + codeView('', 0, o.hit, sp), heads: [], raw: Object.values(sp).join('\n') };
  }
  if (o.body == null && !o.messages) { loadOutput(f, o); return { html: NTE('Loading output…'), heads: [], raw: '' }; }
  const b = o.body || '';
  if (o.kind === 'transcript' && Array.isArray(o.messages) && o.messages.length) {
    const msgs = o.messages.slice(-100);
    return { html: msgsHTML(msgs, 0), heads: [], raw: b || msgs.map((m) => `[${m.role || ''}] ${m.text || m.content || ''}`).join('\n\n') };
  }
  if (o.kind === 'markdown' || o.kind === 'result' || o.kind === 'brief') { const r = md(b); return { html: (o.note ? NTE(esc(o.note)) : '') + `<div class="md">${r.html}</div>`, heads: r.heads, secs: r.secs, raw: b }; }
  if (o.kind === 'verdict') {
    const j = parseVerdict(o) || { response: '' }, r = md(j.response), a = agentOf(f, o.by);
    const rawFold = j.cli || j.model ? `<details class="lfold vk"><summary>${LX}Raw JSON</summary>${codeView(b, 1)}</details>` : '';
    return { html: `<div class="md">${r.html}</div><div class="vk kick">Chat &amp; session</div>${linksBlock(a)}${rawFold}`, heads: r.heads, secs: r.secs, raw: j.response, j };
  }
  if (o.kind === 'table') {
    try {
      const j = JSON.parse(b), tk = o.tableKey || (Array.isArray(j) ? null : Object.keys(j).find((k) => Array.isArray(j[k])));
      const rows = Array.isArray(j) ? j : (tk && j[tk]) || [], other = Array.isArray(j) ? [] : Object.keys(j).filter((k) => k !== tk);
      const meta = other.map((k) => `<div class="meta"><span class="mono">${esc(k)}</span> · ${esc(typeof j[k] === 'object' ? (j[k] && j[k].notes) || JSON.stringify(j[k]).slice(0, 120) + '…' : j[k])}</div>`).join('');
      return { html: `${meta}${NTE(rows.length + ' rows.')}<div class="md" data-tv="1">${S.raw ? codeView(b, 1) : tableView(rows)}</div>`, heads: [], raw: b, table: 1 };
    } catch (e) { return { html: codeView(b, 1), heads: [], raw: b }; }
  }
  if (o.kind === 'json') { let j; try { j = JSON.parse(b); } catch (e) { return { html: codeView(b, 1), heads: [], raw: b }; } return { html: `<div class="jt">${jsonTree(j, 0)}</div>`, heads: [], raw: b }; }
  const tr = o.truncated ? `${NTE(`Showing the first ${fmtKB(b.length)} of ${fmtKB(o.size)}.`)}<button class="tbtn" data-copy="${esc(pth)}">Copy path</button>` : '';
  return { html: tr + codeView(b, 1, o.hit), heads: [], raw: b };
}
function readerHTML(o, f) {
  const R = bodyFor(o, f);
  READ = { o, raw: R.raw, heads: R.heads, secs: R.secs || {} };
  const a = agentOf(f, o.by), kind = o.kind === 'verdict' ? 'verdict' : KINDN[o.kind] || o.kind;
  let meta = [esc(o.path || ''), byLine(f, o.by), esc(o.at || '')].filter(Boolean).join(' · ');
  if (o.kind === 'verdict' && R.j) {
    const j = R.j, w = j.response.split(/\s+/).filter(Boolean).length, c = (j.response.match(CITE_RE) || []).length;
    if (j.cli) meta = `<span style="color:${labC(a && a.lab)}">${esc(j.cli)}</span> · <span class="mono">${esc(j.model || '')}</span>${j.effort ? ` · ${esc(j.effort)} effort` : ''}${j.duration_seconds != null ? ` · ${j.duration_seconds.toFixed(1)} s` : ''} · ${w.toLocaleString()} words · ${c} citations`;
    else if (a) meta = `${byLine(f, o.by)}${a.durS != null ? ` · ${fmtDur(a.durS)}` : ''} · ${w.toLocaleString()} words · ${c} citations`;
  }
  const rw = geo().sideW, rail = R.heads.length >= 2 && rw >= 900;
  const ab = (k, t, p, al) => `<button class="tbtn" data-act="${k}"${p != null ? ` aria-pressed="${!!p}"` : ''}${al ? ` aria-label="${al}"` : ''}>${t}</button>`;
  const ol = (cls) => `<nav class="${cls}" aria-label="Outline"><ol>${R.heads.map((h) => `<li class="l${Math.min(h.lv, 4)}" data-oid="${h.id}"><button data-goto="${h.id}">${esc(h.text)}</button></li>`).join('')}</ol></nav>`;
  return `<div class="rhead"><div class="kick">${o.kind === 'post' ? 'Board post' : 'Output'} · ${esc(kind)}${o.size ? ' · ' + fmtKB(o.size) : ''}</div>
    <h2>${esc(o.title)}</h2><div class="meta">${meta}</div>
    <div class="acts">${ab('copy', 'Copy')}${ab('find', 'Find', S.find)}${ab('aminus', 'A−', null, 'Smaller text')}${ab('aplus', 'A+', null, 'Larger text')}${ab('wrap', 'Wrap', S.wrap)}${R.table ? ab('raw', 'Raw', S.raw) : ''}${R.heads.length >= 2 && !rail ? ab('outline', 'Outline', S.outline) : ''}${$('app').dataset.w === 'wide' ? ab('full', '⤢ Full', S.cover === 'side') : ''}${ab('close', '×', null, 'Close')}</div>
    <div id="findrow"></div>${S.outline && !rail ? `<div class="ofold lfb">${ol('')}</div>` : ''}<div class="prog"><i id="progi"></i></div></div>
    <div class="rbody${rail ? ' rail' : ''}" id="rbody" style="--t-read:${S.size}px">${rail ? ol('orail') : ''}<div id="rmain">${R.html}
    <div class="endnav">${ab('top', '↑ Top')}${ab('close', '← Back')}</div></div></div>`;
}
function afterReader(f) {
  const m = $('rmain');
  if (!m) return;
  const n = citeify(m, f);
  if (READ && READ.o.post) mentionify(m, f);
  if (n >= 5 && READ.o.kind !== 'cfile') q1('.endnav', m).insertAdjacentHTML('beforebegin', `<div class="vk kick">Cited files</div>` + citedTable([{ name: READ.o.title, text: READ.raw }], f));
  if (S.find) openFind();
  const hit = q1('.ln.hit', m);
  if (hit) requestAnimationFrame(() => scrollToEl(hit));
}
