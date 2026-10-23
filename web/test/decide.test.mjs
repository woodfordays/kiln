import test from 'node:test';
import assert from 'node:assert/strict';
import { TEMPLATES, blankQuestion, defaultDraft, validate, toBody, fromBody, snippets, readAnswer } from '../js/decide.js';

const CARD = {
  state: 'My integration keeps failing with a 401 since we rotated our API key. Please help.',
  questions: {
    team: { type: 'choice', instructions: 'Which team should handle this request?',
      criteria: { billing: 'Charges and refunds', support: 'Technical integration errors', sales: 'Questions about buying a product' } },
    urgent: { type: 'noul', instructions: 'Is the customer blocked right now?' },
    mood: { type: 'score', criteria: ['calm', 'mildly annoyed', 'frustrated', 'angry'] },
  },
};

test('the default template builds the model card example exactly', () => {
  const d = defaultDraft();
  assert.equal(validate(d).ok, true);
  assert.deepEqual(toBody(d), CARD);
});

test('every template is valid as it comes', () => {
  for (const t of TEMPLATES.filter((t) => t.id !== 'blank')) assert.equal(validate(t.draft()).ok, true, t.id);
});

test('a pasted body round-trips through the builder', () => {
  const withModel = { model: 'decider', ...CARD };
  withModel.questions = { ...CARD.questions, flag: { type: 'noul', criteria: { true: 'Spam', false: 'Not spam' } },
    pick: { type: 'choice', criteria: { a: null, b: { note: 'x' } } } };
  const d = fromBody(JSON.stringify(withModel));
  assert.equal(d.model, 'decider');
  const back = toBody(d);
  assert.deepEqual(back.questions.flag, { type: 'noul', criteria: { false: 'Not spam', true: 'Spam' } });
  assert.deepEqual(back.questions.pick, { type: 'choice', criteria: { a: null, b: '{"note":"x"}' } }); // the server describes objects as JSON too
  assert.deepEqual(back.questions.team, CARD.questions.team);
});

test('object states switch the builder to JSON and come back as objects', () => {
  const d = fromBody(JSON.stringify({ state: { ticket: 1, body: 'hi' }, questions: { q: { type: 'noul' } } }));
  assert.equal(d.stateMode, 'json');
  assert.deepEqual(toBody(d).state, { ticket: 1, body: 'hi' });
});

test('import errors say what is wrong', () => {
  assert.throws(() => fromBody('{'), /Not valid JSON/);
  assert.throws(() => fromBody('{"questions":{"q":{"type":"noul"}}}'), /no "state"/);
  assert.throws(() => fromBody('{"state":"x","questions":{}}'), /non-empty/);
  assert.throws(() => fromBody('{"state":"x","questions":{"q":{"type":"rank"}}}'), /choice, noul or score/);
  assert.throws(() => fromBody('{"state":"x","questions":{"q":{"type":"score","criteria":{"a":1}}}}'), /array of levels/);
});

test('validation catches what the server rejects and what it would answer uselessly', () => {
  const d = defaultDraft();
  d.state = '  ';
  d.questions[1].id = 'team';
  d.questions[0].options[2].key = 'billing';
  d.questions[2].levels = ['only one'];
  const v = validate(d);
  assert.equal(v.ok, false);
  assert.match(v.state, /Add the text/);
  assert.match(v.byUid[d.questions[1].uid].id, /used twice/);
  assert.match(v.byUid[d.questions[0].uid].options, /different/);
  assert.match(v.byUid[d.questions[2].uid].levels, /two levels/);
  assert.match(validate({ ...d, stateMode: 'json', state: '"text"' }).state, /object or an array/);
  assert.match(validate({ ...d, questions: [] }).questions[0], /at least one/);
  const blank = blankQuestion('choice', 'x');
  assert.match(validate({ model: '', stateMode: 'text', state: 's', questions: [blank] }).byUid[blank.uid].options, /needs a name/);
});

test('blank instructions and option descriptions are left out, not sent empty', () => {
  const q = { ...blankQuestion('choice', 'q'), options: [{ key: 'a', desc: '' }, { key: 'b', desc: ' ' }] };
  assert.deepEqual(toBody({ model: '', stateMode: 'text', state: 's', questions: [q] }).questions.q, { type: 'choice', criteria: { a: null, b: null } });
});

test('snippets carry the same body in each language', () => {
  const body = { ...CARD, questions: { ...CARD.questions, x: { type: 'choice', criteria: { a: null, "it's": 'quote' } } } };
  const s = snippets('http://h:8080/v1/systemone', body);
  assert.match(s.curl, /^curl http:\/\/h:8080\/v1\/systemone/);
  const quoted = s.curl.slice(s.curl.indexOf("-d '") + 3);
  // undo the shell quoting and the JSON must be identical
  assert.deepEqual(JSON.parse(quoted.slice(1, -1).replace(/'\\''/g, "'")), body);
  assert.match(s.python, /"a": None/);
  assert.doesNotMatch(s.python, /null/);
  assert.match(s.python, /print\(answers\["team"\]\["choice"\]\)/);
  assert.deepEqual(JSON.parse(s.json), body);
  assert.match(s.javascript, /console\.log\(answers\["team"\]\['choice'\]\)/);
});

test('answers become rows to draw', () => {
  const qs = defaultDraft().questions;
  const choice = readAnswer(qs[0], { choice: 'support', probabilities: { billing: 0.01, support: 0.98, sales: 0.01 }, confidence: 0.98 });
  assert.equal(choice.headline, 'support');
  assert.deepEqual(choice.rows.filter((r) => r.top).map((r) => r.label), ['support']);
  const no = readAnswer(qs[1], { noul: 0.2 });
  assert.equal(no.headline, 'No');
  assert.ok(Math.abs(no.p - 0.8) < 1e-9);
  const score = readAnswer(qs[2], { score: 2.8, probabilities: { 0: 0.01, 1: 0.02, 2: 0.14, 3: 0.83 }, legend: ['calm', 'mildly annoyed', 'frustrated', 'angry'], confidence: 0.83 });
  assert.equal(score.headline, '2.80');
  assert.equal(score.sub, '≈ angry');
  assert.equal(score.rows[3].label, '3 · angry');
  assert.equal(score.max, 3);
});
