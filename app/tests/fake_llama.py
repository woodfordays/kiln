#!/usr/bin/env python3
"""A stand-in for llama-server: the same command line and HTTP surface Kiln uses, with canned answers.

FAKE_LOAD_S delays /health. A model path containing "crash" makes it exit with code 3 while loading.
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
EMBED = "--embeddings" in args or "--embedding" in args
T0 = time.time()
LOG = os.environ.get("FAKE_LOG")

print(f"fake llama-server: {MODEL} on {PORT} alias={ALIAS}", flush=True)
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
            return self._send(200, {"default_generation_settings": {"n_ctx": CTX}, "total_slots": 1,
                                    "build_info": "fake-1", "modalities": {"vision": False}, "model_alias": ALIAS})
        if self.path.startswith("/v1/models"):
            return self._send(200, {"object": "list", "data": [{"id": ALIAS, "object": "model", "created": 1, "owned_by": "llamacpp"}]})
        return self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        body = self._body()
        if LOG:
            with open(LOG, "a") as f:
                f.write(json.dumps({"alias": ALIAS, "path": self.path, "body": body}) + "\n")
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
