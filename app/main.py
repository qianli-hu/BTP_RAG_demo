"""
app/main.py — the serving layer: a FastAPI wrapper around core.Engine.ask().

The engine is the SAME one the offline eval drives (eval-what-you-serve); this file adds
only HTTP plumbing — typed request/response schemas, /health, and error mapping.

Run:    PYTHONPATH=. uvicorn app.main:app --port 8000
Try:    curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
             -d '{"question": "What distance functions does the HANA vector engine support?"}'
Docs:   http://localhost:8000/docs   (interactive Swagger UI)
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import config
import prompts
from core import Engine

engine: Engine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the engine once at startup (loads chunks + BM25 index), not per request."""
    global engine
    engine = Engine()
    yield


app = FastAPI(
    title="BTP_RAG",
    description="RAG over SAP BTP documentation — grounded answers with chunk-id citations.",
    version="1.0",
    lifespan=lifespan,
)


# ---- request / response schemas (typed -> validation + clean /docs) ----
class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000,
                          examples=["What is the max dimensionality of REAL_VECTOR?"])
    filters: dict | None = Field(default=None,
                                 description="Metadata filter, e.g. {\"doc\": \"sap-hana-vector\"}")


class Citation(BaseModel):
    id: str
    source_url: str
    section_path: str


class AskResponse(BaseModel):
    answer: str
    answerable: bool
    citations: list[Citation]
    retrieved_ids: list[str]              # full top-k context ids (feed these to /judge)
    latency_ms: dict
    cost: float


class JudgeRequest(BaseModel):
    question: str
    answer: str
    retrieved_ids: list[str]


class JudgeResponse(BaseModel):
    faithfulness: float | None
    correctness: float | None
    relevance: float | None
    unsupported_claims: list[str] = []
    rationale: str | None
    judge_model: str
    judge_cost: float


@app.get("/", include_in_schema=False)
def root():
    """Friendly landing: redirect to the interactive API docs (no 404 at /)."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/docs")


@app.get("/health")
def health():
    """Liveness + config snapshot (no secrets)."""
    return {"status": "ok", "store": config.STORE,
            "chunks": len(engine.retriever.ids) if engine else 0,
            "fingerprint": config.fingerprint(prompt_version=prompts.VERSION)}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    try:
        tr = engine.ask(req.question, filters=req.filters)
    except Exception as e:                      # provider/store failure -> clean 503, not a crash
        raise HTTPException(status_code=503, detail=f"backend error: {type(e).__name__}") from e

    # resolve citation chunk-ids to source URLs so the client can render links
    cites = []
    for cid in tr["citations"]:
        m = engine.retriever.meta.get(cid)
        if m:
            cites.append(Citation(id=cid, source_url=m["source_url"], section_path=m["section_path"]))
    return AskResponse(answer=tr["answer"], answerable=tr["answerable"], citations=cites,
                       retrieved_ids=[r["id"] for r in tr["retrieved"]],
                       latency_ms=tr["latency_ms"], cost=tr["cost"])


@app.post("/judge", response_model=JudgeResponse)
def judge(req: JudgeRequest) -> JudgeResponse:
    """LLM-as-judge on a live answer (same judge as the offline eval, gold-free prompt
    variant): faithfulness strictly vs the retrieved context = on-demand hallucination check."""
    import prompts as P
    import providers
    chunks = [engine.retriever.meta[i] for i in req.retrieved_ids if i in engine.retriever.meta]
    if not chunks:
        raise HTTPException(status_code=422, detail="no valid retrieved_ids (judge needs the context)")
    try:
        text, usage = providers.chat(P.live_judge_messages(req.question, chunks, req.answer),
                                     model=config.JUDGE_MODEL)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"judge backend error: {type(e).__name__}") from e
    d = P.parse_json(text) or {}
    return JudgeResponse(faithfulness=d.get("faithfulness"), correctness=d.get("correctness"),
                         relevance=d.get("relevance"),
                         unsupported_claims=d.get("unsupported_claims") or [],
                         rationale=d.get("rationale"),
                         judge_model=config.JUDGE_MODEL, judge_cost=usage["cost"])
