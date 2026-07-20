"""
eval/dataset.py — the gold/adversarial dataset module.

Gold items live under eval/gold/, ONE FILE PER ANSWER TYPE:
  factual.jsonl, table.jsonl, cross-doc.jsonl,            <- positives
  distractor.jsonl, false-premise.jsonl, entity-confusion.jsonl,  <- answerable traps
  negative.jsonl                                          <- out-of-corpus (exact refusal)

load_gold() concatenates them (sorted by id) so consumers see one dataset; reporting
stays per-type + aggregate. gold_hash() is CONTENT-addressed (canonical JSON of the
items, not file bytes) — stable across file reorganization, changes when any item does.

Every item carries "split": dev|test (BUILD_PLAN §2.9) — dev tunes (effort sweeps,
thresholds, RRF params), the locked test split is for FINAL comparisons only.
Assignment rule (deterministic, fixed at authoring time): stratified by answer_type;
within each type, items sorted by id alternate dev, test, dev, ... (22 dev / 18 test).
New items get a split at authoring time under the same alternation.
"""
import hashlib
import json
from pathlib import Path

GOLD_DIR = Path(__file__).resolve().parent / "gold"


def load_gold(types=None, split=None):
    items = []
    for f in sorted(GOLD_DIR.glob("*.jsonl")):
        items += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    items.sort(key=lambda g: g["id"])
    if types:
        items = [g for g in items if g["answer_type"] in types]
    if split:
        items = [g for g in items if g.get("split") == split]
    return items


def gold_hash():
    blob = "\n".join(json.dumps(g, sort_keys=True, ensure_ascii=False) for g in load_gold())
    return hashlib.sha1(blob.encode()).hexdigest()[:10]
