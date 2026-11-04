# Paraphrase-Recall Probe (v2)

Measures whether the optional local semantic channel (FastEmbed,
`BAAI/bge-small-en-v1.5`) improves retrieval of paraphrased questions,
and at what cost. Read this before quoting any number from it.

## Design

- **Corpus:** 22 single-sentence units in one file, including near-miss
  distractors on purpose (three different budgets, two venues, two
  November deadlines). Three structured memory records are seeded so
  the production channel mix is real.
- **Cases:** 8 paraphrases, 3 keyword controls, 4 hard negatives
  (gold unit must outrank a named forbidden near-miss).
- **Arms:** `keyword` · `production` (keyword+memory, the current
  default) · `production_semantic` (keyword+memory+semantic) ·
  `keyword_semantic` (ablation).
- **Metrics:** Recall@1, Recall@5, MRR, hard-negative pass rate, mean
  milliseconds per question. The semantic floor is the configured
  `YAAD_SEMANTIC_MIN_SIMILARITY` (default 0.30).

## Honest limitations

- The paraphrase questions were authored to share almost no words with
  their gold units, so the keyword arm fails there **by construction**.
  This probe measures whether embeddings close a constructed lexical
  gap; it says nothing about real-world query distributions.
- n=11 recall cases. No confidence intervals; a single case swings
  Recall@1 by ~0.09.
- One tiny single-file domain, written by the same author who built
  the channel. This is fixture evidence, not a benchmark result, and
  the probe makes **no ship decision**: the channel stays opt-in and
  disabled by default regardless of the outcome printed here.

## Run

```sh
pip install -e ".[semantic]"
FASTEMBED_CACHE_PATH=/tmp/fastembed-cache .venv/bin/python eval/paraphrase_recall/run_probe.py
```

Weights are the quantized ONNX build (~64 MB as cached). Without the
`semantic` extra or the weights, the probe reports the blocker and
exits 0.

## Real-weight run (2026-11-03)

First run against actual `BAAI/bge-small-en-v1.5` ONNX weights (prior
runs used stub vectors). Weights download free from HuggingFace on
first use (~100 MB); the sandbox default cache is unwritable, so run
with `FASTEMBED_CACHE_PATH=/tmp/fastembed-cache`:

```
FASTEMBED_CACHE_PATH=/tmp/fastembed-cache .venv/bin/python \
  eval/paraphrase_recall/run_probe.py > eval/paraphrase_recall/results_real_weights.json
```

Results (`results_real_weights.json`): paraphrase R@1 0.125 → 0.875,
R@5 1.0; keyword-control steady at 1.0/1.0 (no degradation);
hard negatives 0.0 → 0.5; mean latency 0.7ms → 54.8ms cold,
~3-4ms warm (persistent `unit_embeddings` cache: 22/22 hits).

Decision: the semantic channel stays **opt-in, default off**. The
recall win is real but measured on n=11 authored fixtures, and
default-on would break installs without the `semantic` extra
(`RuntimeError` in the serving path). Enable per deployment with
`YAAD_SEMANTIC_RETRIEVAL=true`; `route_channels` then fuses semantic
for complex and paraphrase-prone questions only.
