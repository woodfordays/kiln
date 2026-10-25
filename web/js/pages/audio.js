/* Audio: try text-to-speech and speech-to-text on the loaded models, run both as a round trip, and copy the calls. */
import { html, E, useState, useEffect, useMemo, useRef, useCallback, useApp, useFetch, useEvent, liveOf, copyText, fmtMs, fmtBytes } from '../lib.js';
import {
  FORMATS, VOICES, SPEED, MAX_INPUT, TTS_EXAMPLES, defaultSpeech, defaultTranscription, validateSpeech, speechBody,
  mimeOf, extOf, speechFilename, errorMessage, pcmToWav, transcriptionForm, fmtClock, readTranscription, wordErrorRate, recorderMime,
  speechCurl, transcriptionCurl, voicesOf, timingRows, isWav, encodeWav, mixToMono,
} from '../audio.js';

const TTS_KEY = 'kiln-audio-tts';
const STT_KEY = 'kiln-audio-stt';
const CODE_TABS = [{ value: 'tts', label: 'Text to speech' }, { value: 'stt', label: 'Speech to text' }];

function loadDraft(key, defaults) {
  try { return { ...defaults, ...JSON.parse(localStorage.getItem(key)) }; } catch { return defaults; }
}

/* The address clients reach /v1 at (no path): this origin behind a reverse proxy, else the API port on this host. */
function useApiUrl(status) {
  const [sameOrigin, setSameOrigin] = useState(false);
  useEffect(() => {
    if (location.port) return; // a port of its own means no reverse proxy in front, so /v1 is on the API port
    fetch('/v1/models', { headers: { accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setSameOrigin(!!(d && d.object === 'list')))
      .catch(() => {});
  }, []);
  return sameOrigin ? location.origin : `${location.protocol}//${location.hostname}:${status ? status.api_port : 8080}`;
}

/* An object URL for a blob, revoked when the blob changes and on unmount. */
function useBlobUrl(blob) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!blob) { setUrl(null); return undefined; }
    const u = URL.createObjectURL(blob);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [blob]);
  return url;
}

/* ---------------------------------------------------------------- requests */

async function requestSpeech(draft) {
  const t0 = performance.now();
  let r;
  try {
    r = await fetch('/api/audio/speech', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(speechBody(draft)) });
  } catch (e) { throw new Error('Kiln is not reachable: ' + e.message); }
  if (!r.ok) throw new Error(errorMessage(r.status, await r.text()));
  const buf = await r.arrayBuffer();
  const contentType = (r.headers.get('content-type') || '').split(';')[0].trim() || mimeOf(draft.format);
  const ext = extOf(contentType, draft.format === 'pcm' ? 'pcm' : draft.format);
  const raw = new Blob([buf], { type: contentType });
  const play = ext === 'pcm' ? new Blob([pcmToWav(new Uint8Array(buf), 24000)], { type: 'audio/wav' }) : raw;
  return { raw, play, contentType, ext, size: buf.byteLength, ms: performance.now() - t0, model: r.headers.get('x-kiln-model'), text: draft.input.trim() };
}

/* Kiln's speech-to-text takes WAV only: decode anything else in the browser and resample to 16 kHz mono. */
async function toWav16k(blob) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  let buf;
  try { buf = await ctx.decodeAudioData(await blob.arrayBuffer()); }
  catch { throw new Error('The browser could not read that file as audio.'); }
  finally { ctx.close().catch(() => {}); }
  const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(buf.duration * 16000)), 16000);
  const src = off.createBufferSource();
  src.buffer = buf;
  src.connect(off.destination);
  src.start();
  const out = await off.startRendering();
  return new Blob([encodeWav(mixToMono([out.getChannelData(0)]), 16000)], { type: 'audio/wav' });
}

async function requestTranscription(draft, blob, filename, onPhase) {
  if (!isWav(new Uint8Array(await blob.slice(0, 12).arrayBuffer()))) {
    if (onPhase) onPhase('convert');
    blob = await toWav16k(blob);
    filename = (filename || 'audio').replace(/\.[^.\/]+$/, '') + '.wav';
  }
  if (onPhase) onPhase('send');
  const t0 = performance.now();
  let r;
  try {
    r = await fetch('/api/audio/transcriptions', { method: 'POST', body: transcriptionForm(draft, blob, filename) });
  } catch (e) { throw new Error('Kiln is not reachable: ' + e.message); }
  const text = await r.text();
  if (!r.ok) throw new Error(errorMessage(r.status, text));
  return { ...readTranscription('json', text), raw: text, ms: performance.now() - t0, model: r.headers.get('x-kiln-model') };
}

const modelOptionsFor = (presets, live, any) => [
  { value: '', label: live.length ? `${any} (now ${live[0].name})` : any },
  ...presets.map((p) => {
    const name = p.alias || p.name;
    const up = live.some((i) => i.preset && i.preset.id === p.id);
    return { value: name, label: name + (up ? ' · running' : ' · loads on first call') };
  }),
];

/* ---------------------------------------------------------------- page */

export function AudioPage() {
  const { status, toast, go } = useApp();
  const presets = useFetch('/api/presets');
  useEvent('presets', () => presets.reload());
  const base = useApiUrl(status);

  const [tts, setTts] = useState(() => {
    const d = loadDraft(TTS_KEY, defaultSpeech());
    return FORMATS.some((f) => f.value === d.format) ? d : { ...d, format: 'wav' }; // saved before a format was dropped
  });
  const [stt, setStt] = useState(() => loadDraft(STT_KEY, defaultTranscription()));
  const [codeTab, setCodeTab] = useState('tts');
  useEffect(() => { try { localStorage.setItem(TTS_KEY, JSON.stringify(tts)); } catch {} }, [tts]);
  useEffect(() => { try { localStorage.setItem(STT_KEY, JSON.stringify(stt)); } catch {} }, [stt]);

  const speakers = useMemo(() => (presets.data || []).filter((p) => p.role === 'tts'), [presets.data]);
  const listeners = useMemo(() => (presets.data || []).filter((p) => p.role === 'stt'), [presets.data]);
  const ttsOptions = modelOptionsFor(speakers, liveOf(status).filter((i) => i.role === 'tts'), 'Any text-to-speech model');
  const sttOptions = modelOptionsFor(listeners, liveOf(status).filter((i) => i.role === 'stt'), 'Any speech-to-text model');
  const missing = presets.data ? [!speakers.length && 'text-to-speech', !listeners.length && 'speech-to-text'].filter(Boolean) : [];

  /* the clip the speech-to-text card will transcribe: { blob, name, source } */
  const [clip, setClip] = useState(null);
  const [sttRun, setSttRun] = useState(null); // { result } | { error }
  const [transcribing, setTranscribing] = useState(false);
  const clipAudio = useRef(null);

  const useClip = useCallback((c) => { setClip(c); setSttRun(null); }, []);

  const [phase, setPhase] = useState('send');
  const doTranscribe = useCallback(async (c = clip) => {
    if (!c || !c.blob || transcribing) return;
    setTranscribing(true);
    setPhase('send');
    setSttRun(null);
    try { setSttRun({ result: await requestTranscription(stt, c.blob, c.name, setPhase) }); }
    catch (e) { setSttRun({ error: e.message }); }
    finally { setTranscribing(false); }
  }, [clip, stt, transcribing]);

  /* round trip */
  const [rtId, setRtId] = useState(TTS_EXAMPLES[0].id);
  const [rt, setRt] = useState(null); // { running, step, error: {step, message}, speech, transcript, wer }
  const rtBusy = !!(rt && rt.running);
  const runRoundTrip = useCallback(async () => {
    if (rtBusy) return;
    const text = TTS_EXAMPLES.find((x) => x.id === rtId).text;
    setRt({ running: true, step: 'speech' });
    let speech;
    try { speech = await requestSpeech({ ...tts, input: text, format: 'wav' }); }
    catch (e) { setRt({ error: { step: 'Text to speech', message: e.message } }); return; }
    setRt({ running: true, step: 'transcribe', speech, text });
    try {
      const transcript = await requestTranscription(stt, speech.play, 'roundtrip.wav');
      setRt({ speech, text, transcript, wer: wordErrorRate(text, transcript.text) });
    } catch (e) { setRt({ speech, text, error: { step: 'Speech to text', message: e.message } }); }
  }, [rtBusy, rtId, tts, stt]);

  const headRoundTrip = () => {
    const el = document.querySelector('.audio__roundtrip');
    if (el) el.scrollIntoView({ behavior: 'smooth' });
    runRoundTrip();
  };

  const ttsExt = extOf(mimeOf(tts.format), tts.format);
  const codes = {
    tts: speechCurl(base, speechBody(tts), ttsExt),
    stt: transcriptionCurl(base, stt, clip && clip.name ? clip.name : 'speech.wav'),
  };

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Audio</h1>
        <p className="text-body">Turn text into speech, transcribe a recording or a file, or run one through the other to hear how well they agree.</p>
      </div>
      <div className="page-head__actions">
        <${E.Button} className="audio__run-top" variant="primary" icon="play" loading=${rtBusy} disabled=${rtBusy} aria-label="Run round trip"
          onClick=${headRoundTrip}>Run round trip<//>
      </div>
    </header>

    ${missing.length > 0 && html`<${E.Alert} tone="info" title=${`No ${missing.join(' or ')} model yet`}
      action=${html`<${E.Button} size="sm" variant="secondary" onClick=${() => go('presets')}>Open Presets<//>`}>
      Give a preset the ${missing.map((m) => (m === 'text-to-speech' ? 'Text to speech' : 'Speech to text')).join(' or ')} role to run calls here. You can still build one and copy the code.
    <//>`}

    <div className="grid grid--side audio">
      <div className="stack">
        <${SpeechCard} draft=${tts} setDraft=${setTts} options=${ttsOptions}
          onTranscribe=${(c) => {
            useClip(c);
            const el = document.querySelector('.audio__stt');
            if (el) el.scrollIntoView({ behavior: 'smooth' });
            doTranscribe(c);
          }} />
        <${TranscribeCard} draft=${stt} setDraft=${setStt} options=${sttOptions} clip=${clip} onClip=${useClip} audioRef=${clipAudio}
          run=${sttRun} busy=${transcribing} phase=${phase} onTranscribe=${() => doTranscribe()} toast=${toast} />
      </div>

      <div className="stack">
        <${E.Card} className="audio__roundtrip" title="Round trip" description="Speaks a sentence with the text-to-speech model, then transcribes that audio with the speech-to-text model.">
          <${RoundTrip} id=${rtId} setId=${setRtId} rt=${rt} busy=${rtBusy} onRun=${runRoundTrip} />
        <//>

        <${E.Card} title="Code" description=${html`<span>POST <span className="mono">${base}/v1/audio/${codeTab === 'tts' ? 'speech' : 'transcriptions'}</span></span>`}>
          <${E.Tabs} label="Endpoint" items=${CODE_TABS} value=${codeTab} onChange=${setCodeTab}>
            <div className="code-wrap">
              <pre className="code text-code decide__code audio__code">${codes[codeTab]}</pre>
              <${E.IconButton} icon="copy" label="Copy code" size="sm" variant="secondary"
                onClick=${() => copyText(codes[codeTab]).then(() => toast({ title: 'Copied' }))} />
            </div>
          <//>
        <//>
      </div>
    </div>
  </div>`;
}

/* ---------------------------------------------------------------- text to speech */

function SpeechCard({ draft, setDraft, options, onTranscribe }) {
  const [run, setRun] = useState(null); // { result } | { error }
  const [busy, setBusy] = useState(false);
  const [duration, setDuration] = useState(null);
  const [voices, setVoices] = useState(null); // names from the model, or null
  useEffect(() => {
    let live = true;
    setVoices(null);
    fetch('/api/audio/voices' + (draft.model ? '?model=' + encodeURIComponent(draft.model) : ''), { headers: { accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live) { const v = voicesOf(d); setVoices(v.length ? v : null); } })
      .catch(() => {});
    return () => { live = false; };
  }, [draft.model]);
  const check = useMemo(() => validateSpeech(draft), [draft]);
  const result = run && run.result;
  const playUrl = useBlobUrl(result && result.play);
  const rawUrl = useBlobUrl(result && result.play !== result.raw ? result.raw : null) || playUrl;
  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));

  const generate = useCallback(async (d = draft) => {
    if (busy || !validateSpeech(d).ok) return;
    setBusy(true);
    setDuration(null);
    try { setRun({ result: await requestSpeech(d) }); }
    catch (e) { setRun({ error: e.message }); }
    finally { setBusy(false); }
  }, [busy, draft]);

  const example = (ex) => { const d = { ...draft, input: ex.text }; setDraft(d); generate(d); };
  const onKey = (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); generate(); } };

  return html`<${E.Card} title="Text to speech" description="Generate speech from text with the loaded model.">
    <div className="stack">
      <${E.Select} label="Model" size="sm" aria-label="Text-to-speech model" value=${draft.model} onChange=${(e) => set({ model: e.target.value })} options=${options} />
      <div>
        <textarea aria-label="Text to speak" className="textarea text-body audio__input" rows="4" value=${draft.input} aria-invalid=${!!check.errors.input || undefined}
          placeholder="Type something to say…" onChange=${(e) => set({ input: e.target.value })} onKeyDown=${onKey}></textarea>
        <div className="row row--between">
          <span className=${'text-caption ' + (check.errors.input ? 'is-error' : 'muted')}>${check.errors.input || 'Ctrl+Enter generates'}</span>
          <span className="text-caption muted num audio__count">${draft.input.length} / ${MAX_INPUT}</span>
        </div>
      </div>
      <div className="row audio__examples">
        <span className="text-label muted">Try</span>
        ${TTS_EXAMPLES.map((ex) => html`<${E.Button} key=${ex.id} size="sm" variant="secondary" className="audio__example" aria-label=${`Say example: ${ex.label}`}
          disabled=${busy} onClick=${() => example(ex)}>${ex.label}<//>`)}
      </div>
      <div className="grid grid--2">
        <${E.TextField} label="Voice" optional list="audio-voices" aria-label="Voice" value=${draft.voice} placeholder="Server default" hint=${voices ? `${voices.length} voice${voices.length === 1 ? '' : 's'}` : undefined}
          onChange=${(e) => set({ voice: e.target.value })} />
        <${E.Select} label="Format" aria-label="Audio format" value=${draft.format} onChange=${(e) => set({ format: e.target.value })}
          options=${FORMATS.map((f) => ({ value: f.value, label: f.label }))} />
      </div>
      <datalist id="audio-voices">${(voices || VOICES).map((v) => html`<option key=${v} value=${v}></option>`)}</datalist>
      <div className="em-field">
        <span className="field-label text-label">Speed</span>
        <div className="audio__speed">
          <input type="range" aria-label="Speed" className="audio__range" min=${SPEED.min} max=${SPEED.max} step=${SPEED.step} value=${draft.speed}
            onChange=${(e) => set({ speed: Number(e.target.value) })} />
          <span className="text-code num audio__speed-val">${Number(draft.speed).toFixed(2)}×</span>
          <${E.IconButton} icon="refresh" size="sm" label="Reset speed to 1" disabled=${Number(draft.speed) === 1} onClick=${() => set({ speed: 1 })} />
        </div>
        ${check.errors.speed && html`<p className="text-caption is-error">${check.errors.speed}</p>`}
      </div>
      <div className="row">
        <${E.Button} variant="primary" icon="play" loading=${busy} disabled=${!check.ok || busy} aria-label="Generate" title="Ctrl+Enter"
          onClick=${() => generate()}>Generate<//>
      </div>

      ${run && run.error && html`<${E.Alert} tone="danger" title="Speech was not generated">${run.error}<//>`}
      ${result && html`<div className=${'stack audio__result' + (busy ? ' is-refreshing' : '')}>
        <audio className="audio__player" controls src=${playUrl || undefined} onLoadedMetadata=${(e) => { const d = e.target.duration; if (Number.isFinite(d)) setDuration(d); }}></audio>
        <div className="row row--between text-caption muted">
          <span className="audio__meta">${[result.model, fmtBytes(result.size), fmtMs(result.ms), duration != null ? fmtClock(duration) + ' of audio' : null].filter(Boolean).join(' · ')}</span>
          <span className="row">
            <${E.Button} size="sm" variant="secondary" aria-label="Transcribe this"
              onClick=${() => onTranscribe({ blob: result.play, name: speechFilename(result.text, result.play === result.raw ? result.ext : 'wav'), source: 'tts' })}>Transcribe this<//>
            <a className="audio__download" download=${speechFilename(result.text, result.ext)} href=${rawUrl || undefined} aria-label="Download audio">
              <${E.Button} size="sm" variant="secondary" icon="download" tabIndex=${-1}>Download<//>
            </a>
          </span>
        </div>
      </div>`}
    </div>
  <//>`;
}

/* ---------------------------------------------------------------- speech to text */

function useRecorder(onClip) {
  const [state, setState] = useState({ recording: false, elapsed: 0, error: null });
  const meterRef = useRef(null);
  const r = useRef({});

  const cleanup = useCallback(() => {
    const s = r.current;
    cancelAnimationFrame(s.raf);
    clearInterval(s.timer);
    if (s.stream) s.stream.getTracks().forEach((t) => t.stop());
    if (s.ctx && s.ctx.state !== 'closed') s.ctx.close().catch(() => {});
    if (meterRef.current) meterRef.current.style.width = '0%';
    r.current = {};
  }, []);
  useEffect(() => () => {
    const rec = r.current.rec;
    if (rec) rec.onstop = null; // unmounting: drop the clip
    cleanup();
  }, [cleanup]);

  const start = useCallback(async () => {
    setState({ recording: false, elapsed: 0, error: null });
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === 'undefined') {
      setState({ recording: false, elapsed: 0, error: 'This browser cannot record here. Recording needs a secure origin (https or localhost); open Kiln that way, or upload a file instead.' });
      return;
    }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) {
      const denied = e && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
      const none = e && e.name === 'NotFoundError';
      setState({ recording: false, elapsed: 0, error: denied ? 'Microphone access was blocked. Allow it for this site in the browser, then try again.'
        : none ? 'No microphone was found.' : 'Could not open the microphone: ' + (e && e.message || e) });
      return;
    }
    const s = r.current;
    s.stream = stream;
    const mime = recorderMime((t) => MediaRecorder.isTypeSupported(t));
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks = [];
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      const type = rec.mimeType || mime || 'audio/webm';
      cleanup();
      setState((x) => ({ ...x, recording: false }));
      if (chunks.length) onClip({ blob: new Blob(chunks, { type }), name: `recording.${extOf(type.split(';')[0], 'webm')}`, source: 'record' });
    };
    s.rec = rec;
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Float32Array(an.fftSize);
      s.ctx = ctx;
      const tick = () => {
        an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        const level = Math.min(1, Math.sqrt(sum / buf.length) * 4);
        if (meterRef.current) meterRef.current.style.width = Math.round(level * 100) + '%';
        s.raf = requestAnimationFrame(tick);
      };
      tick();
    } catch {} // the meter is a nicety; recording still works without it
    const t0 = Date.now();
    s.timer = setInterval(() => setState((x) => ({ ...x, elapsed: (Date.now() - t0) / 1000 })), 200);
    rec.start();
    setState({ recording: true, elapsed: 0, error: null });
  }, [cleanup, onClip]);

  const stop = useCallback(() => {
    const rec = r.current.rec;
    if (rec && rec.state !== 'inactive') rec.stop(); else cleanup();
  }, [cleanup]);

  return { ...state, meterRef, start, stop };
}

function TranscribeCard({ draft, setDraft, options, clip, onClip, audioRef, run, busy, phase, onTranscribe, toast }) {
  const [source, setSource] = useState('record');
  const [dragging, setDragging] = useState(false);
  const [raw, setRaw] = useState(false);
  const [clipDuration, setClipDuration] = useState(null);
  const rec = useRecorder(onClip);
  const clipUrl = useBlobUrl(clip && clip.blob);
  const result = run && run.result;
  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));
  useEffect(() => { setClipDuration(null); }, [clip]);

  const take = (file) => { if (file) onClip({ blob: file, name: file.name || 'upload', source: 'upload' }); };
  const seek = (t) => {
    const a = audioRef.current;
    if (!a || !Number.isFinite(t)) return;
    a.currentTime = t;
    a.play().catch(() => {});
  };

  return html`<${E.Card} className="audio__stt" title="Speech to text" description="Record from the microphone or upload a file, then transcribe it.">
    <div className="stack">
      <${E.Select} label="Model" size="sm" aria-label="Speech-to-text model" value=${draft.model} onChange=${(e) => set({ model: e.target.value })} options=${options} />
      <${E.SegmentedControl} label="Audio source" size="sm" value=${source} onChange=${setSource}
        options=${[{ value: 'record', label: 'Record' }, { value: 'upload', label: 'Upload' }]} />

      ${source === 'record' ? html`<div className="stack stack--sm audio__record">
        <div className="row">
          ${rec.recording
            ? html`<${E.Button} variant="danger" aria-label="Stop" onClick=${rec.stop}>Stop<//>`
            : html`<${E.Button} variant="secondary" icon="play" aria-label="Record" onClick=${rec.start}>Record<//>`}
          <span className="text-code num audio__timer" aria-live="off">${fmtClock(rec.elapsed)}</span>
          <div className="audio__meter" aria-hidden="true"><span ref=${rec.meterRef} className="audio__meter-fill"></span></div>
        </div>
        ${rec.error && html`<${E.Alert} tone="warning" title="Cannot record">${rec.error}<//>`}
      </div>` : html`<div className=${'audio__drop' + (dragging ? ' is-over' : '')}
          onDragOver=${(e) => { e.preventDefault(); setDragging(true); }} onDragLeave=${() => setDragging(false)}
          onDrop=${(e) => { e.preventDefault(); setDragging(false); take(e.dataTransfer.files && e.dataTransfer.files[0]); }}>
        <span className="text-body muted">Drop an audio file here, or</span>
        <input type="file" className="audio__file" aria-label="Audio file" accept="audio/*,video/webm" onChange=${(e) => { take(e.target.files[0]); e.target.value = ''; }} />
      </div>`}

      ${clip && html`<div className="stack stack--sm audio__clip">
        <audio ref=${audioRef} className="audio__player" controls src=${clipUrl || undefined}
          onLoadedMetadata=${(e) => { const d = e.target.duration; if (Number.isFinite(d)) setClipDuration(d); }}></audio>
        <span className="text-caption muted audio__clip-meta">${[clip.name, fmtBytes(clip.blob.size), clipDuration != null ? fmtClock(clipDuration) : null].filter(Boolean).join(' · ')}</span>
      </div>`}

      <${E.TextField} label="Language" optional aria-label="Language" value=${draft.language} placeholder="en" onChange=${(e) => set({ language: e.target.value })} />
      <div className="row">
        <${E.Button} variant="primary" loading=${busy} disabled=${!clip || busy || rec.recording} aria-label="Transcribe" onClick=${onTranscribe}>${busy && phase === 'convert' ? 'Converting to WAV…' : 'Transcribe'}<//>
        ${!clip && html`<span className="text-caption muted">Record or upload a clip first.</span>`}
      </div>

      ${run && run.error && html`<${E.Alert} tone="danger" title="Transcription failed">${run.error}<//>`}
      ${result && html`<div className="stack audio__result">
        <div className="audio__transcript-wrap">
          <p className="audio__transcript">${result.text || html`<span className="muted">No speech was recognised.</span>`}</p>
          <${E.IconButton} icon="copy" label="Copy transcript" size="sm" variant="secondary"
            onClick=${() => copyText(result.text).then(() => toast({ title: 'Copied' }))} />
        </div>
        <div className="text-caption muted audio__meta">${[result.model, result.language, result.duration != null ? fmtClock(result.duration) + ' of audio' : null, fmtMs(result.ms)].filter(Boolean).join(' · ')}</div>
        ${timingRows(result.timing).length > 0 && html`<div className="row text-caption muted audio__timing">
          ${timingRows(result.timing).map(([k, v]) => html`<span key=${k}>${k} <span className="num">${v}</span></span>`)}
        </div>`}
        ${result.segments.length > 0 && html`<div className="audio__segments">
          ${result.segments.map((s, i) => html`<button key=${i} type="button" className="audio__seg" aria-label=${`Play from ${fmtClock(s.start)}`} onClick=${() => seek(s.start)}>
            <span className="text-code subtle audio__seg-time">[${fmtClock(s.start)} → ${fmtClock(s.end)}]</span>
            <span className="text-body">${s.text}</span>
          </button>`)}
        </div>`}
        <div className="row"><${E.Button} size="sm" variant="ghost" onClick=${() => setRaw(!raw)}>${raw ? 'Hide' : 'Show'} raw response<//></div>
        ${raw && html`<pre className="code text-code decide__code">${result.raw}</pre>`}
      </div>`}
    </div>
  <//>`;
}

/* ---------------------------------------------------------------- round trip */

function RoundTrip({ id, setId, rt, busy, onRun }) {
  const url = useBlobUrl(rt && rt.speech && rt.speech.play);
  const werTone = (w) => (w === 0 ? 'success' : w <= 0.2 ? 'warning' : 'danger');
  return html`<div className="stack">
    <${E.Select} label="Sentence" aria-label="Round trip sentence" size="sm" value=${id} onChange=${(e) => setId(e.target.value)}
      options=${TTS_EXAMPLES.map((x) => ({ value: x.id, label: x.label }))} />
    <div className="row">
      <${E.Button} variant="primary" icon="play" loading=${busy} disabled=${busy} aria-label="Run round trip" onClick=${onRun}>Run round trip<//>
      ${busy && html`<span className="text-caption muted">${rt.step === 'speech' ? 'Speaking…' : 'Transcribing…'}</span>`}
    </div>
    ${rt && rt.error && html`<${E.Alert} tone="danger" title=${`${rt.error.step} failed`}>${rt.error.message}<//>`}
    ${rt && rt.speech && html`<div className="stack audio__rt-result">
      <audio className="audio__player" controls src=${url || undefined}></audio>
      <div><span className="field-label text-label">Said</span><p className="text-body audio__rt-said">${rt.text}</p></div>
      ${rt.transcript && html`<div>
        <span className="field-label text-label">Heard</span>
        <p className="text-body audio__rt-heard">${rt.transcript.text || html`<span className="muted">Nothing recognised.</span>`}</p>
      </div>`}
      ${rt.wer != null && html`<div className="row">
        <${E.Badge} tone=${werTone(rt.wer)} className="audio__wer">Word error rate ${(rt.wer * 100).toFixed(0)}%<//>
      </div>`}
      <div className="text-caption muted">${[
        rt.speech.model ? `Speech ${rt.speech.model}` : 'Speech', fmtMs(rt.speech.ms),
      ].join(' · ')}${rt.transcript ? ` · ${rt.transcript.model ? 'Transcript ' + rt.transcript.model : 'Transcript'} ${fmtMs(rt.transcript.ms)}` : ''}</div>
    </div>`}
  </div>`;
}
