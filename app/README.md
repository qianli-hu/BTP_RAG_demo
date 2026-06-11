# app/ — TODO

FastAPI query API:

```text
POST /ask
```

Request: question. Response: answer, citations, retrieved chunk ids, and `answerable`.

v1 runs locally:

```text
local FastAPI -> HANA Cloud -> OpenAI provider
```

Streamlit is optional as a thin client. v2 deploys the same FastAPI app to Cloud Foundry
(`cf push`, bind/inject HANA + model-provider credentials).
