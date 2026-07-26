#!/usr/bin/env python3
"""
Rule-based eval for BTP_RAG (V2_DESIGN §1A / §2) — no LLM, deterministic, CI-safe.

Two modes:
  python3 eval/score_retrieval.py                 # validate the gold set (no DB needed)
  python3 eval/score_retrieval.py results.jsonl   # + score the 3 rule gates

Gold validation (mode 1) checks, per item:
  - schema (required fields; negatives must expect exactly "Not in knowledge base."
    with gold_doc=null);
  - ANCHOR — a chunk exists with the item's gold_doc + gold_section_path (or, for the
    HTML doc, its per-topic gold_source_url);
  - FACT — if the item lists `expect_terms`, those terms actually appear in an anchored
    chunk's text (so the answer is present in the corpus, not just the section).
  Items without `expect_terms` are anchor-validated only (conceptual / table-only).

Results schema (mode 2) — one JSON row per gold id:
  {"id": "q01", "answer": "...", "citations": ["<chunk_id>", ...],
   "retrieved": [{"id","doc","section_path","source_url", ...}, ...]}
Gates scored: hit-rate@k (positives), exact-negative refusal (negatives),
citation validity. Citations are CHUNK IDS (not URLs — many PDF chunks share one
doc-level URL, so a URL check would pass a wrong section/page); each cited id must
exist in the corpus AND be in that query's retrieved set.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHUNKS = ROOT / "ingest" / "out" / "chunks.jsonl"
NOT_IN_KB = "Not in knowledge base."
DOCS = {"sap-ai-core", "sap-hana-vector", "sap-ai-launchpad"}
ADVERSARIAL = {"negative", "distractor", "false-premise", "entity-confusion"}

def load(p): return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]

def matches(chunk, item):
    """anchor match: same doc + section-path substring (or per-topic url for the HTML doc)."""
    if chunk.get("doc") != item["gold_doc"]:
        return False
    sp = (item.get("gold_section_path") or "").lower()
    if sp and sp in (chunk.get("section_path") or "").lower():
        return True
    su = item.get("gold_source_url")
    return bool(su and chunk.get("source_url") == su and item["gold_doc"] == "sap-hana-vector")

# ---- evidence atoms (BUILD_PLAN §2.6): chunking-independent evidence labels ----------
# An atom = {doc, quote}: a verbatim snippet from the SOURCE document. A chunk "covers"
# an atom iff the normalized quote appears in the chunk's normalized raw text — so
# labels survive any re-chunking (quotes live in the docs, not in our chunk ids).
def norm(s):
    """whitespace/case-insensitive containment normalization."""
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def atom_covered(atom, chunk):
    return chunk.get("doc") == atom["doc"] and norm(atom["quote"]) in norm(chunk.get("text"))

def atoms_of(item):
    """Explicit evidence_atoms, else derived from expect_terms (each term is already a
    verified verbatim snippet — the FACT check below guarantees it exists in the doc)."""
    if item.get("evidence_atoms"):
        return item["evidence_atoms"]
    return [{"doc": item["gold_doc"], "quote": t} for t in (item.get("expect_terms") or [])]

def evidence_recall_at_k(item, retrieved, k):
    """(covered, total) atoms of this item within the top-k retrieved chunks.
    Returns None for items with no atoms (anchor-only / negatives)."""
    atoms = atoms_of(item)
    if not atoms:
        return None
    top = retrieved[:k]
    covered = sum(1 for a in atoms if any(atom_covered(a, c) for c in top))
    return covered, len(atoms)

# ---- mode 1: gold validation -------------------------------------------------
def validate(gold, chunks):
    by_type, errs, anchor_fail, fact_fail = {}, [], [], []
    for it in gold:
        t = it.get("answer_type", "?"); by_type[t] = by_type.get(t, 0) + 1
        for f in ("id", "question", "answer_type", "expected"):
            if not it.get(f): errs.append(f"{it.get('id','?')}: missing {f}")
        if t == "negative":
            if it["expected"] != NOT_IN_KB: errs.append(f"{it['id']}: negative expected != '{NOT_IN_KB}'")
            if it.get("gold_doc") is not None: errs.append(f"{it['id']}: negative gold_doc must be null")
            continue
        if it.get("gold_doc") not in DOCS:
            errs.append(f"{it['id']}: gold_doc '{it.get('gold_doc')}' not a corpus doc"); continue
        anchored = [c for c in chunks if matches(c, it)]
        if not anchored:
            anchor_fail.append(f"{it['id']}: no chunk at {it['gold_doc']} / {it.get('gold_section_path')}")
            continue
        terms = it.get("expect_terms") or []
        blob = " ".join(c["text"] for c in anchored).lower()
        missing = [t for t in terms if t.lower() not in blob]
        if missing:
            fact_fail.append(f"{it['id']}: terms not in anchored chunks: {missing}")
        # ATOM check: every explicit evidence atom's quote must exist VERBATIM (normalized)
        # in >=1 chunk of its doc — else the label is broken, not the retrieval.
        for a in it.get("evidence_atoms") or []:
            if not any(atom_covered(a, c) for c in chunks):
                fact_fail.append(f"{it['id']}: atom quote not found in {a['doc']}: "
                                 f"{a['quote'][:60]!r}")
    return by_type, errs, anchor_fail, fact_fail

# ---- mode 2: rule gates (need a results file) -------------------------------
def hit_rate_at_k(gold, res, k):
    pos = [it for it in gold if it["answer_type"] != "negative"]
    hits = sum(1 for it in pos
               if any(matches(c, it) for c in res.get(it["id"], {}).get("retrieved", [])[:k]))
    return hits, len(pos)

def negative_gate(gold, res):
    negs = [it for it in gold if it["answer_type"] == "negative"]
    scored = [it for it in negs if res.get(it["id"], {}).get("answer") is not None]  # skip un-generated
    ok = sum(1 for it in scored if res[it["id"]]["answer"].strip() == NOT_IN_KB)
    return ok, len(scored)

def citation_gate(gold, res, corpus_ids):
    """Every cited CHUNK ID must exist in the corpus AND be in that query's retrieved set."""
    scored = ok = 0
    for it in gold:
        r = res.get(it["id"])
        if not r or it["answer_type"] == "negative":
            continue
        cites = r.get("citations", [])
        if not cites:
            continue
        scored += 1
        rset = {c.get("id") for c in r.get("retrieved", [])}
        if all(cid in corpus_ids and cid in rset for cid in cites):
            ok += 1
    return ok, scored

# ---- main -------------------------------------------------------------------
def main():
    from eval.dataset import load_gold
    gold, chunks = load_gold(), load(CHUNKS)
    by_type, errs, anchor_fail, fact_fail = validate(gold, chunks)

    adv = sum(by_type.get(t, 0) for t in ADVERSARIAL)
    print(f"gold items: {len(gold)}  | adversarial: {adv}/{len(gold)} ({100*adv//len(gold)}%)")
    for t in sorted(by_type):
        print(f"  {t:16s} {by_type[t]:2d}{'  (adversarial)' if t in ADVERSARIAL else ''}")
    n_fact = sum(1 for it in gold if it.get("expect_terms"))
    print(f"\nschema errors: {len(errs)}"); [print("  ✗", e) for e in errs]
    print(f"anchor failures: {len(anchor_fail)}"); [print("  ✗", e) for e in anchor_fail]
    print(f"fact failures (expect_terms not in anchored chunks): {len(fact_fail)}")
    [print("  ✗", e) for e in fact_fail]
    ok = not (errs or anchor_fail or fact_fail)
    print(f"\nGOLD SET: {'✓ valid' if ok else '✗ FIX ABOVE'} "
          f"— positives anchored; {n_fact} fact-verified, "
          f"{len(gold)-n_fact-by_type.get('negative',0)} anchor-only, "
          f"{by_type.get('negative',0)} negatives")

    if len(sys.argv) > 1:
        res = {r["id"]: r for r in load(sys.argv[1])}
        corpus_ids = {c["id"] for c in chunks}
        by_id = {c["id"]: c for c in chunks}
        print("\n--- rule gates ---")
        for k in (1, 3, 5):
            h, n = hit_rate_at_k(gold, res, k); print(f"hit-rate@{k}: {h}/{n} = {h/n:.0%}" if n else "")
        no, nn = negative_gate(gold, res); print(f"negative refusal (exact): {no}/{nn}")
        co, cs = citation_gate(gold, res, corpus_ids); print(f"citation validity (chunk-id): {co}/{cs}")
        # evidence coverage (atoms need chunk TEXT -> resolve retrieved ids to corpus)
        er, comp = [], []
        for it in gold:
            r = res.get(it["id"])
            if not r or it["answer_type"] == "negative":
                continue
            hits = [by_id[c["id"]] for c in r.get("retrieved", []) if c.get("id") in by_id]
            cov = evidence_recall_at_k(it, hits, 5)
            if cov:
                er.append(cov[0] / cov[1]); comp.append(cov[0] == cov[1])
        if er:
            print(f"evidence-recall@5 (macro): {sum(er)/len(er):.3f} | "
                  f"complete@5: {sum(comp)}/{len(comp)}  (n={len(er)} items w/ atoms)")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
