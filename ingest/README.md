# ingest/ — TODO

Full ingestion pipeline (see HANDOFF §5, §10):
parse (fitz prose + Docling tables) → chunk → tag section_path/parent_id from embedded TOC → metadata → embed (Gen AI Hub) → load into HANA.

Reuse the validated prototypes in `../chunking/`.
