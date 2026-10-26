/* The Audio page's logic: text-to-speech and speech-to-text requests in the OpenAI shape that Kiln's proxy
   serves (/v1/audio/speech, /v1/audio/transcriptions), the checks the server would make, how to read an
   answer, and the same calls as curl. No React here, so the tests can run it under node. */

// what Kiln's audio.cpp backend returns (it also has "json", base64 WAV in JSON, which a player has no use for)
export const FORMATS = [
  { value: 'wav', label: 'WAV', mime: 'audio/wav' },
  { value: 'pcm', label: 'PCM (raw 16-bit)', mime: 'audio/pcm' },
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
  if (d.format && d.format !== 'wav') body.response_format = d.format; // wav is Kiln's default
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

export function defaultTranscription() {
  return { model: '', language: '' };
}

/* The fields of the multipart form (Kiln routes the path to its speech-to-text model, so model may be left out) for POST /v1/audio/transcriptions, file aside, in send order. */
export function transcriptionFields(d) {
  const f = [];
  if (d.model) f.push(['model', d.model]);
  if ((d.language || '').trim()) f.push(['language', d.language.trim()]);
  return f;
}

/* A FormData for the call. FormData and Blob are globals in browsers and in node 18+. */
export function transcriptionForm(d, file, filename) {
  const form = new FormData();
  form.append('file', file, filename || file.name || 'audio.wav'); // WAV only: see toWav16k in the page
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

/* Any transcription answer as { text, language, duration, segments: [{start, end, text}], timing }. Kiln answers
   {text, timing}; OpenAI-style servers may send verbose_json segments, plain text, SRT or VTT. */
export function readTranscription(format, body) {
  if (format === 'srt' || format === 'vtt') {
    const segments = parseCues(String(body).replace(/\r/g, ''));
    return { text: segments.map((s) => s.text).join(' '), language: null, duration: segments.length ? segments[segments.length - 1].end : null, segments, timing: null };
  }
  if (typeof body === 'string') {
    let d = null;
    try { d = JSON.parse(body); } catch {}
    if (!d || typeof d !== 'object') return { text: body.trim(), language: null, duration: null, segments: [], timing: null };
    body = d;
  }
  const segments = Array.isArray(body.segments)
    ? body.segments.map((s) => ({ start: Number(s.start), end: Number(s.end), text: String(s.text || '').trim() })) : [];
  const duration = Number.isFinite(Number(body.duration)) && body.duration != null ? Number(body.duration)
    : segments.length ? segments[segments.length - 1].end : null;
  const timing = body.timing && typeof body.timing === 'object' ? body.timing : null;
  return { text: String(body.text || '').trim(), language: body.language || null, duration, segments, timing };
}

/* The timing object's numbers as [label, text] rows: {"decode_ms": 812} → ["decode", "812 ms"]. */
export function timingRows(timing) {
  if (!timing) return [];
  return Object.entries(timing).filter(([, v]) => typeof v === 'number' && Number.isFinite(v)).map(([k, v]) => {
    const m = k.match(/^(.*?)_?(ms|s|sec|seconds)$/);
    const label = (m ? m[1] : k).replace(/_/g, ' ') || k;
    const unit = m ? (m[2] === 'ms' ? 'ms' : 's') : '';
    return [label, unit === 'ms' ? `${Math.round(v)} ms` : unit ? `${v.toFixed(2)} s` : String(Math.round(v * 100) / 100)];
  });
}

/* GET /v1/audio/voices answers {voices: [...]}, as names or as objects with a name or id. */
export function voicesOf(data) {
  const list = data && Array.isArray(data.voices) ? data.voices : Array.isArray(data) ? data : [];
  return [...new Set(list.map((v) => (typeof v === 'string' ? v : v && (v.name || v.id || v.voice))).filter((v) => typeof v === 'string' && v))];
}

/* Kiln's speech-to-text takes WAV only. True when the first bytes are a RIFF/WAVE header. */
export function isWav(bytes) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const tag = (o) => String.fromCharCode(b[o], b[o + 1], b[o + 2], b[o + 3]);
  return b.length >= 12 && tag(0) === 'RIFF' && tag(8) === 'WAVE';
}

/* Channels of float samples (as AudioBuffer.getChannelData gives them) to one mono channel. */
export function mixToMono(channels) {
  if (channels.length === 1) return channels[0];
  const out = new Float32Array(channels[0].length);
  for (const ch of channels) for (let i = 0; i < out.length; i++) out[i] += ch[i] / channels.length;
  return out;
}

/* Mono float samples in [-1, 1] to a 16-bit PCM WAV file. */
export function encodeWav(samples, sampleRate) {
  const pcm = new Uint8Array(samples.length * 2);
  const v = new DataView(pcm.buffer);
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  return pcmToWav(pcm, sampleRate, 1);
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
