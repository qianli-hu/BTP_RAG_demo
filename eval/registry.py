"""
eval/registry.py — append-only run ledger (the reproducibility manifest).

Every eval run appends one line to eval/registry.jsonl:
  {ts, kind, fingerprint, metrics}
The fingerprint already carries the full "model" identity (ROADMAP §2): answer/judge/embed
models, retrieval params, prompt_version, gold_hash, corpus_hash — so any metric in the
ledger is attributable to an exact configuration and corpus/gold snapshot.
The ledger is COMMITTED (metrics only, no © text): the repo's experiment history.
"""
import datetime
import json
from pathlib import Path

import config
import prompts

LEDGER = Path(__file__).resolve().parent / "registry.jsonl"


def record(kind, metrics):
    row = {"ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
           "kind": kind,                       # rag-eval | norag-baseline | gates | ab-test...
           "fingerprint": config.fingerprint(prompt_version=prompts.VERSION),
           "metrics": metrics}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
