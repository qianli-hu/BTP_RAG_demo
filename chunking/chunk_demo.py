"""
Structure-aware chunker demo for SAP BTP docs (SAP AI Core PDF).
- heading detection via font size  - boilerplate (header/footer) stripping
- section-bounded chunking w/ overlap  - tables -> Markdown  - metadata + hash
Run: python3 chunk_demo.py
"""
import fitz, pdfplumber, re, hashlib, json
from collections import Counter

PDF = "docs/sap-ai-core.pdf"
TARGET_TOK = 220          # ~ chars/4
OVERLAP_SENT = 1
def toks(s): return max(1, len(s) // 4)
def h(s): return hashlib.sha1(s.encode()).hexdigest()[:10]

doc = fitz.open(PDF)

# ---- 1. find boilerplate lines (repeat across many pages) ----
line_freq = Counter()
pages_lines = []
for pg in doc:
    lines = []
    for b in pg.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            txt = "".join(s["text"] for s in l["spans"]).strip()
            size = max((s["size"] for s in l["spans"]), default=0)
            if txt:
                lines.append((txt, round(size, 1)))
                line_freq[txt] += 1
    pages_lines.append(lines)
boiler = {t for t, c in line_freq.items() if c > doc.page_count * 0.25 or re.fullmatch(r"\d+|PUBLIC|© \d+ SAP SE.*", t)}

# ---- 2. heading-aware sectioning ----
def level(size):
    if size >= 17: return 1
    if size >= 13.5: return 2
    if size >= 11.5: return 3
    return 0  # body

chunks = []
path = {}                      # level -> heading text (breadcrumb)
buf, buf_page = [], None

def flush(section_path, page):
    """split buffered body text into token-bounded, sentence-overlapped chunks"""
    global buf
    text = " ".join(buf).strip()
    buf = []
    if len(text) < 40:
        return
    sents = re.split(r"(?<=[.!?])\s+", text)
    cur, cur_tok = [], 0
    for s in sents:
        if cur and cur_tok + toks(s) > TARGET_TOK:
            body = " ".join(cur)
            chunks.append({"section_path": section_path, "page": page,
                           "tokens": toks(body), "hash": h(body), "text": body})
            cur = cur[-OVERLAP_SENT:]; cur_tok = sum(toks(x) for x in cur)
        cur.append(s); cur_tok += toks(s)
    if cur:
        body = " ".join(cur)
        chunks.append({"section_path": section_path, "page": page,
                       "tokens": toks(body), "hash": h(body), "text": body})

for pno, lines in enumerate(pages_lines):
    for txt, size in lines:
        if txt in boiler:
            continue
        lv = level(size)
        if lv:                                   # heading -> close current section
            cur_path = " › ".join(path[k] for k in sorted(path) if k <= max(path or [0]))
            flush(cur_path, buf_page)
            path[lv] = txt
            for k in list(path):                 # drop deeper levels
                if k > lv: del path[k]
            buf_page = pno
        else:                                    # body
            if buf_page is None: buf_page = pno
            buf.append(txt)
final_path = " › ".join(path[k] for k in sorted(path))
flush(final_path, buf_page)

# ---- 3. tables -> Markdown (scan whole doc, keep non-trivial ones) ----
def tbl_md(rows):
    rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in rows]
    w = max(len(r) for r in rows); rows = [r + [""] * (w - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * w) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)

md_tables = []
with pdfplumber.open(PDF) as pdf:
    for i, pg in enumerate(pdf.pages):
        for t in pg.extract_tables():
            filled = sum(1 for r in t for c in r if c and c.strip())
            if len(t) >= 3 and len(t[0]) >= 2 and filled >= 6:   # skip junk/false-positives
                md_tables.append((i, tbl_md(t)))

# ---- 4. write a readable preview ----
content = [c for c in chunks if c["tokens"] >= 25 and c["section_path"]]
with open("chunks_preview.md", "w") as f:
    f.write(f"# Chunking preview — SAP AI Core doc ({doc.page_count} pp)\n\n")
    f.write(f"- total chunks: **{len(chunks)}** (content chunks ≥25 tok: {len(content)})\n")
    avg = sum(c['tokens'] for c in chunks)//max(1,len(chunks))
    f.write(f"- avg chunk size: ~{avg} tokens · boilerplate lines stripped: {len(boiler)} · tables→MD: {len(md_tables)}\n\n---\n\n")
    f.write("## Sample content chunks (from mid-document)\n\n")
    for c in content[40:46]:
        f.write(f"**chunk `{c['hash']}`** · page {c['page']} · ~{c['tokens']} tok\n")
        f.write(f"`section: {c['section_path'] or '(none)'}`\n\n")
        f.write("> " + c["text"][:700].replace("\n", " ") + ("…" if len(c["text"])>700 else "") + "\n\n")
    if md_tables:
        pg, md = md_tables[0]
        f.write(f"---\n\n## Sample table → Markdown (page {pg})\n\n{md}\n")

print(f"pages={doc.page_count} chunks={len(chunks)} content={len(content)} "
      f"boiler_stripped={len(boiler)} tables={len(md_tables)} avg_tok={sum(c['tokens'] for c in chunks)//max(1,len(chunks))}")
print("wrote chunks_preview.md")
