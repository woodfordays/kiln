#!/usr/bin/env python3
"""A stand-in for audio.cpp's audiocpp_server: the same command line and HTTP surface Kiln uses, with canned answers.

It reads the --config JSON file (host, port, backend and the models list) and answers /health, /v1/models,
/v1/audio/voices, /v1/audio/speech and the transcription routes. Speech comes back as a silent WAV whose length
follows the input text; transcription reports how many bytes it was sent. FAKE_LOAD_S delays /health.
A model path containing "crash" makes it exit with code 3 while loading. FAKE_LOG, when set, gets one JSON
line with the command line and the config.
"""
import base64
import json
import os
import signal
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.parser import BytesParser
from email.policy import default as default_policy
from urllib.parse import parse_qs, urlsplit

HELP = """usage: audiocpp_server --config <file.json> [--no-ui] [other flags are ignored]

Serves the audio.cpp models described by the config file over HTTP.

  --config FILE    JSON config: host, port, backend and the models list
  --no-ui          accepted and ignored
  --help           show this help and exit
"""

if "--help" in sys.argv:
    print(HELP, end="")
    sys.exit(0)

args = sys.argv[1:]


def opt(*names, default=None):
    for n in names:
        if n in args and args.index(n) + 1 < len(args):
            return args[args.index(n) + 1]
    return default


CONFIG_PATH = opt("--config")
if not CONFIG_PATH:
    print("fake audiocpp_server: --config <file.json> is required", file=sys.stderr, flush=True)
    sys.exit(2)
with open(CONFIG_PATH) as f:
    cfg = json.load(f)

HOST = cfg.get("host", "127.0.0.1")
PORT = int(cfg.get("port", 8080))
BACKEND = cfg.get("backend", "cpu")
MODELS = cfg.get("models") or []
BY_ID = {m["id"]: m for m in MODELS}
LOAD_S = float(os.environ.get("FAKE_LOAD_S", "0.3"))
T0 = time.time()
LOG = os.environ.get("FAKE_LOG")

if LOG:
    with open(LOG, "a") as f:
        f.write(json.dumps({"argv": sys.argv, "config": cfg}) + "\n")
for m in MODELS:
    print(f"fake audiocpp_server: {m['id']} family={m.get('family')} task={m.get('task')} "
          f"backend={BACKEND} on {PORT}", flush=True)
if any("crash" in str(m.get("path", "")) for m in MODELS):
    time.sleep(0.2)
    print("error: failed to load model (out of memory)", flush=True)
    sys.exit(3)


def wav_bytes(n_samples: int) -> bytes:
    """A 44-byte RIFF header for 16-bit mono 24 kHz audio, then n_samples of silence."""
    data = bytes(2 * n_samples)
    rate, channels, bits = 24000, 1, 16
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate,
                                   rate * channels * bits // 8, channels * bits // 8, bits)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def parse_multipart(ctype: str, raw: bytes):
    """Split a multipart/form-data body into (text fields, file fields) keyed by field name."""
    msg = BytesParser(policy=default_policy).parsebytes(
        b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
    fields, files = {}, {}
    if not msg.is_multipart():
        return fields, files
    for part in msg.get_payload():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if part.get_filename() is not None:
            files[name] = payload
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, files


def model_for(model_id, task):
    m = BY_ID.get(model_id) if model_id else None
    return m if m is not None and m.get("task") == task else None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json", headers=None):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, message, etype="invalid_request_error"):
        return self._send(code, {"error": {"message": message, "type": etype}})

    def _raw(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json(self, raw):
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def do_GET(self):
        print(f"GET {self.path}", flush=True)
        url = urlsplit(self.path)
        if url.path == "/health":
            if time.time() - T0 < LOAD_S:
                return self._send(503, {"status": "loading"})
            return self._send(200, {"status": "ok", "models": len(MODELS)})
        if url.path == "/v1/models":
            data = [{"id": m["id"], "object": "model", "owned_by": "audio.cpp", "configured": True, "device": 0}
                    for m in MODELS]
            return self._send(200, {"object": "list", "data": data})
        if url.path == "/v1/audio/voices":
            model_id = (parse_qs(url.query).get("model") or [""])[0]
            if not model_id and len(MODELS) == 1:
                model_id = MODELS[0]["id"]
            voices = ["alba", "cosette"] if model_id in BY_ID else []
            return self._send(200, {"voices": voices})
        return self._error(404, "not found", "not_found")

    def do_POST(self):
        print(f"POST {self.path}", flush=True)
        path = urlsplit(self.path).path
        raw = self._raw()
        if path == "/v1/audio/speech":
            return self._speech(raw)
        if path in ("/v1/audio/transcriptions", "/v1/audio/transcriptions/details"):
            return self._transcribe(raw, detailed=path.endswith("/details"))
        return self._error(404, "not found", "not_found")

    def _speech(self, raw):
        body = self._json(raw)
        if body is None:
            return self._error(400, "invalid JSON body")
        model_id = body.get("model")
        m = model_for(model_id, "tts")
        if m is None:
            return self._error(400, f"unknown model '{model_id or ''}'")
        text = body.get("input")
        if not isinstance(text, str) or not text:
            return self._error(400, "missing input")
        voice = body.get("voice") or (m.get("default_voice_preset") or {}).get("voice_id") or ""
        headers = {"X-Fake-Voice": voice}
        wav = wav_bytes(len(text))
        if body.get("response_format") == "json":
            return self._send(200, {"audio": base64.b64encode(wav).decode(), "sample_rate": 24000},
                              headers=headers)
        return self._send(200, wav, "audio/wav", headers=headers)

    def _transcribe(self, raw, detailed):
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            fields, files = parse_multipart(ctype, raw)
            model_id = fields.get("model", "")
            language = fields.get("language") or None
            audio = files.get("file")
            missing = "missing file"
        else:
            body = self._json(raw)
            if body is None:
                return self._error(400, "invalid JSON body")
            model_id = body.get("model", "")
            language = body.get("language") or None
            encoded = body.get("audio")
            audio = None
            if isinstance(encoded, str):
                try:
                    audio = base64.b64decode(encoded, validate=True)
                except ValueError:
                    audio = encoded.encode()
            missing = "missing audio"
        if model_for(model_id, "asr") is None:
            return self._error(400, f"unknown model '{model_id or ''}'")
        if audio is None:
            return self._error(400, missing)
        out = {"text": f"heard {len(audio)} bytes" + (f" in {language}" if language else ""),
               "timing": {"wall_ms": 1.0, "audio_duration_ms": 10.0, "rtf": 0.1}}
        if detailed:
            out["language"] = language
        return self._send(200, out)


ThreadingHTTPServer.daemon_threads = True
srv = ThreadingHTTPServer((HOST, PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
signal.signal(signal.SIGTERM, lambda *a: os._exit(0))
while True:
    time.sleep(0.5)
