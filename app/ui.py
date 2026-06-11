"""
app/ui.py — Streamlit demo client for the BTP_RAG service.

Thin client by design: it talks ONLY to the FastAPI service (the same /ask endpoint any
integration would use) — demoing the system as a service, not a notebook.

Run (two terminals):
  1. PYTHONPATH=. python3 -m uvicorn app.main:app --port 8000      # the service
  2. streamlit run app/ui.py                                       # the UI -> http://localhost:8501
"""
import requests
import streamlit as st

API = "http://localhost:8000"

st.set_page_config(page_title="BTP_RAG", page_icon="📚", layout="centered")
st.title("📚 BTP_RAG — SAP BTP documentation assistant")
st.caption("Grounded answers with citations over SAP AI Core · HANA Cloud Vector · AI Launchpad docs. "
           "Out-of-scope questions are refused, not guessed.")

# ---- sidebar: live service health + the experiment fingerprint ----
with st.sidebar:
    st.subheader("Service")
    try:
        h = requests.get(f"{API}/health", timeout=5).json()
        st.success(f"online · store={h['store']} · {h['chunks']} chunks")
        with st.expander("experiment fingerprint"):
            st.json(h["fingerprint"])
    except Exception:
        st.error(f"API not reachable at {API}\n\nStart it:\n"
                 "`PYTHONPATH=. python3 -m uvicorn app.main:app --port 8000`")
    judge_on = st.toggle("🧑‍⚖️ Judge every answer (gpt-5)", value=True,
                         help="Runs the same LLM-as-judge as the offline eval on this live answer: "
                              "faithfulness (grounding vs retrieved context), correctness, relevance. "
                              "Adds ~5–10 s and ~$0.01 per question.")
    st.subheader("Try these")
    st.markdown("""
- *What is the max dimensionality of REAL_VECTOR?*
- *What unit meters generative AI usage in SAP AI Core?*
- *Since REAL_VECTOR is double precision, how many bytes per element?* — false premise
- *How do I create a story in SAP Analytics Cloud?* — out of scope → refusal
""")

# ---- main: ask ----
q = st.text_input("Ask about SAP BTP", placeholder="How do I create a vector index in HANA Cloud?")
if st.button("Ask", type="primary") and q.strip():
    with st.spinner("retrieving + generating…"):
        try:
            r = requests.post(f"{API}/ask", json={"question": q}, timeout=120)
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            st.error(f"request failed: {e}")
            st.stop()

    if d["answerable"]:
        st.markdown(f"### {d['answer']}")
    else:
        st.warning(f"🚫 {d['answer']}  \n*(out of corpus scope — refused rather than guessed)*")

    if d["citations"]:
        st.markdown("**Sources**")
        for c in d["citations"]:
            st.markdown(f"- [{c['section_path']}]({c['source_url']}) · `{c['id']}`")

    m1, m2, m3 = st.columns(3)
    m1.metric("retrieve", f"{d['latency_ms']['retrieve']} ms")
    m2.metric("generate", f"{d['latency_ms']['generate']} ms")
    m3.metric("cost", f"${d['cost']:.4f}")

    # ---- live LLM-as-judge: the answer audits itself (same judge as offline eval) ----
    if judge_on and d["answerable"] and d.get("retrieved_ids"):
        with st.spinner("gpt-5 judging the answer against its retrieved context…"):
            try:
                jr = requests.post(f"{API}/judge", timeout=120,
                                   json={"question": q, "answer": d["answer"],
                                         "retrieved_ids": d["retrieved_ids"]})
                jr.raise_for_status()
                j = jr.json()
            except Exception as e:
                st.warning(f"judge unavailable: {e}")
                j = None
        if j:
            st.markdown(f"**🧑‍⚖️ LLM-as-judge** (`{j['judge_model']}`, ${j['judge_cost']:.4f})")
            j1, j2, j3 = st.columns(3)
            j1.metric("faithfulness", j["faithfulness"],
                      help="claims grounded in retrieved context (anti-hallucination)")
            j2.metric("correctness", j["correctness"],
                      help="factually right (no gold live — vs context + general knowledge)")
            j3.metric("relevance", j["relevance"], help="actually answers the question asked")
            if j["unsupported_claims"]:
                st.error("⚠️ unsupported claims: " + " · ".join(j["unsupported_claims"]))
            if j.get("rationale"):
                st.caption(f"judge rationale: {j['rationale']}")
