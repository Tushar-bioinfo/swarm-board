// ===== md.js: escaped-only markdown -> {html, heads, secs} =====
const OKURL = /^(https?:\/\/|t3-thread:\/\/)/i;
const NUMC = /^[-+$~≈<>]?\s?[\d][\d,.]*\s?(%|x|×|s|ms|min|MB|KB|GB|M|K|B|h)?$/;
const VID_EXT = /\.(mp4|webm|mov)(\?.*)?$/i, AUD_EXT = /\.(wav|mp3|ogg|m4a)(\?.*)?$/i;
function inl(s) {
  const code = [];
  s = String(s == null ? '' : s).replace(/`([^`]+)`/g, (m, c) => { code.push(`<code>${esc(c)}</code>`); return '\u0000' + (code.length - 1) + '\u0000'; });
  s = esc(s);
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, a, u) => {
    const raw = u.replace(/&amp;/g, '&'), cf = flow();
    const src = /^https?:\/\//i.test(raw) ? raw : raw.startsWith('/api/raw') ? raw : cf ? rawUrl(cf.id, raw) : '';
    if (VID_EXT.test(raw) && src) return `<div class="media"><video ${PRE} src="${esc(src)}" aria-label="${a || 'Video'}"></video></div>`;
    if (AUD_EXT.test(raw) && src) return `<div class="media"><audio ${PRE} src="${esc(src)}" aria-label="${a || 'Audio'}"></audio></div>`;
    return `<span class="mist">[image: ${a}]</span>`;
  });
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^)]*&quot;)?\)/g, (m, t, u) => {
    const raw = u.replace(/&amp;/g, '&');
    return OKURL.test(raw) ? `<a href="${u}" target="_blank" rel="noopener">${t}</a>` : t;
  });
  s = s.replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, (m, u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
  s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*\w])\*(?=\S)([^*]*?\S)\*(?!\*)/g, '$1<em>$2</em>');
  s = s.replace(/( {2,}|\\)\n/g, '<br>').replace(/&lt;br\s*\/?&gt;/gi, '<br>');
  return s.replace(/\u0000(\d+)\u0000/g, (m, i) => code[+i]);
}
const slug = (t, n) => 'h-' + t.toLowerCase().replace(/<[^>]+>/g, '').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) + '-' + n;
const plain = (h) => h.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
const LEAD = /^\*\*([^*]{2,40}?)[:.]?\*\*\s*[:.—-]/;
function md(src) {
  const L = String(src == null ? '' : src).replace(/\r\n?/g, '\n').split('\n'), heads = [], secs = {};
  let n = 0;
  function lead(raw) {
    const m = raw.match(LEAD);
    if (!m) return '';
    const id = slug(m[1], ++n);
    heads.push({ id, lv: 5, text: m[1].trim() });
    return ` id="${id}" data-lead="1"`;
  }
  function blocks(ls, top) {
    let i = 0, o = '';
    const isList = (l) => /^\s*([-*+]|\d+[.)])\s+/.test(l), isHr = (l) => /^ {0,3}([-*_])( *\1){2,} *$/.test(l);
    const isTbl = (k) => /\|/.test(ls[k] || '') && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(ls[k + 1] || '');
    while (i < ls.length) {
      const l = ls[i];
      if (!l.trim()) { i++; continue; }
      let m;
      if ((m = l.match(/^\s*(```+|~~~+)\s*([\w+-]*)/))) {
        const f = m[1], lang = m[2], body = [];
        i++;
        while (i < ls.length && !ls[i].trim().startsWith(f)) body.push(ls[i++]);
        i++;
        o += `<pre data-raw="${esc(body.join('\n'))}">${lang ? `<span class="lang">${esc(lang)}</span>` : ''}<button class="tbtn cp" data-copy="pre">Copy</button><code>${esc(body.join('\n'))}</code></pre>`;
        continue;
      }
      if ((m = l.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/))) {
        const lv = m[1].length, h = inl(m[2]), id = slug(h, ++n);
        if (lv <= 3) heads.push({ id, lv, text: plain(h) });
        let j = i + 1;
        if (top && (lv === 2 || lv === 3)) { while (j < ls.length && !(ls[j].match(/^(#{1,3})\s/) && ls[j].match(/^#+/)[0].length <= lv)) j++; secs[id] = ls.slice(i, j).join('\n'); }
        o += `<h${Math.min(lv, 4)} id="${id}">${h}${secs[id] ? `<button class="scp" data-sec="${id}">Copy section</button>` : ''}</h${Math.min(lv, 4)}>`;
        i++; continue;
      }
      if (isHr(l)) { o += '<hr>'; i++; continue; }
      if (isTbl(i)) {
        const row = (r) => r.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map((c) => c.trim());
        const hd = row(ls[i]), al = row(ls[i + 1]).map((c) => (/^:-+:$/.test(c) ? 'center' : /-:$/.test(c) ? 'right' : ''));
        const rows = [];
        i += 2;
        while (i < ls.length && /\|/.test(ls[i]) && ls[i].trim()) rows.push(row(ls[i++]));
        const num = hd.map((_, k) => { const cs = rows.map((r) => (r[k] || '').replace(/[`*]/g, '').trim()).filter(Boolean); return cs.length && cs.filter((c) => NUMC.test(c)).length / cs.length >= 0.8; });
        const cell = (t, k, c) => `<${t}${num[k] ? ' class="num"' : al[k] ? ` style="text-align:${al[k]}"` : ''}>${inl(c || '')}</${t}>`;
        o += `<div class="tw"><table><thead><tr>${hd.map((c, k) => cell('th', k, c)).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${hd.map((_, k) => cell('td', k, r[k])).join('')}</tr>`).join('')}</tbody></table></div>`;
        continue;
      }
      if (/^\s*>/.test(l)) {
        const q = [];
        while (i < ls.length && /^\s*>/.test(ls[i])) q.push(ls[i++].replace(/^\s*>\s?/, ''));
        o += `<blockquote>${blocks(q, false)}</blockquote>`;
        continue;
      }
      if (isList(l)) {
        const base = l.match(/^\s*/)[0].length, ord = /^\s*\d/.test(l), items = [];
        while (i < ls.length) {
          const x = ls[i], ind = x.match(/^\s*/)[0].length;
          if (!x.trim()) { if (ls[i + 1] && (isList(ls[i + 1]) || /^\s{2,}\S/.test(ls[i + 1])) && ls[i + 1].match(/^\s*/)[0].length >= base) { i++; continue; } break; }
          if (ind === base && isList(x)) { items.push([x.replace(/^\s*([-*+]|\d+[.)])\s+/, '')]); i++; continue; }
          if (ind > base && items.length) { items[items.length - 1].push(x.slice(Math.min(ind, base + 2))); i++; continue; }
          if (ind < base || isHr(x) || /^#/.test(x)) break;
          if (items.length) { items[items.length - 1].push(x); i++; continue; }
          break;
        }
        const start = ord ? parseInt(l.trim(), 10) : 1;
        o += `<${ord ? 'ol' : 'ul'}${ord && start !== 1 ? ` start="${start}"` : ''}>` + items.map((it) => {
          let first = it[0], task = '';
          const t = first.match(/^\[([ xX])\]\s+/);
          if (t) { task = `<span class="task">${t[1] === ' ' ? '☐' : '☑'}</span>`; first = first.slice(t[0].length); }
          const rest = it.slice(1), sub = rest.findIndex((r) => isList(r) || /^\s*```/.test(r));
          const para = sub < 0 ? [first].concat(rest) : [first].concat(rest.slice(0, sub));
          return `<li${top ? lead(first) : ''}>${task}${inl(para.join('\n'))}${sub < 0 ? '' : blocks(rest.slice(sub).map((r) => r.replace(/^ {0,4}/, '')), false)}</li>`;
        }).join('') + `</${ord ? 'ol' : 'ul'}>`;
        continue;
      }
      const p = [];
      while (i < ls.length && ls[i].trim() && !isList(ls[i]) && !/^(#{1,6}\s|\s*```|\s*>)/.test(ls[i]) && !isHr(ls[i]) && !isTbl(i)) p.push(ls[i++]);
      if (!p.length) { o += `<p>${inl(ls[i++])}</p>`; continue; }
      const raw = p.join('\n'), ph = raw.match(/^\*\*([^*\n]{1,60})\*\*$/);
      if (ph) { const id = slug(ph[1], ++n); heads.push({ id, lv: 4, text: ph[1] }); o += `<p class="ph" id="${id}">${inl(ph[1])}</p>`; } else o += `<p${top ? lead(raw) : ''}>${inl(raw)}</p>`;
    }
    return o;
  }
  return { html: blocks(L, true), heads, secs };
}
// citation + mention linking on text nodes after render
const CITE_RE = /(?<![\w/.-])((?:\/|\.{0,2}\/)?[\w.-]+(?:\/[\w.-]+)*\.\w{1,6}):(\d+)(?:-(\d+))?/g;
function walkText(el, re, mk, skip) {
  const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, { acceptNode: (t) => (t.parentElement && t.parentElement.closest(skip) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
  const ns = [];
  while (tw.nextNode()) ns.push(tw.currentNode);
  ns.forEach((t) => {
    const s = t.nodeValue;
    re.lastIndex = 0;
    if (!re.test(s)) return;
    re.lastIndex = 0;
    const fr = document.createDocumentFragment();
    let last = 0, m;
    while ((m = re.exec(s))) { fr.append(s.slice(last, m.index)); fr.append(mk(m)); last = m.index + m[0].length; }
    fr.append(s.slice(last));
    t.replaceWith(fr);
  });
}
function citeify(el, f) {
  if (!el || !f) return 0;
  let n = 0;
  walkText(el, CITE_RE, (m) => {
    n++;
    const b = CE('button');
    b.className = 'cite'; b.type = 'button';
    b.dataset.p = m[1]; b.dataset.a = m[2]; b.dataset.b = m[3] || m[2]; b.dataset.flow = f.id;
    b.textContent = m[0];
    return b;
  }, 'pre,.code,.cite,a,.scp');
  return n;
}
function mentionify(el, f) {
  if (!el || !f) return;
  walkText(el, /@([\w-]+)/g, (m) => {
    const a = agentOf(f, m[1]);
    if (!a) return m[0];
    const b = CE('button');
    b.className = 'ment'; b.type = 'button'; b.dataset.agent = a.id; b.style.color = labC(a.lab); b.textContent = m[0];
    return b;
  }, 'pre,code,.ment');
}
