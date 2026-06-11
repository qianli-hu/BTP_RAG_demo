#!/usr/bin/env python3
"""
eval/gates.py — deterministic CI quality gates (no LLM judge, no generation).

Runs retrieval over the gold set and FAILS (exit 1) if quality regressed:
  gate 1  hit-rate@5  >= HIT5_MIN          (retrieval quality)
  gate 2  every negative's top-cosine < SIM_THRESHOLD   (refusal gate separates)
  gate 3  every positive's top-cosine >= SIM_THRESHOLD  (no false refusals)

Cost: 40 query embeddings (~$0.0001). Latency: ~1 min. Suitable for CI on every PR
(given an embedded store — see .github/workflows/eval.yml for how CI builds/caches it).

Run:  PYTHONPATH=. python3 eval/gates.py
"""
import sys

import config
import providers
from eval.score_retrieval import matches
from retrieve.hybrid import Retriever

HIT5_MIN = 0.70          # baseline measured 76% — fail if it drops below 70%


def main():
    from eval.dataset import load_gold
    gold = load_gold()
    r = Retriever()
    pos = [g for g in gold if g["answer_type"] != "negative"]
    neg = [g for g in gold if g["answer_type"] == "negative"]

    hits5, neg_leak, pos_below = 0, [], []
    for it in gold:
        qvec = providers.embed([it["question"]])[0]
        dense = r.store.dense_search(qvec, config.DENSE_K, None)
        top = dense[0][1] if dense else 0.0
        if it["answer_type"] == "negative":
            if top >= config.SIM_THRESHOLD:
                neg_leak.append((it["id"], round(top, 3)))
            continue
        if top < config.SIM_THRESHOLD:
            pos_below.append((it["id"], round(top, 3)))
        sparse = r._sparse(it["question"], config.SPARSE_K, None)
        from retrieve.hybrid import dedup, rrf
        fused = dedup(rrf([dense, sparse], config.RRF_K), r.meta)[:5]
        if any(matches(r.meta[cid], it) for cid, _ in fused):
            hits5 += 1

    rate = hits5 / len(pos)
    print(f"gate 1  hit-rate@5          : {hits5}/{len(pos)} = {rate:.0%}  (min {HIT5_MIN:.0%})")
    print(f"gate 2  negatives separated : {len(neg) - len(neg_leak)}/{len(neg)}  leaks={neg_leak}")
    print(f"gate 3  positives answerable: {len(pos) - len(pos_below)}/{len(pos)}  below={pos_below}")
    print(f"fingerprint: {config.fingerprint()}")

    failed = []
    if rate < HIT5_MIN: failed.append("hit-rate@5")
    if neg_leak: failed.append("negative separation")
    if pos_below: failed.append("false refusals")
    if failed:
        print(f"\nGATES FAILED: {failed}")
        return 1
    print("\nALL GATES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
