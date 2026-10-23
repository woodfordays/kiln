/* The decision call builder's logic: a draft (what the form edits) to a /v1/systemone body and back,
   the checks the server would make, and the same call as curl, Python and JavaScript. No React here,
   so the tests can run it under node. */

export const TYPES = [
  { value: 'choice', label: 'Choice' },
  { value: 'noul', label: 'Yes / no' },
  { value: 'score', label: 'Score' },
];
export const TYPE_HELP = {
  choice: 'Picks one of named options. Answers with the choice, a probability per option and the confidence.',
  noul: 'A yes/no question. Answers with the probability that it is true.',
  score: 'Places the state on an ordered scale, lowest first. Answers with the expected level (0 = first) and a probability per level.',
};
const MAX_OPTIONS = 255; // the most option codes a decider checkpoint carries; the server says if a model has fewer

let uidN = 0;
const uid = () => 'q' + (++uidN);

export function blankQuestion(type = 'choice', id = '') {
  return {
    uid: uid(), id, type, instructions: '',
    options: [{ key: '', desc: '' }, { key: '', desc: '' }],
    levels: ['', ''],
    noTrue: '', noFalse: '',
  };
}

export const TEMPLATES = [
  {
    id: 'triage', label: 'Support triage',
    draft: () => ({
      model: '', stateMode: 'text',
      state: 'My integration keeps failing with a 401 since we rotated our API key. Please help.',
      questions: [
        { ...blankQuestion('choice', 'team'), instructions: 'Which team should handle this request?',
          options: [{ key: 'billing', desc: 'Charges and refunds' }, { key: 'support', desc: 'Technical integration errors' },
            { key: 'sales', desc: 'Questions about buying a product' }] },
        { ...blankQuestion('noul', 'urgent'), instructions: 'Is the customer blocked right now?' },
        { ...blankQuestion('score', 'mood'), levels: ['calm', 'mildly annoyed', 'frustrated', 'angry'] },
      ],
    }),
  },
  {
    id: 'moderation', label: 'Moderation',
    draft: () => ({
      model: '', stateMode: 'text',
      state: 'Great post! Check out my profile for cheap followers, link in bio.',
      questions: [
        { ...blankQuestion('choice', 'verdict'), instructions: 'How should this comment be handled?',
          options: [{ key: 'allow', desc: 'On-topic and harmless' }, { key: 'review', desc: 'Unclear, needs a person' },
            { key: 'remove', desc: 'Spam, abuse or off-topic promotion' }] },
        { ...blankQuestion('noul', 'spam'), instructions: 'Is this comment spam or self-promotion?' },
      ],
    }),
  },
  {
    id: 'blank', label: 'Blank',
    draft: () => ({ model: '', stateMode: 'text', state: '', questions: [blankQuestion('choice', 'q1')] }),
  },
];

export const defaultDraft = () => TEMPLATES[0].draft();

/* A draft saved earlier, with fresh question uids so they can't clash with new ones. */
export function rekey(draft) {
  if (!draft || !Array.isArray(draft.questions) || typeof draft.state !== 'string') return null;
  return { model: draft.model || '', stateMode: draft.stateMode === 'json' ? 'json' : 'text', state: draft.state,
    questions: draft.questions.map((q) => ({ ...blankQuestion(q.type, q.id), ...q, uid: uid() })) };
}

/* ---------------------------------------------------------------- checks */

/* Returns { state: msg|null, questions: [msg], byUid: {uid: {field: msg}}, ok }. Mirrors decider.parse, plus
   the mistakes the server would accept but answer uselessly (one option, duplicate keys that JSON collapses). */
export function validate(draft) {
  const byUid = {};
  const questions = [];
  let state = null;
  if (draft.stateMode === 'json') {
    try {
      const v = JSON.parse(draft.state);
      if (v === null || typeof v !== 'object') state = 'JSON state must be an object or an array.';
    } catch (e) {
      state = 'Not valid JSON: ' + e.message;
    }
  } else if (!draft.state.trim()) {
    state = 'Add the text the model should classify.';
  }
  if (!draft.questions.length) questions.push('Add at least one question.');
  const seen = new Set();
  for (const q of draft.questions) {
    const e = {};
    const id = q.id.trim();
    if (!id) e.id = 'Give the question a name.';
    else if (seen.has(id)) e.id = `“${id}” is used twice.`;
    seen.add(id);
    if (q.type === 'choice') {
      const keys = q.options.map((o) => o.key.trim());
      if (keys.length < 2) e.options = 'Add at least two options.';
      else if (keys.some((k) => !k)) e.options = 'Every option needs a name.';
      else if (new Set(keys).size !== keys.length) e.options = 'Option names must be different.';
      else if (keys.length > MAX_OPTIONS) e.options = `At most ${MAX_OPTIONS} options.`;
    } else if (q.type === 'score') {
      if (q.levels.length < 2) e.levels = 'Add at least two levels.';
      else if (q.levels.some((l) => !l.trim())) e.levels = 'Describe every level.';
      else if (q.levels.length > MAX_OPTIONS) e.levels = `At most ${MAX_OPTIONS} levels.`;
    }
    if (Object.keys(e).length) byUid[q.uid] = e;
  }
  return { state, questions, byUid, ok: !state && !questions.length && !Object.keys(byUid).length };
}

/* ---------------------------------------------------------------- draft ⇄ body */

export function toBody(draft) {
  const body = {};
  if (draft.model) body.model = draft.model;
  let state = draft.state;
  if (draft.stateMode === 'json') {
    try { state = JSON.parse(draft.state); } catch { /* validate() reports it; keep the text so the preview still shows */ }
  }
  body.state = state;
  body.questions = {};
  for (const q of draft.questions) {
    const out = { type: q.type };
    if (q.instructions.trim()) out.instructions = q.instructions.trim();
    if (q.type === 'choice') {
      out.criteria = {};
      for (const o of q.options) out.criteria[o.key.trim()] = o.desc.trim() || null;
    } else if (q.type === 'score') {
      out.criteria = q.levels.map((l) => l.trim());
    } else if (q.noTrue.trim() || q.noFalse.trim()) {
      out.criteria = {};
      if (q.noFalse.trim()) out.criteria.false = q.noFalse.trim();
      if (q.noTrue.trim()) out.criteria.true = q.noTrue.trim();
    }
    body.questions[q.id.trim()] = out;
  }
  return body;
}

const asText = (v) => (typeof v === 'string' ? v : JSON.stringify(v));

/* A pasted request body back into a draft. Throws an Error that says what is wrong. */
export function fromBody(text) {
  let body;
  try { body = JSON.parse(text); } catch (e) { throw new Error('Not valid JSON: ' + e.message); }
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('The request must be a JSON object.');
  if (!('state' in body)) throw new Error('The request has no "state".');
  const qs = body.questions;
  if (!qs || typeof qs !== 'object' || Array.isArray(qs) || !Object.keys(qs).length) throw new Error('"questions" must be a non-empty object.');
  const json = typeof body.state !== 'string';
  const questions = Object.entries(qs).map(([id, q]) => {
    if (!q || typeof q !== 'object') throw new Error(`Question “${id}” must be an object.`);
    if (!['choice', 'noul', 'score'].includes(q.type)) throw new Error(`Question “${id}”: "type" must be choice, noul or score.`);
    const d = { ...blankQuestion(q.type, id), instructions: q.instructions == null ? '' : asText(q.instructions) };
    const c = q.criteria;
    if (q.type === 'choice') {
      if (!c || typeof c !== 'object' || Array.isArray(c)) throw new Error(`Question “${id}”: "criteria" must be an object of options.`);
      d.options = Object.entries(c).map(([key, desc]) => ({ key, desc: desc == null ? '' : asText(desc) }));
    } else if (q.type === 'score') {
      if (!Array.isArray(c)) throw new Error(`Question “${id}”: "criteria" must be an array of levels.`);
      d.levels = c.map(asText);
    } else if (c && typeof c === 'object') {
      d.noTrue = c.true == null ? '' : asText(c.true);
      d.noFalse = c.false == null ? '' : asText(c.false);
    }
    return d;
  });
  return {
    model: typeof body.model === 'string' ? body.model : '',
    stateMode: json ? 'json' : 'text',
    state: json ? JSON.stringify(body.state, null, 2) : body.state,
    questions,
  };
}

/* ---------------------------------------------------------------- the same call in code */

/* JSON with Python's None/True/False, indented like black would. */
function pyLiteral(v, ind = '') {
  if (v === null) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v !== 'object') return JSON.stringify(v);
  const next = ind + '    ';
  const items = Array.isArray(v) ? v.map((x) => pyLiteral(x, next)) : Object.entries(v).map(([k, x]) => JSON.stringify(k) + ': ' + pyLiteral(x, next));
  const [open, close] = Array.isArray(v) ? ['[', ']'] : ['{', '}'];
  return items.length ? `${open}\n${items.map((x) => next + x).join(',\n')},\n${ind}${close}` : open + close;
}

const shq = (s) => "'" + s.replace(/'/g, "'\\''") + "'";

export function snippets(url, body) {
  const pretty = JSON.stringify(body, null, 2);
  const py = pyLiteral(body);
  const keys = Object.keys(body.questions || {});
  const first = keys[0];
  const q = first && body.questions[first];
  const pick = !q ? '' : q.type === 'choice' ? `["choice"]` : q.type === 'noul' ? `["noul"]` : `["score"]`;
  return {
    curl: `curl ${url} \\\n  -H 'Content-Type: application/json' \\\n  -d ${shq(pretty)}`,
    python: `import httpx\n\nbody = ${py}\n\nr = httpx.post(${JSON.stringify(url)}, json=body, timeout=60)\nr.raise_for_status()\nanswers = r.json()["answers"]` +
      (first ? `\nprint(answers[${JSON.stringify(first)}]${pick})` : ''),
    javascript: `const body = ${pretty};\n\nconst r = await fetch(${JSON.stringify(url)}, {\n  method: 'POST',\n  headers: { 'Content-Type': 'application/json' },\n  body: JSON.stringify(body),\n});\nconst { answers } = await r.json();` +
      (first ? `\nconsole.log(answers[${JSON.stringify(first)}]${pick.replace(/"/g, "'")});` : ''),
    json: pretty,
  };
}

/* ---------------------------------------------------------------- reading an answer */

/* One question's answer as rows to draw: [{label, p, top}], plus the headline. */
export function readAnswer(q, a) {
  if (!a) return null;
  if (q.type === 'noul' || 'noul' in a) {
    const p = a.noul;
    return { headline: p >= 0.5 ? 'Yes' : 'No', p: p >= 0.5 ? p : 1 - p, rows: [{ label: 'true', p, top: p >= 0.5 }, { label: 'false', p: 1 - p, top: p < 0.5 }] };
  }
  const probs = a.probabilities || {};
  const names = q.type === 'score' ? (a.legend || q.levels) : Object.keys(probs);
  const keys = Object.keys(probs);
  const topKey = keys.reduce((m, k) => (probs[k] > (probs[m] ?? -1) ? k : m), keys[0]);
  const rows = keys.map((k, i) => ({ label: q.type === 'score' ? `${i} · ${names[i] ?? k}` : k, p: probs[k], top: k === topKey }));
  if (q.type === 'score' || 'score' in a) {
    const i = Math.round(a.score);
    return { headline: `${a.score.toFixed(2)}`, sub: names[i] ? `≈ ${names[i]}` : '', p: a.confidence, rows, score: a.score, max: keys.length - 1 };
  }
  return { headline: a.choice, p: a.confidence, rows };
}
