#!/usr/bin/env python3
"""A stand-in for llama-server: the same command line and HTTP surface Kiln uses, with canned answers.

FAKE_LOAD_S delays /health. A model path containing "crash" makes it exit with code 3 while loading.
/completion answers with timings worked out from the command line, so the tuner has something to optimise:
decode is fastest at -t 8 (the fake CPU "chooses" FAKE_AUTO_THREADS, default 12, for -t -1), prompt
processing grows with -tb up to 24, and speculative decoding speeds decode up, most with a DSpark draft
file at --spec-draft-n-max 7.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HELP = """----- common params -----

""" + "\n".join(f"{spec:<40}{desc}" for spec, desc in [
    ("-c,    --ctx-size N", "size of the prompt context (default: 0)"),
    ("-np,   --parallel N", "number of slots (default: -1)"),
    ("-ngl,  --gpu-layers N", "layers on the GPU"),
    ("-ctk,  --cache-type-k TYPE", "KV cache type for K"),
    ("--embeddings", "embedding mode"),
    ("--rerank", "reranking mode"),
    ("--pooling {none,mean,cls,last,rank}", "pooling type"),
    ("--alias STRING", "model name aliases"),
    ("--mmproj FILE", "multimodal projector"),
    ("--spec-type TYPE", "speculative decoding type"),
    ("--spec-draft-model, -md FNAME", "draft model"),
    ("--override-kv KEY=TYPE:VALUE", "override metadata"),
])

if "--help" in sys.argv:
    print(HELP)
    sys.exit(0)
if "--version" in sys.argv:
    print("version: 0.0.0-fake (build 1)")
    sys.exit(0)
if "--list-devices" in sys.argv:
    print("Available devices:\n  Fake0: Fake GPU (1000 MiB, 900 MiB free)")
    sys.exit(0)

args = sys.argv[1:]


def opt(*names, default=None):
    for n in names:
        if n in args and args.index(n) + 1 < len(args):
            return args[args.index(n) + 1]
    return default


MODEL = opt("-m", "--model", default="")
PORT = int(opt("--port", default="8081"))
ALIAS = opt("-a", "--alias", default=MODEL)
CTX = int(opt("-c", "--ctx-size", default="4096"))
LOAD_S = float(os.environ.get("FAKE_LOAD_S", "0.3"))
NP = max(1, int(opt("-np", "--parallel", default="1")))
EMBED = "--embeddings" in args or "--embedding" in args
T0 = time.time()
LOG = os.environ.get("FAKE_LOG")

AUTO_THREADS = int(os.environ.get("FAKE_AUTO_THREADS", "12"))
THREADS = int(opt("-t", "--threads", default=AUTO_THREADS))
THREADS_BATCH = int(opt("-tb", "--threads-batch", default=THREADS))
SPEC = opt("--spec-type")
DRAFT_FILE = opt("-md", "--spec-draft-model", "--model-draft")
SPEC_N = int(opt("--spec-draft-n-max", default="3"))

print(f"fake llama-server: {MODEL} on {PORT} alias={ALIAS}", flush=True)
print(f"llama threadpool init, n_threads = {THREADS}", flush=True)
for line in ("load_tensors:      Vulkan0 model buffer size =  1234.00 MiB",
             "load_tensors:   CPU_Mapped model buffer size =    50.00 MiB",
             "llama_kv_cache:    Vulkan0 KV buffer size =   256.00 MiB",
             "sched_reserve:    Vulkan0 compute buffer size =   300.00 MiB"):
    print(line, flush=True)


def completion_timings(n_predict: int, n_prompt: int) -> dict:
    decode = 20.0 / (1 + 0.05 * abs(THREADS - 8))
    prefill = 400.0 * (0.5 + 0.5 * min(THREADS_BATCH, 24) / 24)
    drafted = accepted = 0
    if SPEC and (SPEC == "draft-mtp" or DRAFT_FILE):
        n = SPEC_N
        gain = {"draft-mtp": 1 + 0.1 * min(n, 5), "draft-dspark": 1 + 0.2 * min(n, 7) - 0.15 * max(0, n - 7),
                "draft-dflash": 1 + 0.12 * n}.get(SPEC, 1 + 0.05 * n)
        decode *= gain
        drafted = n_predict // 2
        accepted = round(drafted * max(0.1, 0.9 - 0.04 * n))
    return {"prompt_n": n_prompt, "prompt_per_second": prefill, "predicted_n": n_predict, "predicted_per_second": decode,
            "draft_n": drafted, "draft_n_accepted": accepted}
# A stand-in for the built-in chat page: like the real one, it only uses relative URLs.
UI_HTML = b"""<!doctype html><meta charset="utf-8"><title>llama.cpp - chat</title>
<link rel="stylesheet" href="./_app/app.css"><div id="model"></div><div id="reply"></div>
<script type="module" src="./_app/app.js"></script>"""
UI_CSS = b"body{font:14px sans-serif}"
UI_JS = b"""const props = await (await fetch('./props')).json();
document.getElementById('model').textContent = props.model_alias;
const r = await fetch('./v1/chat/completions', {method: 'POST', headers: {'content-type': 'application/json'},
  body: JSON.stringify({stream: true, messages: [{role: 'user', content: 'hello'}]})});
const reader = r.body.getReader(), dec = new TextDecoder();
let text = '', buf = '';
for (;;) {
  const {done, value} = await reader.read();
  if (done) break;
  buf += dec.decode(value, {stream: true});
  const lines = buf.split('\\n'); buf = lines.pop();
  for (const l of lines) if (l.startsWith('data: {')) text += JSON.parse(l.slice(6)).choices[0].delta.content || '';
}
document.getElementById('reply').textContent = text;
document.body.dataset.done = '1';
"""
UI = {"/": (UI_HTML, "text/html; charset=utf-8"), "/index.html": (UI_HTML, "text/html; charset=utf-8"),
      "/_app/app.css": (UI_CSS, "text/css"), "/_app/app.js": (UI_JS, "text/javascript")}

if "crash" in MODEL:
    time.sleep(0.2)
    print("error: failed to load model (out of memory)", flush=True)
    sys.exit(3)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw) if raw else {}

    def do_GET(self):
        if self.path.startswith("/health"):
            if time.time() - T0 < LOAD_S:
                return self._send(503, {"error": {"message": "Loading model"}})
            return self._send(200, {"status": "ok"})
        if self.path.startswith("/props"):
            return self._send(200, {"default_generation_settings": {"n_ctx": CTX}, "total_slots": NP,
                                    "build_info": "fake-1", "modalities": {"vision": False}, "model_alias": ALIAS})
        if self.path.startswith("/slots"):
            cell, t = CTX // NP, int(time.time() - T0)
            return self._send(200, [{"id": i, "n_ctx": cell, "is_processing": i == 0,
                                     "n_prompt_tokens": (t * 37 + i * cell // 3) % cell,
                                     "next_token": [{"n_decoded": (t * 5 + i) % 200}]} for i in range(NP)])
        if self.path.startswith("/metrics"):
            text = ("# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.\n"
                    "# TYPE llamacpp:prompt_tokens_total counter\n"
                    "llamacpp:prompt_tokens_total 5\n"
                    "# HELP llamacpp:requests_processing Number of requests processing.\n"
                    "# TYPE llamacpp:requests_processing gauge\n"
                    "llamacpp:requests_processing 0\n")
            return self._send(200, text.encode(), "text/plain; version=0.0.4")
        if self.path.split("?")[0] in UI:
            return self._send(200, *UI[self.path.split("?")[0]])
        if self.path.startswith("/v1/models"):
            return self._send(200, {"object": "list", "data": [{"id": ALIAS, "object": "model", "created": 1, "owned_by": "llamacpp"}]})
        return self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        body = self._body()
        if LOG:
            with open(LOG, "a") as f:
                f.write(json.dumps({"alias": ALIAS, "path": self.path, "body": body}) + "\n")
        if self.path == "/completion":
            prompt = body.get("prompt")
            n_prompt = len(prompt) if isinstance(prompt, list) else len(str(prompt or "")) // 4 + 1
            return self._send(200, {"content": "x" * 8, "stop": True,
                                    "timings": completion_timings(int(body.get("n_predict") or 8), n_prompt)})
        if self.path == "/v1/chat/completions":
            delay = float(body.get("fake_delay", 0))
            time.sleep(delay)
            timings = {"prompt_n": 5, "cache_n": 0, "prompt_ms": 10.0, "predicted_n": 3, "predicted_ms": 30.0,
                       "prompt_per_second": 500.0, "predicted_per_second": 100.0}
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                chunks = [f"data: {json.dumps({'choices': [{'delta': {'content': 'hi'}}], 'model': ALIAS})}\n\n",
                          f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'timings': timings, 'model': ALIAS})}\n\n",
                          "data: [DONE]\n\n"]
                for c in chunks:
                    b = c.encode()
                    self.wfile.write(f"{len(b):x}\r\n".encode() + b + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                return
            return self._send(200, {"model": ALIAS, "choices": [{"message": {"role": "assistant", "content": f"hi from {ALIAS}"},
                                                                  "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}, "timings": timings})
        if self.path in ("/v1/embeddings", "/embeddings") and not body.get("per_token"):
            if self.path == "/embeddings" and "--pooling" in args and opt("--pooling") == "none":
                n = len(body["content"]) if isinstance(body.get("content"), list) else 4
                return self._send(200, [{"index": 0, "embedding": [[float(i % 7), 0.5, -0.25, 1.0] for i in range(n)]}])
            return self._send(200, {"model": ALIAS, "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}]})
        if self.path == "/v1/systemone":
            return self._send(501, {"error": {"code": 501, "message": "This model is not a decision model"}})
        if self.path == "/apply-template":
            msgs = body.get("messages", [])
            text = "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in msgs) + "<|im_start|>assistant\n"
            return self._send(200, {"prompt": text})
        if self.path == "/tokenize":
            text = body.get("content", "")
            toks, i = [], 0
            while i < len(text):
                if text.startswith("<|fim_suffix|>", i):
                    toks.append(999)
                    i += len("<|fim_suffix|>")
                else:
                    toks.append(ord(text[i]) % 200)
                    i += 1
            return self._send(200, {"tokens": toks})
        return self._send(404, {"error": {"message": "not found"}})


ThreadingHTTPServer.daemon_threads = True
srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
import signal
signal.signal(signal.SIGTERM, lambda *a: os._exit(0))
while True:
    time.sleep(0.5)
