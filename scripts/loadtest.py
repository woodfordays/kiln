#!/usr/bin/env python3
"""Send test chat requests to the Kiln API port so the dashboard has something to show.

Usage: ./scripts/loadtest.py [--url http://localhost:8080] [-n 40] [--max-depth 16000] [--concurrency 1]
Standard library only. Mixes streaming and non-streaming requests, short and long prompts.
"""
import argparse
import json
import random
import threading
import time
import urllib.request

WORDS = ("kiln ember clay glaze fire pottery wheel throw trim bisque cone oxide slip wedge "
         "studio heat cool crack reduction celadon tenmoku shino raku saggar").split()
TASKS = [
    "Summarize the text above in two sentences.",
    "Write a short poem about a wood-fired kiln.",
    "List five glaze recipes with one line each.",
    "Explain the difference between bisque and glaze firing.",
    "Write a Python function that converts cone numbers to temperatures.",
]


def one(url, max_depth, i, lock):
    depth = random.choice([0, 0, 300, 1000, 2500, 5000, max_depth])
    filler = " ".join(random.choice(WORDS) for _ in range(int(depth * 0.75)))
    prompt = (filler + "\n\n" if filler else "") + random.choice(TASKS)
    stream = random.random() < 0.6
    body = {"messages": [{"role": "user", "content": prompt}],
            "max_tokens": random.choice([64, 128, 256, 512]), "stream": stream,
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    t0 = time.time()
    timings = {}
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            if stream:
                for line in r:
                    if line.startswith(b"data:") and b'"timings"' in line:
                        timings = json.loads(line[5:]).get("timings", {})
            else:
                timings = json.load(r).get("timings", {})
        msg = (f"prompt {timings.get('prompt_n', 0):>6} (+{timings.get('cache_n', 0)} cached)  "
               f"gen {timings.get('predicted_n', 0):>4}  "
               f"prefill {timings.get('prompt_per_second', 0):7.0f} t/s  "
               f"decode {timings.get('predicted_per_second', 0):6.1f} t/s")
        if timings.get("draft_n"):
            msg += f"  draft {timings['draft_n_accepted'] / timings['draft_n']:.0%}"
    except Exception as e:
        msg = f"error: {e}"
    with lock:
        print(f"[{i:>3}] {'stream' if stream else 'json  '} {time.time() - t0:6.1f}s  {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("-n", type=int, default=40)
    ap.add_argument("--max-depth", type=int, default=16000)
    ap.add_argument("--concurrency", type=int, default=1)
    a = ap.parse_args()
    lock = threading.Lock()
    sem = threading.Semaphore(a.concurrency)
    threads = []
    for i in range(1, a.n + 1):
        sem.acquire()
        t = threading.Thread(target=lambda i=i: (one(a.url, a.max_depth, i, lock), sem.release()))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    print("done — see http://localhost:8090")


if __name__ == "__main__":
    main()
