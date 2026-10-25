/* The Audio page's logic: text-to-speech and speech-to-text requests in the OpenAI shape that Kiln's proxy
   serves (/v1/audio/speech, /v1/audio/transcriptions), the checks the server would make, how to read an
   answer, and the same calls as curl. No React here, so the tests can run it under node. */

export const FORMATS = [
  { value: 'wav', label: 'WAV', mime: 'audio/wav' },
  { value: 'mp3', label: 'MP3', mime: 'audio/mpeg' },
  { value: 'opus', label: 'Opus', mime: 'audio/ogg' },
  { value: 'flac', label: 'FLAC', mime: 'audio/flac' },
  { value: 'aac', label: 'AAC', mime: 'audio/aac' },
  { value: 'pcm', label: 'PCM (raw)', mime: 'audio/pcm' },
];
// OpenAI's names; a server that has its own voices says which ones in its error
export const VOICES = ['alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'nova', 'onyx', 'sage', 'shimmer'];
export const SPEED = { min: 0.25, max: 4, step: 0.05 };
export const MAX_INPUT = 4096; // OpenAI's limit on input characters

export const TTS_EXAMPLES = [
  { id: 'hello', label: 'Hello', text: 'Hello! This is Kiln, speaking through a local text-to-speech model.' },
  { id: 'pangram', label: 'Pangram', text: 'The quick brown fox jumps over the lazy dog.' },
  { id: 'numbers', label: 'Numbers and dates', text: 'Your order 4411 ships on March 3rd, 2027, and costs $129.99.' },
  { id: 'question', label: 'A question', text: 'Did you remember to turn off the kiln before you left?' },
  { id: 'long', label: 'A paragraph', text: 'Pottery kilns fire clay at temperatures above a thousand degrees. ' +
      'The heat drives out water, fuses the particles together, and turns soft clay into hard, lasting ceramic.' },
];

export function defaultSpeech() {
  return { model: '', input: TTS_EXAMPLES[0].text, voice: '', format: 'wav', speed: 1 };
}

/* ---------------------------------------------------------------- text to speech */

export function validateSpeech(d) {
  const errors = {};
  const input = (d.input || '').trim();
  if (!input) errors.input = 'Type something to say.';
  else if (input.length > MAX_INPUT) errors.input = `At most ${MAX_INPUT} characters (this is ${input.length}).`;
  if (!FORMATS.some((f) => f.value === d.format)) errors.format = 'Pick a format.';
  const s = Number(d.speed);
  if (!Number.isFinite(s) || s < SPEED.min || s > SPEED.max) errors.speed = `Speed runs from ${SPEED.min} to ${SPEED.max}.`;
  return { ok: !Object.keys(errors).length, errors };
}

/* The JSON body for POST /v1/audio/speech. Optional fields at their defaults are left out, so the server's
   own defaults apply and a model that ignores them is not told about them. */
export function speechBody(d) {
  const body = {};
  if (d.model) body.model = d.model;
  body.input = (d.input || '').trim();
  if ((d.voice || '').trim()) body.voice = d.voice.trim();
  if (d.format && d.format !== 'mp3') body.response_format = d.format; // mp3 is the API's default
  if (Number(d.speed) !== 1) body.speed = Math.round(Number(d.speed) * 100) / 100;
  return body;
}

export const mimeOf = (format) => (FORMATS.find((f) => f.value === format) || FORMATS[0]).mime;

/* A file extension for a Content-Type (or the requested format when the server sent none). */
export function extOf(mime, fallback = 'wav') {
  const m = (mime || '').split(';')[0].trim().toLowerCase();
  const map = {
    'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/wave': 'wav', 'audio/vnd.wave': 'wav',
    'audio/mpeg': 'mp3', 'audio/mp3': 'mp3', 'audio/ogg': 'ogg', 'audio/opus': 'opus', 'audio/webm': 'webm',
    'audio/flac': 'flac', 'audio/x-flac': 'flac', 'audio/aac': 'aac', 'audio/mp4': 'm4a', 'audio/x-m4a': 'm4a',
    'audio/pcm': 'pcm', 'audio/l16': 'pcm',
  };
  return map[m] || fallback;
}

/* "kiln-tts-hello-this-is-kiln.wav" */
export function speechFilename(input, ext) {
  const slug = (input || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32).replace(/-+$/, '');
  return `kiln-tts${slug ? '-' + slug : ''}.${ext}`;
}

/* A failed call's body as one message: OpenAI's {error: {message}}, FastAPI's {detail}, or plain text. */
export function errorMessage(status, text) {
  let msg = '';
  try {
    const d = JSON.parse(text);
    const e = d && (d.error ?? d.detail);
    msg = typeof e === 'string' ? e : e && e.message ? e.message : e ? JSON.stringify(e) : '';
  } catch {
    msg = (text || '').trim().slice(0, 500);
  }
  return msg || (status ? `HTTP ${status}` : 'No response');
}

/* PCM has no header, so a browser cannot play it: wrap 16-bit mono samples in a WAV header. */
export function pcmToWav(pcm, sampleRate = 24000, channels = 1) {
  const bytes = pcm instanceof Uint8Array ? pcm : new Uint8Array(pcm);
  const buf = new ArrayBuffer(44 + bytes.length);
  const v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + bytes.length, true); str(8, 'WAVE');
  str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, channels, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * channels * 2, true);
  v.setUint16(32, channels * 2, true); v.setUint16(34, 16, true);
  str(36, 'data'); v.setUint32(40, bytes.length, true);
  new Uint8Array(buf, 44).set(bytes);
  return new Uint8Array(buf);
}

/* ---------------------------------------------------------------- speech to text */

export const STT_FORMATS = ['json', 'verbose_json', 'text', 'srt', 'vtt'];

export function defaultTranscription() {
  return { model: '', language: '', prompt: '', format: 'verbose_json' };
}

/* The fields of the multipart form (Kiln routes the path to its speech-to-text model, so model may be left out) for POST /v1/audio/transcriptions, file aside, in send order. */
export function transcriptionFields(d) {
  const f = [];
  if (d.model) f.push(['model', d.model]);
  if ((d.language || '').trim()) f.push(['language', d.language.trim()]);
  if ((d.prompt || '').trim()) f.push(['prompt', d.prompt.trim()]);
  if (d.format && d.format !== 'json') f.push(['response_format', d.format]);
  if (d.format === 'verbose_json') f.push(['timestamp_granularities[]', 'segment']);
  return f;
}

/* A FormData for the call. FormData and Blob are globals in browsers and in node 18+. */
export function transcriptionForm(d, file, filename) {
  const form = new FormData();
  form.append('file', file, filename || file.name || 'audio.wav');
  for (const [k, v] of transcriptionFields(d)) form.append(k, v);
  return form;
}

const clock = (s) => {
  const ms = Math.round(s * 1000);
  const h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, sec = Math.floor(ms / 1000) % 60, r = ms % 1000;
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(sec).padStart(2, '0') + '.' + String(r).padStart(3, '0').slice(0, 1);
};
export const fmtClock = (s) => (Number.isFinite(s) ? clock(s) : '–');

/* SRT/VTT cue times to seconds: "00:01:02,500" or "01:02.500". */
function cueSeconds(t) {
  const p = t.trim().replace(',', '.').split(':').map(Number);
  return p.reduce((acc, x) => acc * 60 + x, 0);
}

function parseCues(text) {
  const segs = [];
  const re = /((?:\d+:)?\d+:\d+[.,]\d+)\s*-->\s*((?:\d+:)?\d+:\d+[.,]\d+)[^\n]*\n([\s\S]*?)(?=\n\s*\n|$)/g;
  let m;
  while ((m = re.exec(text))) segs.push({ start: cueSeconds(m[1]), end: cueSeconds(m[2]), text: m[3].trim().replace(/\s*\n\s*/g, ' ') });
  return segs;
}

/* Any transcription answer as { text, language, duration, segments: [{start, end, text}] }. */
export function readTranscription(format, body) {
  if (format === 'srt' || format === 'vtt') {
    const segments = parseCues(String(body).replace(/\r/g, ''));
    return { text: segments.map((s) => s.text).join(' '), language: null, duration: segments.length ? segments[segments.length - 1].end : null, segments };
  }
  if (typeof body === 'string') {
    let d = null;
    try { d = JSON.parse(body); } catch {}
    if (!d || typeof d !== 'object') return { text: body.trim(), language: null, duration: null, segments: [] };
    body = d;
  }
  const segments = Array.isArray(body.segments)
    ? body.segments.map((s) => ({ start: Number(s.start), end: Number(s.end), text: String(s.text || '').trim() })) : [];
  const duration = Number.isFinite(Number(body.duration)) && body.duration != null ? Number(body.duration)
    : segments.length ? segments[segments.length - 1].end : null;
  return { text: String(body.text || '').trim(), language: body.language || null, duration, segments };
}

/* Word error rate of a transcript against what was said, for the round trip. Case and punctuation aside. */
export function wordErrorRate(ref, hyp) {
  const words = (s) => (s || '').toLowerCase().replace(/[^\p{L}\p{N}\s']/gu, ' ').split(/\s+/).filter(Boolean);
  const r = words(ref), h = words(hyp);
  if (!r.length) return h.length ? 1 : 0;
  let prev = Array.from({ length: h.length + 1 }, (_, j) => j);
  for (let i = 1; i <= r.length; i++) {
    const cur = [i];
    for (let j = 1; j <= h.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r[i - 1] === h[j - 1] ? 0 : 1));
    prev = cur;
  }
  return prev[h.length] / r.length;
}

/* The best MediaRecorder type this browser offers. isSupported is MediaRecorder.isTypeSupported. */
export function recorderMime(isSupported) {
  const want = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/wav'];
  return want.find((t) => { try { return isSupported(t); } catch { return false; } }) || '';
}

/* ---------------------------------------------------------------- the same calls in code */

const shq = (s) => "'" + String(s).replace(/'/g, "'\\''") + "'";

export function speechCurl(base, body, ext) {
  return `curl ${base}/v1/audio/speech \\\n  -H 'Content-Type: application/json' \\\n  -d ${shq(JSON.stringify(body, null, 2))} \\\n  -o speech.${ext}`;
}

export function transcriptionCurl(base, d, filename = 'speech.wav') {
  const fields = transcriptionFields(d);
  return [`curl ${base}/v1/audio/transcriptions`, `  -F file=@${shq(filename)}`, ...fields.map(([k, v]) => `  -F ${shq(k + '=' + v)}`)].join(' \\\n');
}
