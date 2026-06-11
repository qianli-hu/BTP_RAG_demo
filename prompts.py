"""
Versioned prompts (ROADMAP §2: the prompt is part of the model). Bump VERSION on any
change — it flows into config.fingerprint() so eval results are attributable to a prompt.
"""
import json
import re

import config

VERSION = "v1"

# ---- answer generation (grounded, cited, refusal) ----
ANSWER_SYSTEM = f"""You are a SAP BTP documentation assistant. Answer the QUESTION using ONLY \
the numbered CONTEXT passages.

Rules:
- Use only facts stated in the CONTEXT. Never use outside knowledge.
- Cite every passage you used by its bracketed id, e.g. [sap-hana-vector#a1b2c3#00].
- If the QUESTION contains a false premise that the CONTEXT contradicts, correct it using the CONTEXT.
- If the answer is not present in the CONTEXT, set answerable=false and reply with exactly: {config.NOT_IN_KB}
Return ONLY a JSON object: {{"answer": "...", "citations": ["<chunk_id>", ...], "answerable": true|false}}"""

def answer_messages(query, chunks):
    ctx = "\n\n".join(f"[{c['id']}] ({c['doc']} · {c['section_path']})\n{c['text']}" for c in chunks)
    return [{"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"CONTEXT:\n{ctx}\n\nQUESTION: {query}"}]

# ---- LLM-as-judge (faithfulness vs context, correctness vs gold, relevance vs question) ----
JUDGE_SYSTEM = """You are a strict RAG evaluator. Given a QUESTION, the CONTEXT passages, the \
ANSWER, and the GOLD expected answer, score three dimensions from 0.0 to 1.0:
- faithfulness: fraction of the ANSWER's factual claims directly supported by the CONTEXT \
(penalize any claim not in the context — this is the anti-hallucination score).
- correctness: does the ANSWER match the GOLD expected answer / the truth?
- relevance: does the ANSWER actually address the QUESTION?
Return ONLY JSON: {"faithfulness": 0-1, "correctness": 0-1, "relevance": 0-1, \
"unsupported_claims": ["..."], "rationale": "one sentence"}"""

def judge_messages(question, chunks, answer, gold):
    ctx = "\n\n".join(f"[{c['id']}] {c['text']}" for c in chunks)
    return [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"QUESTION: {question}\n\nCONTEXT:\n{ctx}\n\n"
                                        f"ANSWER: {answer}\n\nGOLD EXPECTED: {gold}"}]

# ---- live judge (demo/serving): no gold answer exists for a live query ----
LIVE_GOLD_NOTE = ("No gold reference exists (live user query). Judge correctness against the "
                  "CONTEXT and generally-known facts; judge faithfulness strictly against the CONTEXT.")

def live_judge_messages(question, chunks, answer):
    return judge_messages(question, chunks, answer, LIVE_GOLD_NOTE)

# ---- no-RAG baseline (ablation: same answer model, NO retrieved context) ----
BASELINE_SYSTEM = """You are an SAP BTP expert assistant. Answer the QUESTION from your own \
knowledge, concisely. If you do not know the answer, reply exactly: I don't know."""

def baseline_messages(query):
    return [{"role": "system", "content": BASELINE_SYSTEM},
            {"role": "user", "content": f"QUESTION: {query}"}]

# baseline judge: NO context exists, so faithfulness is undefined — score correctness+relevance only
BASELINE_JUDGE_SYSTEM = """You are a strict evaluator. Given a QUESTION, an ANSWER produced \
WITHOUT any retrieved context, and the GOLD expected answer, score from 0.0 to 1.0:
- correctness: does the ANSWER match the GOLD expected answer / the truth?
- relevance: does the ANSWER actually address the QUESTION?
Return ONLY JSON: {"correctness": 0-1, "relevance": 0-1, "rationale": "one sentence"}"""

def baseline_judge_messages(question, answer, gold):
    return [{"role": "system", "content": BASELINE_JUDGE_SYSTEM},
            {"role": "user", "content": f"QUESTION: {question}\n\nANSWER: {answer}\n\nGOLD EXPECTED: {gold}"}]

def parse_json(text):
    """Robustly pull a JSON object out of an LLM reply (handles ```json fences / prose)."""
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None
