/* The model library as nested rows: models on top, their companion files folded under them. */

// Each top-level entry (models, and companions without a model) with `children`: its companions, by name.
// Top-level order is the input order. Entries are copied, never mutated.
export function nestLibrary(models) {
  const byPath = new Map();
  const tops = [];
  for (const m of models) {
    if (m.companion_of) continue;
    const copy = { ...m, children: [] };
    byPath.set(m.path, copy);
    tops.push(copy);
  }
  for (const m of models) {
    if (!m.companion_of) continue;
    const parent = byPath.get(m.companion_of);
    if (parent) parent.children.push({ ...m, parentName: parent.name });
    else tops.push({ ...m, children: [] }); // its model is missing from the list: keep it on top
  }
  for (const t of tops) t.children.sort(cmpName);
  return tops;
}

// The rows to render, in order. Each row is the model object plus: depth (0 = top level, 1 = a companion),
// childCount and open. Top-level rows also carry forcedOpen: a matching companion holds them open, so the
// chevron must not toggle. Child rows carry parentName: the model they're shown under.
// sort: { key, direction: 'asc'|'desc' } on a model field (strings compare case-insensitively;
//   null/undefined last in both directions; ties keep input order). Children always follow their parent, by name.
// filter: text matched case-insensitively against name, repo and arch (only the ones present). A parent that
//   matches shows (its children only when open). A parent that doesn't match but has matching children shows
//   opened, with only those.
// expanded: a Set of parent paths the user opened.
export function libraryRows(groups, { sort, filter, expanded }) {
  const f = (filter || '').trim().toLowerCase();
  const matches = (m) => !f || [m.name, m.repo, m.arch].filter(Boolean).join(' ').toLowerCase().includes(f);
  const dir = sort && sort.direction === 'asc' ? 1 : -1;
  const tops = sort ? groups.slice().sort((a, b) => cmp(a, b, sort.key, dir)) : groups;
  const rows = [];
  for (const g of tops) {
    const parentShown = matches(g);
    const childHits = g.children.filter(matches);
    if (!parentShown && !childHits.length) continue;
    const open = parentShown ? expanded.has(g.path) : true; // a matching companion forces its model open
    rows.push({ ...g, depth: 0, childCount: g.children.length, open, forcedOpen: !parentShown });
    if (open) for (const c of parentShown ? g.children : childHits) rows.push({ ...c, depth: 1, childCount: 0, open: false });
  }
  return rows;
}

function cmpName(a, b) {
  const x = a.name.toLowerCase(), y = b.name.toLowerCase();
  return x < y ? -1 : x > y ? 1 : 0;
}

function cmp(a, b, key, dir) {
  const va = a[key], vb = b[key];
  const ma = va === null || va === undefined;
  const mb = vb === null || vb === undefined;
  if (ma || mb) return ma === mb ? 0 : ma ? 1 : -1; // missing values always sort last, in both directions
  if (typeof va === 'string' || typeof vb === 'string') {
    const x = String(va).toLowerCase(), y = String(vb).toLowerCase();
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  }
  return (va < vb ? -1 : va > vb ? 1 : 0) * dir;
}
