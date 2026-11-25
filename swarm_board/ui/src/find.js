// ===== find.js: Find, scrolling, progress line =====
let FIND = { i: 0, ms: [] };
function openFind() {
  const r = $('findrow');
  if (!r) return;
  r.innerHTML = `<div class="findrow"><input id="fq" type="search" aria-label="Find in text" placeholder="Find" value="${esc(S.fq || '')}"><span class="cnt" id="fcnt"></span><button class="tbtn" data-act="fprev" aria-label="Previous match">↑</button><button class="tbtn" data-act="fnext" aria-label="Next match">↓</button><button class="tbtn" data-act="fclose">Esc</button></div>`;
  const q = $('fq');
  q.addEventListener('input', () => { S.fq = q.value; runFind(); });
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); stepFind(e.shiftKey ? -1 : 1); } if (e.key === 'Escape') { e.stopPropagation(); closeFind(); } });
  q.focus();
  runFind();
}
function closeFind() { S.find = false; S.fq = ''; clearMarks(); const r = $('findrow'); if (r) r.innerHTML = ''; const b = q1('[data-act=find]'); if (b) b.setAttribute('aria-pressed', 'false'); }
function clearMarks() { qa('#rmain mark.find').forEach((k) => k.replaceWith(k.textContent)); const m = $('rmain'); if (m) m.normalize(); }
function runFind() {
  clearMarks();
  FIND = { i: 0, ms: [] };
  const q = (S.fq || '').trim(), m = $('rmain');
  if (q && m) {
    const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    walkText(m, re, (x) => { const k = CE('mark'); k.className = 'find'; k.textContent = x[0]; return k; }, 'mark,button.tbtn,.endnav');
    FIND.ms = qa('mark.find', m);
  }
  stepFind(0);
}
function stepFind(d) {
  const ms = FIND.ms, c = $('fcnt');
  if (!ms.length) { if (c) c.textContent = (S.fq ? '0 of 0' : ''); return; }
  ms.forEach((x) => x.classList.remove('cur'));
  FIND.i = (FIND.i + d + ms.length) % ms.length;
  ms[FIND.i].classList.add('cur');
  const det = ms[FIND.i].closest('details');
  if (det) det.open = true;
  if (c) c.textContent = `${FIND.i + 1} of ${ms.length}`;
  scrollToEl(ms[FIND.i]);
}
function scrollToEl(el) {
  const sc = $('app').classList.contains('appm') ? el.closest('.side,.left') : null;
  if (sc) { sc.scrollTop += el.getBoundingClientRect().top - sc.getBoundingClientRect().top - 120; }
  else if (window.self === window.top || QS.has('app')) el.scrollIntoView({ block: 'center' });
  else window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 120);
}
function progress() {
  const i = $('progi'), b = $('rbody');
  if (!i || !b) return;
  const r = b.getBoundingClientRect(), sc = $('app').classList.contains('appm') ? $('side') : null;
  const vh = sc ? sc.getBoundingClientRect().bottom : innerHeight, top = sc ? sc.getBoundingClientRect().top : 0;
  const p = Math.max(0, Math.min(1, (vh - r.top) / Math.max(1, r.height)));
  i.style.width = (100 * Math.min(1, Math.max(0, (top - r.top + (vh - top)) / r.height))).toFixed(1) + '%';
  return p;
}
