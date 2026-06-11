#!/usr/bin/env python3
"""
Embed chunks and store the vectors — store-agnostic (works for sqlite or hana via config.STORE).
Embeds EMBED_TEXT (breadcrumb-prefixed, DESIGN §D5) with the configured provider.
Idempotent: only embeds rows still missing a vector.

Run:  PYTHONPATH=. python3 ingest/embed.py            # uses config.STORE (default sqlite)
      PYTHONPATH=. STORE=hana python3 ingest/embed.py # against HANA when it's up
"""
import config
import providers
from retrieve.store import get_store

CHUNKS = "ingest/out/chunks.jsonl"
BATCH = 128

def main():
    store = get_store()
    loaded = store.ensure_chunks(CHUNKS)            # sqlite: loads jsonl; hana: already loaded
    pending = store.pending_embeddings()
    print(f"store={config.STORE} | chunks={loaded} | embedding {len(pending)} "
          f"with {config.EMBEDDING_MODEL} ({config.EMBEDDING_DIM}d)")
    for i in range(0, len(pending), BATCH):
        batch = pending[i:i + BATCH]
        vecs = providers.embed([t for _, t in batch])
        store.set_embeddings([(cid, v) for (cid, _), v in zip(batch, vecs, strict=True)])
        print(f"  {min(i + BATCH, len(pending))}/{len(pending)}")
    e, t = store.count_embedded()
    print(f"DONE: {e}/{t} chunks embedded (store={config.STORE})")

if __name__ == "__main__":
    main()
