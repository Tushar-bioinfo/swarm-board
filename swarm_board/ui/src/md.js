// ===== md.js =====
const NMC = /^[-+$~≈<>]?\s?[\d][\d,.]*\s?(%|x|×|s|ms|min|MB|KB|GB|M|K|B|h)?$/,
  VEX = /\.(mp4|webm|mov)(\?.*)?$/i, AEX = /\.(wav|mp3|ogg|m4a)(\?.*)?$/i,
  slug = (t, n) => 'h-' + t.toLowerCase().replace(/<[^>]+>/g, '').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) + '-' + n,
  plain = (h) => h.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'"),
  LEAD = /^\*\*([^*]{2,40}?)[:.]?\*\*\s*[:.—-]/, CRE = /(?<![\w/.-])((?:\/|\.{0,2}\/)?[\w.-]+(?:\/[\w.-]+)*\.\w{1,6}):(\d+)(?:-(\d+))?/g;
function inl(s) {
  const code = [];
  s = E(String(s == null ? '' : s).replace(/`([^`]+)`/g, (m, c) => (code.push(`<code>${E(c)}</code>`), '\u0000' + (code.length - 1) + '\u0000')));
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, a, u) => {
    const raw = u.replace(/&amp;/g, '&'), cf = flow(), src = /^https?:\/\//i.test(raw) ? raw : raw.startsWith('/api/raw') ? raw : cf ? rUrl(cf.id, raw) : '';
    return VEX.test(raw) && src ? D_('media', `<video ${PRE} src="${E(src)}" aria-label="${a || 'Video'}"></video>`)
      : AEX.test(raw) && src ? D_('media', `<audio ${PRE} src="${E(src)}" aria-label="${a || 'Audio'}"></audio>`) : S_('mist', `[image: ${a}]`);
  });
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^)]*&quot;)?\)/g, (m, t, u) => (OKU.test(u.replace(/&amp;/g, '&')) ? `<a href="${u}" target="_blank" rel="noopener">${t}</a>` : t));
  s = s.replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, (m, u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
  s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*\w])\*(?=\S)([^*]*?\S)\*(?!\*)/g, '$1<em>$2</em>').replace(/( {2,}|\\)\n/g, '<br>').replace(/&lt;br\s*\/?&gt;/gi, '<br>');
  return s.replace(/\u0000(\d+)\u0000/g, (m, i) => code[+i]);
}
function md(src) {
  const L = String(src == null ? '' : src).replace(/\r\n?/g, '\n').split('\n'), heads = [], secs = {};
  let n = 0;
  const lead = (raw) => { const m = raw.match(LEAD); if (!m) return ''; const id = slug(m[1], ++n); heads.push({ id, lv: 5, text: m[1].trim() }); return ` id="${id}" data-lead="1"`; };
  function blocks(ls, top) {
    let i = 0, o = '';
    const isL = (l) => /^\s*([-*+]|\d+[.)])\s+/.test(l), isH = (l) => /^ {0,3}([-*_])( *\1){2,} *$/.test(l),
      isT = (k) => /\|/.test(ls[k] || '') && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(ls[k + 1] || '');
    while (i < ls.length) {
      const l = ls[i];
      if (!l.trim()) { i++; continue; }
      let m;
      if ((m = l.match(/^\s*(```+|~~~+)\s*([\w+-]*)/))) {
        const f = m[1], lang = m[2], body = []; i++;
        while (i < ls.length && !ls[i].trim().startsWith(f)) body.push(ls[i++]);
        i++;
        o += `<pre data-raw="${E(body.join('\n'))}">${lang ? S_('lang', E(lang)) : ''}<button class="tbtn cp" data-copy="pre">Copy</button><code>${E(body.join('\n'))}</code></pre>`;
        continue;
      }
      if ((m = l.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/))) {
        const lv = m[1].length, h = inl(m[2]), id = slug(h, ++n);
        if (lv <= 3) heads.push({ id, lv, text: plain(h) });
        let j = i + 1;
        if (top && (lv === 2 || lv === 3)) { while (j < ls.length && !(ls[j].match(/^(#{1,3})\s/) && ls[j].match(/^#+/)[0].length <= lv)) j++; secs[id] = ls.slice(i, j).join('\n'); }
        o += `<h${MN(lv, 4)} id="${id}">${h}${secs[id] ? `<button class="scp" data-sec="${id}">Copy section</button>` : ''}</h${MN(lv, 4)}>`;
        i++; continue;
      }
      if (isH(l)) { o += '<hr>'; i++; continue; }
      if (isT(i)) {
        const row = (r) => r.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map((c) => c.trim()),
          hd = row(ls[i]), al = row(ls[i + 1]).map((c) => (/^:-+:$/.test(c) ? 'center' : /-:$/.test(c) ? 'right' : '')), rows = [];
        i += 2;
        while (i < ls.length && /\|/.test(ls[i]) && ls[i].trim()) rows.push(row(ls[i++]));
        const num = hd.map((_, k) => { const cs = rows.map((r) => (r[k] || '').replace(/[`*]/g, '').trim()).filter(Boolean); return cs.length && cs.filter((c) => NMC.test(c)).length / cs.length >= 0.8; }),
          cell = (t, k, c) => `<${t}${num[k] ? ' class="num"' : al[k] ? ` style="text-align:${al[k]}"` : ''}>${inl(c || '')}</${t}>`;
        o += D_('tw', `<table><thead><tr>${JN(hd, (c, k) => cell('th', k, c))}</tr></thead><tbody>${JN(rows, (r) => `<tr>${JN(hd, (_, k) => cell('td', k, r[k]))}</tr>`)}</tbody></table>`);
        continue;
      }
      if (/^\s*>/.test(l)) { const q = []; while (i < ls.length && /^\s*>/.test(ls[i])) q.push(ls[i++].replace(/^\s*>\s?/, '')); o += `<blockquote>${blocks(q, false)}</blockquote>`; continue; }
      if (isL(l)) {
        const base = l.match(/^\s*/)[0].length, ord = /^\s*\d/.test(l), items = [];
        while (i < ls.length) {
          const x = ls[i], ind = x.match(/^\s*/)[0].length;
          if (!x.trim()) { if (ls[i + 1] && (isL(ls[i + 1]) || /^\s{2,}\S/.test(ls[i + 1])) && ls[i + 1].match(/^\s*/)[0].length >= base) { i++; continue; } break; }
          if (ind === base && isL(x)) { items.push([x.replace(/^\s*([-*+]|\d+[.)])\s+/, '')]); i++; continue; }
          if (ind > base && items.length) { items[items.length - 1].push(x.slice(MN(ind, base + 2))); i++; continue; }
          if (ind < base || isH(x) || /^#/.test(x)) break;
          if (items.length) { items[items.length - 1].push(x); i++; continue; }
          break;
        }
        const st = ord ? parseInt(l.trim(), 10) : 1;
        o += `<${ord ? 'ol' : 'ul'}${ord && st !== 1 ? ` start="${st}"` : ''}>` + JN(items, (it) => {
          let fst = it[0], tsk = '';
          const t = fst.match(/^\[([ xX])\]\s+/);
          if (t) { tsk = S_('task', t[1] === ' ' ? '☐' : '☑'); fst = fst.slice(t[0].length); }
          const rest = it.slice(1), sub = rest.findIndex((r) => isL(r) || /^\s*```/.test(r)), para = sub < 0 ? [fst].concat(rest) : [fst].concat(rest.slice(0, sub));
          return `<li${top ? lead(fst) : ''}>${tsk}${inl(para.join('\n'))}${sub < 0 ? '' : blocks(rest.slice(sub).map((r) => r.replace(/^ {0,4}/, '')), false)}</li>`;
        }) + `</${ord ? 'ol' : 'ul'}>`;
        continue;
      }
      const p = [];
      while (i < ls.length && ls[i].trim() && !isL(ls[i]) && !/^(#{1,6}\s|\s*(```|~~~)|\s*>)/.test(ls[i]) && !isH(ls[i]) && !isT(i)) p.push(ls[i++]);
      if (!p.length) { o += `<p>${inl(ls[i++])}</p>`; continue; }
      const raw = p.join('\n'), ph = raw.match(/^\*\*([^*\n]{1,60})\*\*$/);
      if (ph) { const id = slug(ph[1], ++n); heads.push({ id, lv: 4, text: ph[1] }); o += `<p class="ph" id="${id}">${inl(ph[1])}</p>`; } else o += `<p${top ? lead(raw) : ''}>${inl(raw)}</p>`;
    }
    return o;
  }
  return { html: blocks(L, true), heads, secs };
}
function wTxt(el, re, mk, skip) {
  const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, { acceptNode: (t) => (t.parentElement && t.parentElement.closest(skip) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) }), ns = [];
  while (tw.nextNode()) ns.push(tw.currentNode);
  ns.forEach((t) => {
    const s = t.nodeValue; re.lastIndex = 0; if (!re.test(s)) return; re.lastIndex = 0;
    const fr = document.createDocumentFragment(); let last = 0, m;
    while ((m = re.exec(s))) { fr.append(s.slice(last, m.index)); fr.append(mk(m)); last = m.index + m[0].length; }
    fr.append(s.slice(last)); t.replaceWith(fr);
  });
}
function cify(el, f) {
  if (!el || !f) return 0;
  let n = 0;
  wTxt(el, CRE, (m) => { n++; const b = CE('button'); b.className = 'cite'; b.type = 'button'; Object.assign(b.dataset, { p: m[1], a: m[2], b: m[3] || m[2], flow: f.id }); b.textContent = m[0]; return b; }, 'pre,.code,.cite,a,.scp');
  return n;
}
function mify(el, f) {
  if (!el || !f) return;
  wTxt(el, /@([\w-]+)/g, (m) => { const a = agOf(f, m[1]); if (!a) return m[0]; const b = CE('button'); b.className = 'ment'; b.type = 'button'; b.dataset.agent = a.id; b.style.color = labC(a.lab); b.textContent = m[0]; return b; }, 'pre,code,.ment');
}