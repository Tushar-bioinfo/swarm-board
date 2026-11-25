// ===== views2.js: feed, keyboard list =====
function feedHTML(f) {
  const P = f.posts || [], seen = store.get('seen.' + f.id, null);
  if (P.length) store.set('seen.' + f.id, P[P.length - 1].id);
  return `<div class="kick mt20">Feed · ${P.length} posts</div>` + P.map((p) => {
    const a = agentOf(f, p.author), nw = seen != null && p.id > seen;
    return `${nw && p.id === seen + 1 ? `<div class="newdiv">New since you looked</div>` : ''}<div class="post bar${nw ? ' on' : ''}" data-post-row="${p.id}"><div class="by" style="color:${labC(a && a.lab)}">${esc(a ? aName(a) : p.author)}<span class="t">#${p.id}${p.at ? ' · ' + esc(p.at) : ''}</span></div><div class="md">${md(p.text).html}</div></div>`;
  }).join('');
}
const KEYS = [['1–9', 'Switch flow'], ['j / k', 'Next / previous agent'], ['Enter', 'Open the focused node'], ['o', 'Open chat of the selected agent'], ['c', 'Compare (committee) or pick outputs'], ['d', 'Map details on / off'], ['s', 'Stats page on / off'],
  ['/', 'Find in the Reader'], ['f', 'Reading sheet on / off'], ['[ ]', 'Move the split 5%'], ['← →', 'Previous / next seat in Compare'], ['Esc', 'Close Find, sheet or view'], ['?', 'This list']];
const keysHTML = () => `<div class="kick mt20">Keyboard</div><div class="keys mt12">${KEYS.map((k) => `<b>${k[0]}</b><span>${k[1]}</span>`).join('')}</div>`;
