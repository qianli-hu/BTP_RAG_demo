#!/usr/bin/env python3
"""
eval/measurements/effort_repeat_probe.py — the committed version of the 2026-07-18
repeat-probe that falsified the single-run "TTFT 1.36s / 72% wait eliminated" claim
(BUILD_PLAN F2). Runs each probe question N times per reasoning-effort arm through the
CANONICAL path (Engine.ask_stream) and writes raw per-run rows, so refusal-rate and
latency claims are distributions, never single shots.

Runs are SEQUENTIAL on purpose: parallel requests would contend and distort TTFT.

Usage:
  PYTHONPATH=. python3 eval/measurements/effort_repeat_probe.py [efforts] [repeats]
  # defaults: efforts=minimal,medium  repeats=3
Output: eval/measurements/<UTCdate>-effort-refusal-probe.jsonl (append; committed)
"""
import datetime
import json
import statistics
import sys
from pathlib import Path

import config
from core import Engine

# One synthesis question (evidence scattered across chunks — the type that exposed the
# refusal failure) and one fact-lookup control (answer verbatim in a single chunk).
QUESTIONS = [
    ("synthesis", "What are the steps to deploy a model for inference in SAP AI Core?"),
    ("lookup", "What is the maximum dimensionality of the REAL_VECTOR data type?"),
]

def probe(eng, effort, repeats, out):
    config.ANSWER_REASONING_EFFORT = effort          # arm override (call sites read at call time)
    rows = []
    for qkind, q in QUESTIONS:
        for run in range(1, repeats + 1):
            trace = eng.ask(q, log=False)            # buffered wrapper over ask_stream
            u = trace["tokens"] or {}
            row = {"ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                   "question_kind": qkind, "effort": effort, "run": run,
                   "answerable": trace["answerable"], "gated": trace["gated"],
                   "ttft_ms": trace["latency_ms"]["ttft"], "total_ms": trace["latency_ms"]["total"],
                   "completion_tokens": u.get("completion"), "cost": trace["cost"],
                   "n_citations": len(trace["citations"]),
                   "answer_prefix": trace["answer"][:80]}
            rows.append(row)
            out.write(json.dumps(row, ensure_ascii=False) + "\n"); out.flush()
            print(f"  {qkind:9s} {effort:8s} run{run}: answerable={row['answerable']!s:5s} "
                  f"gated={row['gated']} ttft={row['ttft_ms']}ms total={row['total_ms']}ms")
    return rows

def main():
    efforts = (sys.argv[1] if len(sys.argv) > 1 else "minimal,medium").split(",")
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    day = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    path = Path(__file__).resolve().parent / f"{day}-effort-refusal-probe.jsonl"
    eng = Engine()
    all_rows = []
    with path.open("a", encoding="utf-8") as out:
        for effort in efforts:
            all_rows += probe(eng, effort, repeats, out)

    print(f"\n=== summary (n={repeats}/question/arm) -> {path.name} ===")
    for effort in efforts:
        for qkind, _ in QUESTIONS:
            g = [r for r in all_rows if r["effort"] == effort and r["question_kind"] == qkind]
            ans = sum(1 for r in g if r["answerable"])
            print(f"{qkind:9s} {effort:8s}: answered {ans}/{len(g)}  "
                  f"ttft median={int(statistics.median(r['ttft_ms'] for r in g))}ms  "
                  f"total median={int(statistics.median(r['total_ms'] for r in g))}ms")
    print(f"total cost ${sum(r['cost'] for r in all_rows):.4f}")

if __name__ == "__main__":
    main()
