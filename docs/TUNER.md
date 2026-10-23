# The tuner

The Tuner page measures flag combinations for one model on your machine and saves the fastest as a
preset. This page explains what it tries, in what order, and how it decides. For the form and the
results screen, see the [user guide](GUIDE.md#tuner).

## How it decides

Every search starts with the **KV cache**: it tries `f16/f16`, `q8_0/q8_0`, `q8_0/q4_0`, `q4_0/q4_0`
at full offload and keeps the first that loads at the target context with at least 512 MiB of VRAM
to spare, so KV precision is never traded for speed (*Every type that fits* keeps all of them as
candidates instead). If nothing fits fully, it repeats with `--fit on` to offload part of the model.
Then, depending on the search:

- **Every combination** (default): runs every combination of the fitting KV types, flash attention
  (`on`, `off`, `auto`; only `on` for a quantized V cache), physical batch `-ub` 256 / 512 / 1024 /
  2048 (plus `-b` variants with *Also vary the logical batch*) and speculative decoding (see below).
  A configuration that doesn't fit skips the ones that need more memory.
  The best three are measured again and ranked by the mean of both runs; the simplest finalist wins
  unless another beats it by more than 2%.
- **One flag at a time**: flash attention (`off`, `auto`), then `-ub`, then each speculative option,
  each change kept only if it beats the current best by more than 2%.

**Speculative decoding.** When the GGUF has an MTP head, `--spec-type draft-mtp` is tried with
`--spec-draft-n-max` 1 to 5. Every draft file the library finds next to the model is tried too, the nearest
match of each kind (DSpark, DFlash, EAGLE-3, a plain draft model) with `--spec-type <kind> -md <file>`:

| Draft | Lengths tried |
|---|---|
| DSpark | 3, 5, 7, 10 |
| DFlash | a quarter, half and all of its block size (3, 7, 15 for a block of 15) |
| EAGLE-3 | 2, 3, 4, 5 |
| Plain draft model | 4, 8, 16 |

DSpark and DFlash clamp the length to the block size the draft was trained with, so nothing longer is run. The
form has a switch for each draft file (off skips it) and for the MTP head, and the API takes `drafts` (a list of
paths) and `speculative`. When a model has both an MTP head and a draft file, both are measured and the faster
wins. A draft file needs VRAM of its own: one that doesn't fit next to the model is recorded as not fitting
(and, in the grid, rules out the longer lengths of the same file), not silently shrunk. The saved preset carries
`--spec-type`, `-md` and `--spec-draft-n-max`, so the *Speculative decoding* choice in the preset editor shows it.

**Optimize for: Context length** works differently. The target context is the requirement, and
model weights may spill out of VRAM into system RAM to reach it, so the KV cache and the full
context stay on the GPU:

1. **Spill**: for each `-ub` (and each KV type, if *Every type that fits* is chosen), find the
   smallest spill that loads with 512 MiB of VRAM to spare (plus 128 MiB the benchmark itself
   needs). MoE models spill expert weights with `--n-cpu-moe N` (attention and KV stay on the
   GPU); dense models spill whole layers with `-ngl`.
   N is found by bisection with load-only probes (a few seconds each), starting each batch size
   from the previous answer. Spill is capped by free system RAM (keeping 4 GiB).
2. **Benchmark** the smallest spill for each batch size.
3. **Threads.** The CPU reads the weights that sit in system RAM, so the thread count shows up in the
   benchmark. On the fastest spill the tuner measures `-t` at half and three quarters of llama.cpp's default
   (the count in the server's own log, `-t -1`), at every logical CPU, and at any `-t` or `-tb` the base
   preset used. A fixed `-t` that beats the default by more than 2% is kept; then `-tb` (prompt processing,
   which follows `-t` unless set) is tried at the default and at every logical CPU. Fewer threads can win
   when memory bandwidth is the limit, and the extra logical CPUs tend to help prefill. If the default is
   best, no thread flags are saved. Only this objective touches threads: with the others, `-t` and `-tb`
   from the base preset carry over untouched.
4. **Speculative decoding** (the MTP head and any draft files) on the best configuration so far. A draft
   that needs more VRAM than is left spills a layer or two more, as a benchmark does, and the longer lengths
   keep that step.
5. Re-run the top three. Scored like balanced, since long prompts are the point.

Every candidate runs a full-batch warm-up, three fixed 128-token generations and a fixed deep prompt
(seeded filler cut to an exact token count) twice, with temperature 0, seed 42, `ignore_eos`
and the prompt cache off. `--fit off` stops llama.cpp from quietly shrinking a configuration. Scores:
generation = geometric mean of short and deep decode t/s; prompt processing = deep prefill t/s;
balanced = decode^0.7 × prefill^0.3. A later candidate replaces the current one only above +2%, so
run-to-run noise (under 1% in testing) doesn't change the outcome. The tuner stops the running
model while it works and starts the winner, or the previous model, afterwards. Every trial is
kept on the Tuner page.
