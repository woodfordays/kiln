# The tuner

The Tuner page measures flag combinations for one model on your machine and saves the fastest as a
preset. This page explains what it tries, in what order, and how it decides. For the form and the
results screen, see the [user guide](GUIDE.md#tuner).

## How it decides

With the search space on Auto (see [Search space](#search-space)), every search starts with the
**KV cache**: it tries `f16/f16`, `q8_0/q8_0`, `q8_0/q4_0`, `q4_0/q4_0` at full offload and keeps the
first that loads at the target context with at least 512 MiB of VRAM to spare, so KV precision is never
traded for speed (*Every type that fits* keeps all of them as candidates instead). With GPU layers on
Auto, if nothing fits fully it repeats with `--fit on` to offload part of the model.
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
`--spec-draft-n-max` 1 to 5 (*Draft max* on Auto). Every draft file the library finds next to the model is tried too, the nearest
match of each kind (DSpark, DFlash, EAGLE-3, a plain draft model) with `--spec-type <kind> -md <file>`:

| Draft | Lengths tried |
|---|---|
| DSpark | 3, 5, 7, 10 |
| DFlash | a quarter, half and all of its block size (3, 7, 15 for a block of 15) |
| EAGLE-3 | 2, 3, 4, 5 |
| Plain draft model | 4, 8, 16 |

DSpark and DFlash clamp the length to the block size the draft was trained with, so nothing longer is run. The
form has a switch for each draft file (off skips it) and for the MTP head, and the API takes `drafts` (a list of
paths), `speculative` and `space`. When a model has both an MTP head and a draft file, both are measured and the faster
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
   best, no thread flags are saved. On Auto, only this objective touches threads: with the others, `-t` and `-tb`
   from the base preset carry over untouched.
4. **Speculative decoding** (the MTP head and any draft files) on the best configuration so far. A draft
   that needs more VRAM than is left spills a layer or two more, as a benchmark does, and the longer lengths
   keep that step.
5. Re-run the top three. Scored like balanced, since long prompts are the point.

Every candidate runs a full-batch warm-up, three fixed 128-token generations and a fixed deep prompt
(seeded filler cut to an exact token count) twice, with temperature 0, seed 42, `ignore_eos`
and the prompt cache off. `--fit off` (except GPU layers on `auto`, which runs `--fit on`) stops llama.cpp from quietly shrinking
a configuration. Scores:
generation = geometric mean of short and deep decode t/s; prompt processing = deep prefill t/s;
balanced = decode^0.7 × prefill^0.3. A later candidate replaces the current one only above +2%, so
run-to-run noise (under 1% in testing) doesn't change the outcome. The tuner stops the running
model while it works and starts the winner, or the previous model, afterwards. Every trial is
kept on the Tuner page.

## Search space

The **Search space** disclosure under the form (closed until opened; its summary says how many settings
are changed) sets what the search may vary. Each setting has one of four modes:

- **Auto**: the tuner's own behaviour, as described above. Every setting starts here.
- **Off**: held out of the search. The setting takes its *Off holds* value in every candidate, or its
  flag is left out.
- **Fixed**: one value, applied to every candidate.
- **Search**: the listed values, each tried in turn. The fit stage and the speed search use them
  differently (below).

With every setting on Auto the run is the same as before: the same candidates in the same order, the
same count and the same preset. Auto passes no flag for a setting the tuner never searched, so a base
preset's value carries over. Off, Fixed and Search remove that flag from the base preset, so only the
search's value is used.

Speculative decoding has only Auto and Off. Off means no speculative options at all, the same as
switching off the MTP head and every draft file.

**Values.** The boxes under Fixed and Search take:

- Comma lists: `256, 512, 1024`. Choice settings take their names (`f16, q8_0`); Draft p-min takes
  numbers from 0 to 1 (`0.1, 0.5`).
- Whole-number ranges: `lo..hi` is every whole number from `lo` to `hi`. `lo..hi +s` steps by `s`, and
  `lo..hi xf` multiplies by `f`, so `256..2048 x2` is 256, 512, 1024, 2048. Ranges and single values mix
  freely: `256..1024, 2048`. GPU layers also takes `auto`.
- Text settings (Tensor split, Override tensor) take one value per line, or separated by `;`, because a
  tensor split contains commas: `3,1; 1,1`.

Values are checked as you type. A bad one is named under its box and disables Start. Duplicates are
dropped, keeping the first. A setting takes up to 32 values, and Fixed takes exactly one.

### Memory and offload

| Setting | Flag | Auto | Off holds |
|---|---|---|---|
| Context | `-c` | The target context | The target context |
| GPU layers | `-ngl` | Full offload (`99`), then `--fit on` (`auto`) | `99` |
| Experts in RAM | `--n-cpu-moe` | Not used; the context length objective bisects it for MoE models | Flag left out |
| Split mode | `-sm` | Not passed | Flag left out |
| Tensor split | `-ts` | Not passed | Flag left out |
| Main GPU | `-mg` | Not passed | Flag left out |
| KV offload | `-kvo` / `-nkvo` | Not passed | Flag left out |
| Op offload | `--op-offload` / `--no-op-offload` | Not passed | Flag left out |
| Override tensor | `-ot` | Not passed | Flag left out |

### KV cache

| Setting | Flag | Auto | Off holds |
|---|---|---|---|
| K cache | `-ctk` | The KV ladder (above) | `f16` |
| V cache | `-ctv` | The KV ladder (above) | `f16` |
| Flash attention | `-fa` | `on`, `off` and `auto` in the grid when V is f16, else `on` | `on` |
| Parallel slots | `-np` | `1` | `1` |
| Unified KV | `--kv-unified` / `--no-kv-unified` | Not passed | Flag left out |
| Full SWA cache | `--swa-full` | Not passed | Flag left out |
| Cache reuse | `--cache-reuse` | Not passed | Flag left out |

### Batching and CPU

| Setting | Flag | Auto | Off holds |
|---|---|---|---|
| Micro-batch | `-ub` | `256`, `512`, `1024`, `2048` | `512` |
| Logical batch | `-b` | The larger of 2048 and the micro-batch; with *Also vary the logical batch*, also the micro-batch and 4096 | `2048` |
| Threads | `-t` | Context length: today's thread sweep. Other objectives: the base preset's `-t` is kept | Flag left out |
| Batch threads | `-tb` | As threads | Flag left out |
| Load mode | `-lm` | Not passed | Flag left out |
| NUMA | `--numa` | Not passed | Flag left out |
| Weight repacking | `--repack` / `--no-repack` | Not passed | Flag left out |
| Polling | `--poll` | Not passed | Flag left out |
| Priority | `--prio` | Not passed | Flag left out |

### Speculative decoding

| Setting | Flag | Auto | Off holds |
|---|---|---|---|
| Speculative | `--spec-type`, `-md` | The MTP head and each draft file, as above | No speculative options |
| Draft max | `--spec-draft-n-max` | The lengths above: MTP 1 to 5, and each draft file's | `3` (llama.cpp's default) |
| Draft min | `--spec-draft-n-min` | Not passed | Flag left out |
| Draft p-min | `--spec-draft-p-min` | Not passed | Flag left out |
| Draft GPU layers | `-ngld` | Not passed | Flag left out |
| Draft K cache | `-ctkd` | Not passed | Flag left out |
| Draft V cache | `-ctvd` | Not passed | Flag left out |

Draft max as a list replaces the lengths for every speculative option. For a draft file each value is
capped at its block size, and duplicates are dropped. Draft min and Draft p-min apply to every
speculative option. Draft GPU layers and the draft K and V caches apply only to options that use a draft
file, not to the MTP head.

### How the values are searched

**Memory-first settings** are context, GPU layers, experts in RAM and the K and V caches. They decide what
fits, so the fit stage tries them in preference order and keeps the first combination that loads. None of
them is traded for speed:

1. Context, largest first.
2. GPU layers, in the order given (Auto: `99`, then `auto`).
3. Experts in RAM, in the order given, for each GPU layers value.
4. K and V types, most precise pair first. *Most precise type that fits* stops at the first pair that
   loads; *Every type that fits* keeps every pair that loads. If only one of K and V is set, the other
   tries `f16`, `q8_0` and `q4_0`.

The stage stops at the first context, then the first GPU layers and experts combination, that loads. The
fit trial uses flash attention `on` when that is a candidate, and every other setting at its first value.

**Everything else is searched for speed,** after that combination is found. In *Every combination* the
Search lists are crossed with each other, the KV pairs that fit and the speculative options. In *One flag
at a time* each Search setting gets a stage of its own, kept only for a win of more than 2%.

**Context length** uses its own spill search (above). With Fixed or Search on GPU layers or experts in
RAM, those values are probed in order of increasing spill (the fewest layers in system RAM first), and
the first that loads is benchmarked. Nothing is bisected. Off on that axis means no spill along it: an
MoE model with experts in RAM off spills layers instead, and with both off nothing spills.

**Pruning** removes combinations before they run:

- **Didn't fit.** A combination that fails to load rules out the ones that need at least as much memory:
  the same values for every setting that uses VRAM, except that micro-batch, logical batch and draft GPU
  layers may be the same or larger, and a draft may be the same length or longer. A failed combination
  without speculative decoding rules out every speculative one. Threads, NUMA, polling, priority, cache
  reuse, load mode, weight repacking, and draft min and p-min never count.
- **Logical batch below micro-batch.** A `-b` smaller than `-ub` is not generated.
- **Flash attention off with a quantized V cache.** Skipped. A V cache other than f32, f16 or bf16 needs
  flash attention.
- **Too few slots for the prompt.** With more than one parallel slot and unified KV off, each slot gets
  the context divided by the slots. A combination is skipped when a slot is smaller than the benchmark
  depth plus 128 tokens.

**Count and time.** The form shows how many candidates a run will measure and roughly how long it takes,
and updates both as you edit. The count is typical, not exact. The time runs from the count times
(0.5 + depth / 20,000) minutes to the count times (0.8 + depth / 8,000) minutes, where depth is the
benchmark depth in tokens. A run of more than 4,096 candidates is refused: the form disables Start and
asks you to narrow the search, and the API returns an error with the count.

**Cache reuse** (`--cache-reuse`) can't change the measured speed, because every candidate sends
`cache_prompt: false` and the server reuses no cache during the benchmark. The API accepts it anyway.

### API

`POST /api/tune` takes `space` next to the other fields. A setting missing from it is Auto, and `null` or
no `space` means every setting is Auto. Each entry has a `mode`. Fixed and Search also take `values`, as
strings, and Off takes none:

```json
{"space": {"ub": {"mode": "list", "values": ["256", "1024"]},
           "np": {"mode": "fixed", "values": ["2"]},
           "fa": {"mode": "off"}}}
```

That searches micro-batch over 256 and 1024, holds parallel slots at 2, and holds flash attention at `on`,
the value Off holds. A setting name that doesn't exist, a value outside a setting's range, or a Fixed with
more than one value is refused with a message that names the setting.
