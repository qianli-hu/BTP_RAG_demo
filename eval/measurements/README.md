# eval/measurements — committed raw measurement artifacts

Latency/refusal claims in this repo must be **distributions, not single runs**
(BUILD_PLAN F2 + §2.9 run-count policy). This directory holds the committed probe
scripts and their raw per-run outputs, so every published number has a reproducible
artifact behind it.

## Why this exists (the 2026-07-18 finding, BUILD_PLAN F2)

Streaming (1.1) shipped with a single-run headline: "TTFT 1.36 s vs 4.85 s total, 72%
of perceived wait eliminated." Repeating the measurement falsified it: at
`reasoning_effort=minimal` the synthesis probe question was **refused ~5/6 runs**
(layer-2 refusal is a reasoning-dependent judgment — deciding that evidence scattered
across chunks assembles into an answer needs thinking; minimal effort skips it), while
at `medium` it answered 3/3 with TTFT ≈ 12 s (thinking-bound — streaming removes only
write-wait). Fact-lookup questions were unaffected (answered 3/3, TTFT ≈ 1.2 s).
Those original probes were ad-hoc (session-only, un-logged); the scripts here are the
committed, re-runnable versions.

## Contents

- `effort_repeat_probe.py` — N sequential repeats per reasoning-effort arm of one
  synthesis + one lookup question through the canonical path (`Engine.ask_stream`).
  Sequential on purpose: parallel requests contend and distort TTFT.
- `<date>-effort-refusal-probe.jsonl` — raw per-run rows (refusal, gate, TTFT, total,
  tokens, cost, 80-char answer prefix).
- `sweep_effort.py` — the M1.1 operating-point sweep: full dev split × effort arms,
  3 repeats on refusal-prone types, judge on run 1; writes raw rows here + a registry
  row per arm. Latency published from the sequential probe, not from this (it runs
  concurrently for wall-clock).
- `<date>-effort-sweep.jsonl` — raw sweep rows.
