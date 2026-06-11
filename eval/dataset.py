"""
eval/dataset.py — the gold/adversarial dataset module.

Gold items live under eval/gold/, ONE FILE PER ANSWER TYPE:
  factual.jsonl, table.jsonl, cross-doc.jsonl,            <- positives
  distractor.jsonl, false-premise.jsonl, entity-confusion.jsonl,  <- answerable traps
  negative.jsonl                                          <- out-of-corpus (exact refusal)

load_gold() concatenates them (sorted by id) so consumers see one dataset; reporting
stays per-type + aggregate. gold_hash() is CONTENT-addressed (canonical JSON of the
items, not file bytes) — stable across file reorganization, changes when any item does.
"""
import hashlib
import json
from pathlib import Path

GOLD_DIR = Path(__file__).resolve().parent / "gold"


def load_gold(types=None):
    items = []
    for f in sorted(GOLD_DIR.glob("*.jsonl")):
        items += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    items.sort(key=lambda g: g["id"])
    if types:
        items = [g for g in items if g["answer_type"] in types]
    return items


def gold_hash():
    blob = "\n".join(json.dumps(g, sort_keys=True, ensure_ascii=False) for g in load_gold())
    return hashlib.sha1(blob.encode()).hexdigest()[:10]
