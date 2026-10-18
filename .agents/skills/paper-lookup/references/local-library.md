# Local Literature Library (`zotero-agentic-pipeline` — SPECTER2 + Obsidian Notes + PDFs)

This repository maintains a curated local literature library containing indexed papers, full-text PDFs (`papers/`), curated Obsidian Markdown notes (`notes/<citekey>.md`), and precomputed 768-d AllenAI SPECTER2 task-adapter vectors (`papers_index.parquet`).

**Always search the local library first** (via SPECTER2 semantic + keyword search, followed by reading the associated `notes/<citekey>.md` files) before or alongside querying external literature APIs.

---

## Location & Files

| Resource | Path | Description |
|---|---|---|
| **Workspace Root** | `./` | Local repository root |
| **Search CLI** | `scripts/search_library.py` | Hybrid semantic (`allenai/specter2_aug2023refresh_adhoc_query` vs `proximity` L2 distance) + lexical search over `papers_index.parquet` via DuckDB |
| **Vector & Metadata Index** | `papers_index.parquet` | Indexed papers with metadata, `abstract`, `embedding` (proximity adapter), and `embedding_clf` (classification adapter) |
| **Markdown Notes** | `notes/<citekey>.md` | Per-paper YAML frontmatter (`id`, `citekey`, `doi`, `year`, `projects`, `status`, `file`), `## Abstract`, and `## Notes` |
| **Full-Text PDFs** | `papers/<filename>.pdf` | Full-text PDFs referenced by the `file` field (`papers/...`) in the manifest and note frontmatter |

---

## Two-Step Local Retrieval Workflow

### Step 1: Initial Search via SPECTER2 (`scripts/search_library.py`)

Run `search_library.py` from the repository root:

```bash
# Natural-language semantic + keyword search (uses SPECTER2 adhoc_query adapter + L2 distance)
~/miniconda3/bin/python scripts/search_library.py \
  "how do CAR-T therapies target solid tumors?" \
  --top 10

# Filter by taxonomy project or publication year
~/miniconda3/bin/python scripts/search_library.py \
  "mRNA vaccine efficacy" --project Treatments --year-min 2020 --top 10

# Find papers most similar to a known citekey in the library (instant 0ms model load)
~/miniconda3/bin/python scripts/search_library.py \
  --similar mustermann2022mouse --top 10

# Machine-readable JSON output
~/miniconda3/bin/python scripts/search_library.py \
  "CRISPR base editing" --top 10 --json
```

| Parameter | Required | Default | Description |
|---|---|---|---|
| `query` | Positional (or `--similar`) | `None` | Natural-language search query encoded with `allenai/specter2_aug2023refresh_adhoc_query` |
| `--similar` | No | `None` | Citekey to find nearest neighbors for in SPECTER2 `proximity` L2 space |
| `--project` | No | `None` | Filter by taxonomy project tag (see `config/taxonomy.py`) |
| `--year-min` / `--year-max` | No | `None` | Publication year bounds |
| `--top` | No | `10` | Number of deduplicated results to return |
| `--json` | No | `False` | Emit structured JSON list |

---

### Step 2: Lookup Notes in the Associated Markdown (`notes/<citekey>.md`)

Every result returned by `search_library.py` includes a `note` path (`notes/<citekey>.md`) and a `file` path (`papers/<filename>.pdf`).

After running the SPECTER2 search:
1. **Read `notes/<citekey>.md`** for the relevant candidate papers.
2. **Check the YAML `status:` field and `## Notes` section:**
   - `status: verified` or `status: read` indicates a **curated, human-verified source** (often backing grant text or research claims) with detailed notes in `## Notes`. Prioritize these notes and surface their findings directly.
   - `status: unread` indicates an auto-generated stub containing YAML metadata and `## Abstract`, where you can also inspect the local PDF at `<file>` if full-text details (methods, figures, tables) are needed.

---

## Hazards & Mandatory Guards

- **NEVER overwrite or modify headers/content of notes with `status: verified` or `status: read`.** These notes form the verified source layer behind grants.
- **100% Offline Capable:** Both the SPECTER2 model + adapters (`allenai/specter2_aug2023refresh*`) and `papers_index.parquet` are cached locally, so local library search and note lookup work with zero network access (e.g., on an airplane).
- **Do not commit data files:** Never stage or commit `.pdf`, `notes/*.md`, `papers_index.json`, `papers_index.parquet`, or `references/canonical_library.json` to Git.
