# Running several models

Kiln runs each model as its own `llama-server` process, so a chat model can sit next to an embedding
model, a reranker or a decision model when they fit in GPU memory. This page covers how requests find
the right model, how Kiln decides whether another model fits, loading models on demand, idle unloading,
decision models, and the extra files (vision projectors, speculative-decoding drafts) that come with some
models. For the screens that control all this, see the [user guide](GUIDE.md).

## Routing, fit and swapping

Each running model is its own `llama-server` process on its own internal port. The API port routes
every request:

1. **By name.** The request's `model` field is matched, ignoring case, against a running model's API
   name (set on the preset, otherwise its name), the preset name, its slug, any `--alias` flag, and
   finally the model file's name. `X-Kiln-Model: <name>` or `?model=<name>` works too, and every response
   says which model answered in `X-Kiln-Model`.
2. **By endpoint.** `/v1/embeddings` goes to the embedding model, `/v1/rerank` to the reranker,
   `/v1/systemone` to the decision model.
3. **Otherwise** to the chat model used last. A name that matches nothing (`gpt-4o`, `default`) falls
   through to that, as before, so existing clients keep working.

`GET /v1/models` lists what is loaded, and (with swapping on) every preset that could be.

**A preset has a role**: *Chat*, *Embeddings* (Kiln adds `--embeddings`), *Reranker* (`--rerank`) or
*Decision*. The role decides the flags Kiln adds and which paths the model serves; the Server page shows
the exact command line. New presets pick the role from the model (the library marks embedding, reranker
and decision models). A preset can also be **pinned**, so it is never stopped to make room.

**Do they fit?** Before another model starts, Kiln estimates what it needs from the GGUF header and
the flags (weights, KV cache with sliding-window and hybrid layers accounted for, compute buffer,
projector) and compares it with the VRAM that is free right now, keeping 512 MiB spare. After a model
has loaded on its own, the measured footprint replaces the estimate for that model and those flags.
Against the 87 successful trials the tuner has recorded on one AMD box, the estimate was within -9%
to +11% of the VRAM actually used, 1.6% high on average. If it doesn't fit, Start offers to stop idle,
unpinned models (least recently used first), or to start anyway. `--fit on`, llama.cpp's default,
shrinks a model to whatever is free instead of refusing; add `--fit off` for exact settings. Without
GPU telemetry Kiln can't check and starts the model.

**Swap on request** (Settings, on by default). When a request names a preset that isn't running, Kiln
loads it first: it waits for running requests on a model to finish (up to 60 s) before stopping it to
make room, shares one load between requests that want the same preset, and answers when the model is ready. Streaming
requests get SSE comment lines while they wait, so a long load doesn't hit a first-byte timeout, and a
failure arrives as an error event. The Requests page shows the load time in its own column and keeps it
out of time to first token. Settings also caps how many models run at once, and lets several presets
start on boot, one after another. The API port has no auth of its own, so anyone who can reach it can
make Kiln load any preset: a preset with `--api-key` is only loaded (and only listed in `/v1/models`) for
requests that carry one of its keys, and you can turn swapping off in Settings.

**Idle timeout** (Settings, 2 hours by default, or never). A model that no completion, embedding, rerank or
decision request has used for that long is stopped, so a forgotten model doesn't hold the GPU all night.
Health checks and `/props` don't count as use, models with a request running are left alone, and a preset
set to **Keep loaded** is never unloaded. With swap on request, the next request that names the preset loads
it again.

The tuner stops everything for the GPU and starts what was running again when it finishes.

## Decision models

A decision model reads a request (a `state` and typed `questions`) and scores each question's options
in one forward pass. It generates no text. The API is llama.cpp's `/v1/systemone`:

```bash
curl http://localhost:8080/v1/systemone -H 'Content-Type: application/json' -d '{
  "state": "My integration keeps failing with a 401 since we rotated our API key. Please help.",
  "questions": {
    "team":    {"type": "choice", "instructions": "Which team should handle this request?",
                "criteria": {"billing": "Charges and refunds", "support": "Technical integration errors",
                             "sales": "Questions about buying a product"}},
    "urgent":  {"type": "noul", "instructions": "Is the customer blocked right now?"},
    "mood":    {"type": "score", "criteria": ["calm", "mildly annoyed", "frustrated", "angry"]}
  }}'
```

- Models whose GGUF carries llama.cpp's `<arch>.decision.type` (OpenJev, lev, Laya, Kev and the like) are
  served by `llama-server` itself. Kiln routes and records the requests.
- **Perplexity's pplx-decider checkpoints (protocol v3)**, such as `decider-flash-v1.2`, are not: stock
  `llama-server` answers `501 This model is not a decision model`, because they have no language-model
  head and need non-causal attention and a readout at each question's marker. Kiln serves them itself.
  Give the preset the **Decision** role and Kiln starts `llama-server` with `--embeddings --pooling none`,
  `attention.causal=false` and a micro-batch that holds the whole prompt, then builds the checkpoint's
  joint prompt, asks `llama-server` for the hidden state at every position, multiplies the state at each
  `<|fim_suffix|>` marker with that question's rows of the checkpoint's readout (read from `output.weight`
  in the GGUF), divides by the calibration temperature in the GGUF and takes a softmax. The answer
  follows the model card's response shape: `answers` per question (`choice`, `probabilities`,
  `confidence`; `noul`; `score`, `probabilities`, `legend`, `confidence`) and `usage`. `confidence` is the
  probability of the top option.

  Checked against the model card's own example on the real `llama-server`: `team` = support (99.8%),
  `urgent` = true (97%), and a refund request routes to billing, a pricing question to sales. Limits to
  know about:
  - **Text only.** `llama-server` decodes image chunks separately, but this checkpoint has to see the
    whole prompt, image included, in one micro-batch, so requests with `images` get a 400 and the
    projector isn't loaded.
  - The prompt layout is the community reproduction of the reference encoder published with
    `Jakevin/pplx-decider-flash-v1.2-GGUF` (Apache-2.0); Perplexity's own source for protocol v3 is in a private
    repository. It agrees with the model card's description and gave sensible answers in testing, but
    it is not the reference implementation. Treat probabilities from here as unverified against Perplexity's.
  - The whole prompt must fit the context (`-c`, 4096 unless you set it). `llama-server` returns one
    vector per token, so very long states are slow and use memory.

## Speculative decoding and other model files

Models often come with extra files, and the library groups them with the model they belong to:

| File | Flag | How Kiln finds it |
|---|---|---|
| Vision projector (`mmproj-*.gguf`) | `--mmproj` | GGUF architecture `clip`, nearest name first |
| **MTP** head | `--spec-type draft-mtp` | built into the model; the GGUF has `nextn_predict_layers` |
| **DSpark**, DFlash, EAGLE-3 or a plain draft model | `--spec-type draft-dspark` (…) with `-md <file>` | a `dflash`/`eagle` architecture file, or "draft" in the name, next to the model |

In the preset editor, **Model features** has a switch for vision and a *Speculative decoding* choice
(off, MTP, or each draft file found), and the Presets list shows the same as clickable chips. Switching a
feature off doesn't delete anything: its flags stay in the preset, greyed out and skipped (every flag
row also has its own checkbox), so switching it back on restores `--spec-draft-n-max` and the rest as
they were. If the preset's model is running, the toast offers a restart.

Verified on the real `llama-server` (b11699, on CPU, with a short greedy generation):

| Model | Setting | Decode |
|---|---|---|
| Qwen3.8 27B | MTP on (`--spec-draft-n-max 3`) | 2.88 t/s, 26 of 37 drafted tokens accepted |
| Qwen3.8 27B | MTP off | 1.88 t/s, no `--spec-type` on the command line |
| MiniCPM-V 4.7 35B-A3B + its DSpark file | DSpark on (`-md`, `--spec-draft-n-max 7`) | 17.6 t/s, 41 of 41 accepted |
| MiniCPM-V 4.7 35B-A3B | DSpark off | 11.4 t/s, no `-md` on the command line |

The counting prompt is easy to draft, so the acceptance rates flatter; use the tuner or your own
traffic for real numbers. `--spec-type draft-dspark` and its siblings need a recent llama.cpp
(`LLAMA_CPP_REF=b11699` was used here). DFlash and DSpark clamp `--spec-draft-n-max` to the draft's
block size. The tuner still tries only MTP.
