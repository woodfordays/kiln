import test from 'node:test';
import assert from 'node:assert/strict';
import {
  FORMATS, TTS_EXAMPLES, defaultSpeech, validateSpeech, speechBody, mimeOf, extOf, speechFilename, errorMessage, pcmToWav,
  defaultTranscription, transcriptionFields, transcriptionForm, readTranscription, fmtClock, wordErrorRate, recorderMime,
  speechCurl, transcriptionCurl,
} from '../js/audio.js';

test('the default speech draft is valid and sends only what differs from the API defaults', () => {
  const d = defaultSpeech();
  assert.equal(validateSpeech(d).ok, true);
  assert.deepEqual(speechBody(d), { input: TTS_EXAMPLES[0].text, response_format: 'wav' });
});

test('every example prompt is valid', () => {
  for (const x of TTS_EXAMPLES) assert.equal(validateSpeech({ ...defaultSpeech(), input: x.text }).ok, true, x.id);
});

test('speech body carries model, voice, format and speed when set', () => {
  const b = speechBody({ model: 'kokoro', input: '  Hi there  ', voice: ' nova ', format: 'mp3', speed: 1.234 });
  assert.deepEqual(b, { model: 'kokoro', input: 'Hi there', voice: 'nova', speed: 1.23 });
});

test('speech validation flags empty text, long text, bad speed and format', () => {
  const v = validateSpeech({ input: '   ', format: 'nope', speed: 9 });
  assert.equal(v.ok, false);
  assert.ok(v.errors.input && v.errors.format && v.errors.speed);
  assert.match(validateSpeech({ ...defaultSpeech(), input: 'x'.repeat(5000) }).errors.input, /4096/);
});

test('formats map to MIME types and back to extensions', () => {
  for (const f of FORMATS) assert.ok(mimeOf(f.value).startsWith('audio/'));
  assert.equal(extOf('audio/mpeg'), 'mp3');
  assert.equal(extOf('audio/wav; charset=binary'), 'wav');
  assert.equal(extOf('audio/webm;codecs=opus'), 'webm');
  assert.equal(extOf('', 'flac'), 'flac');
  assert.equal(extOf('application/octet-stream', 'mp3'), 'mp3');
});

test('download names come from the text', () => {
  assert.equal(speechFilename('Hello! This is Kiln, speaking through a local model.', 'wav'), 'kiln-tts-hello-this-is-kiln-speaking-thro.wav');
  assert.equal(speechFilename('', 'mp3'), 'kiln-tts.mp3');
});

test('error bodies read as one message', () => {
  assert.equal(errorMessage(400, '{"error":{"message":"Unknown voice"}}'), 'Unknown voice');
  assert.equal(errorMessage(422, '{"detail":"model is required"}'), 'model is required');
  assert.equal(errorMessage(502, 'Bad gateway'), 'Bad gateway');
  assert.equal(errorMessage(500, ''), 'HTTP 500');
});

test('raw PCM gets a playable WAV header', () => {
  const wav = pcmToWav(new Uint8Array([1, 0, 2, 0]), 16000);
  const v = new DataView(wav.buffer);
  assert.equal(String.fromCharCode(...wav.slice(0, 4)), 'RIFF');
  assert.equal(String.fromCharCode(...wav.slice(8, 12)), 'WAVE');
  assert.equal(v.getUint32(24, true), 16000);
  assert.equal(v.getUint32(40, true), 4);
  assert.equal(wav.length, 48);
});

test('transcription form fields', () => {
  assert.deepEqual(transcriptionFields(defaultTranscription()), [['response_format', 'verbose_json'], ['timestamp_granularities[]', 'segment']]);
  assert.deepEqual(transcriptionFields({ model: 'whisper', language: ' en ', prompt: '', format: 'json' }), [['model', 'whisper'], ['language', 'en']]);
});

test('the transcription form carries the file under its name', async () => {
  const form = transcriptionForm({ model: 'w', format: 'text' }, new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/wav' }), 'clip.wav');
  const file = form.get('file');
  assert.equal(file.name, 'clip.wav');
  assert.equal(file.size, 3);
  assert.equal(form.get('model'), 'w');
  assert.equal(form.get('response_format'), 'text');
});

test('transcriptions read the same whatever the format', () => {
  const verbose = readTranscription('verbose_json', JSON.stringify({ text: ' Hello world. ', language: 'english', duration: 2.5,
    segments: [{ id: 0, start: 0, end: 1.2, text: ' Hello' }, { id: 1, start: 1.2, end: 2.5, text: ' world.' }] }));
  assert.deepEqual(verbose, { text: 'Hello world.', language: 'english', duration: 2.5,
    segments: [{ start: 0, end: 1.2, text: 'Hello' }, { start: 1.2, end: 2.5, text: 'world.' }] });
  assert.deepEqual(readTranscription('json', { text: 'Hi' }), { text: 'Hi', language: null, duration: null, segments: [] });
  assert.equal(readTranscription('text', 'Just text\n').text, 'Just text');
  const srt = readTranscription('srt', '1\n00:00:00,000 --> 00:00:01,500\nHello\n\n2\n00:00:01,500 --> 00:00:03,000\nthere\nfriend\n');
  assert.equal(srt.text, 'Hello there friend');
  assert.equal(srt.duration, 3);
  const vtt = readTranscription('vtt', 'WEBVTT\n\n00:00.000 --> 00:02.250\nOne cue\n');
  assert.deepEqual(vtt.segments, [{ start: 0, end: 2.25, text: 'One cue' }]);
});

test('clock times', () => {
  assert.equal(fmtClock(0), '0:00.0');
  assert.equal(fmtClock(62.35), '1:02.3');
  assert.equal(fmtClock(3725), '1:02:05.0');
  assert.equal(fmtClock(null), '–');
});

test('word error rate ignores case and punctuation', () => {
  assert.equal(wordErrorRate('The quick brown fox.', 'the quick brown fox'), 0);
  assert.equal(wordErrorRate('one two three four', 'one too three'), 0.5);
  assert.equal(wordErrorRate('', ''), 0);
});

test('the recorder picks the first type the browser supports', () => {
  assert.equal(recorderMime((t) => t === 'audio/mp4'), 'audio/mp4');
  assert.equal(recorderMime(() => true), 'audio/webm;codecs=opus');
  assert.equal(recorderMime(() => { throw new Error('no'); }), '');
});

test('curl snippets', () => {
  assert.equal(speechCurl('http://h:8080', { input: "it's" }, 'wav'),
    `curl http://h:8080/v1/audio/speech \\\n  -H 'Content-Type: application/json' \\\n  -d '{\n  "input": "it'\\''s"\n}' \\\n  -o speech.wav`);
  assert.equal(transcriptionCurl('http://h:8080', { model: 'w', format: 'json' }),
    "curl http://h:8080/v1/audio/transcriptions \\\n  -F file=@'speech.wav' \\\n  -F 'model=w'");
});
