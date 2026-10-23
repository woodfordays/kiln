# Prometheus and Grafana

Kiln serves Prometheus metrics at `GET /metrics` on the control port (`http://<host>:8090/metrics`). One
scrape covers everything: Kiln's request counters, the GPU, the models that are running, and every
llama-server's own metrics, labelled with the model they came from. A ready-made Grafana dashboard is in
[grafana/kiln.json](grafana/kiln.json).

The Overview page and `/metrics` measure the same requests. Use Overview to look at one box. Use Prometheus
when you already run it, want alerts, or want Kiln next to your other machines.

## Scraping

Without a UI password:

```yaml
scrape_configs:
  - job_name: kiln
    static_configs:
      - targets: ["kiln-host:8090"]
```

`/metrics` sits behind the same login as the rest of the control port. With `KILN_UI_PASSWORD` set, give
Prometheus the API token (`KILN_API_TOKEN`) or the password:

```yaml
scrape_configs:
  - job_name: kiln
    authorization:
      credentials: "<KILN_API_TOKEN>"
    static_configs:
      - targets: ["kiln-host:8090"]
```

(`basic_auth: {username: kiln, password: "<KILN_UI_PASSWORD>"}` works too.) A scrape interval of 15 to 30
seconds is plenty. Each scrape asks every running llama-server for its metrics, with a 2-second limit per
model.

## The dashboard

In Grafana, **Dashboards → New → Import**, upload `docs/grafana/kiln.json`, and pick your Prometheus data
source. It has three sections:

- **Requests**: decode and prefill speed (token-weighted, overall and per model), time to first token
  (p50, p95), requests per minute by result, tokens per second, request time and time spent waiting for a
  model to load, draft acceptance, and each model's queue.
- **Models**: the running models with their state, GPU memory, context, requests in flight and idle time.
- **GPU**: energy and tokens per Wh over the time range, power, load, VRAM, temperature and clock.

The *Kiln* and *Model* selectors at the top filter by scrape target and model. The dashboard has been
checked with Grafana 12.1 and Prometheus 3.5.

## Metrics

Kiln's own metrics all start with `kiln_`. Counters start at zero when Kiln starts, as Prometheus expects;
`rate()` and `increase()` handle the restart. They count only requests made since then, so they don't change
when request history is deleted (*Keep request history*, *Reset statistics*).

### Requests

Every request through the API port that Kiln records on the Requests page: completions, chat, embeddings,
reranking and decisions. Labelled with `model`, the model file's name, as on the Overview page.

| Metric | Type | Meaning |
|---|---|---|
| `kiln_requests_total` | counter | Requests, with `result`: `ok`, `error`, or `aborted` (the client hung up). |
| `kiln_prompt_tokens_total` | counter | New prompt tokens, not counting tokens reused from the prompt cache. |
| `kiln_prompt_cached_tokens_total` | counter | Prompt tokens reused from the cache. |
| `kiln_generated_tokens_total` | counter | Tokens generated. |
| `kiln_prefill_timed_tokens_total`, `kiln_prefill_seconds_total` | counter | Prompt tokens and prompt time of requests llama-server timed. Divide the rates for prefill speed. |
| `kiln_decode_timed_tokens_total`, `kiln_decode_seconds_total` | counter | The same for generation. Divide the rates for decode speed. |
| `kiln_draft_tokens_total`, `kiln_draft_accepted_tokens_total` | counter | Speculative decoding: tokens drafted and accepted. |
| `kiln_ttft_seconds` | histogram | Time to first token of successful requests. Time spent waiting for a model to load isn't included. |
| `kiln_request_duration_seconds` | histogram | Wall time of each request. |
| `kiln_model_wait_seconds` | histogram | How long a request waited for its model to be loaded or swapped in, for requests that had to wait. |

### Running models

Labelled with `model` (the file), `name` (what clients call it: the preset's alias or name) and `role`.

| Metric | Type | Meaning |
|---|---|---|
| `kiln_model_state` | gauge | `1`, with `state`: `starting`, `loading`, `ready`, `stopping` or `crashed`. |
| `kiln_model_gpu_memory_bytes` | gauge | GPU memory the model uses, measured when it loaded (otherwise estimated). Absent for CPU-only models. |
| `kiln_model_context_size` | gauge | Context size llama-server reports. |
| `kiln_model_inflight_requests` | gauge | Requests the model is answering now. |
| `kiln_model_idle_seconds` | gauge | Seconds since its last request (what *Unload idle models after* compares with). |

### GPU

Absent when Kiln has no GPU readings. With several GPUs, memory and power are summed, load is averaged and
temperature is the hottest card, as on the Overview page. The readings cover the whole GPU, not just Kiln's
models.

| Metric | Type | Meaning |
|---|---|---|
| `kiln_gpu_info` | gauge | `1`, with `name` and `source` (`amd` or `nvidia`). |
| `kiln_gpu_busy_ratio` | gauge | Load, 0 to 1. |
| `kiln_gpu_vram_used_bytes`, `kiln_gpu_vram_total_bytes` | gauge | GPU memory. |
| `kiln_gpu_power_watts` | gauge | Power draw. |
| `kiln_gpu_temperature_celsius` | gauge | Temperature. |
| `kiln_gpu_clock_hertz` | gauge | Core clock. |
| `kiln_gpu_energy_joules_total` | counter | Energy since Kiln started, integrated from the power readings every 2 seconds. |

### Kiln

| Metric | Type | Meaning |
|---|---|---|
| `kiln_info` | gauge | `1`, with `llama_version` and `backend`. |
| `kiln_start_time_seconds` | gauge | When Kiln started (Unix time). |

### llama-server's own metrics

Every ready model's llama-server metrics (Kiln always starts it with `--metrics`) are included as they are,
with `model` and `name` labels added: `llamacpp:prompt_tokens_total`, `llamacpp:tokens_predicted_total`,
`llamacpp:requests_processing`, `llamacpp:requests_deferred`, `llamacpp:n_busy_slots_per_decode` and the rest,
depending on the llama.cpp build. They keep llama.cpp's names, so dashboards built for llama-server work
with a `model` filter. They also count requests that bypass Kiln, and they reset when a model restarts.
`promtool check metrics` warns about the colon in these names. That is how llama.cpp names them, and the
warning is harmless.

## Queries

```promql
# Decode speed per model, tokens/s
sum by (model) (rate(kiln_decode_timed_tokens_total[5m])) / sum by (model) (rate(kiln_decode_seconds_total[5m]))

# 95th percentile time to first token
histogram_quantile(0.95, sum by (le) (rate(kiln_ttft_seconds_bucket[5m])))

# GPU energy per day, kWh
increase(kiln_gpu_energy_joules_total[1d]) / 3.6e6

# Generated tokens per watt-hour
sum(increase(kiln_generated_tokens_total[1h])) / (increase(kiln_gpu_energy_joules_total[1h]) / 3600)
```

An alert for a crashed model:

```yaml
- alert: KilnModelCrashed
  expr: kiln_model_state{state="crashed"} == 1
  for: 1m
  annotations:
    summary: "{{ $labels.name }} crashed on {{ $labels.instance }}"
```
