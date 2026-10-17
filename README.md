# Paper Library & Agentic Knowledge Base

A streamlined, AI-ready scientific literature repository toolkit connecting Zotero, (Google Drive) cloud storage, and automated agent workflows.

---

## Tech Stack Overview

- **Reference Manager:** Zotero
  - **Better BibTeX:** Automatically generates collision-free citekeys and continuously exports the library metadata (`canonical_library.json`) in the background.
  - **Zotero Attanger:** Intercepts new PDFs, renames them, moves them safely to Google Drive, and replaces Zotero's internal file with a lightweight link.
  - **Zotero Connector:** Browser extension to quickly fetch metadata and PDFs directly from publisher websites.
- **Storage & Sync:** Google Drive
  - Contains all physical PDFs and Markdown notes, fully synced and backed up without cluttering the local Git repository. Note: make the folder available offline!
- **Note-Taking:** Obsidian (or Logseq/VS Code)
  - To view and edit the AI-generated Markdown notes (with yaml metadata).
- **Automation & Embedding Layer:** Python 3 (`pandas`, `pyarrow`, `duckdb`, `sentence-transformers`)
  - Bridges Zotero's metadata with the AI manifest and Obsidian vault using custom classification logic (`config/taxonomy.py`) and local scientific embeddings (`allenai/specter2_base`).
- **Agent Layer:** Your favorite out-of-the box or custom agent
  - Interacts with `papers_index.json`, queries `papers_index.parquet` via DuckDB + SPECTER2 semantic search (`scripts/search_library.py`), and reads/updates the Markdown notes and PDFs.

---

## Architecture Overview

This repository acts as the **bridge** between a human-facing citation manager (Zotero) and an AI-facing knowledge base (Google Drive, Obsidian, and LLM Agents).

### Layout
- **Git Repo** (`~/.../zotero-agentic-pipeline`): Tracks python automation scripts, configuration, and Zotero exports.
- **Zotero Data Directory** (`~/Zotero`): Strictly for Zotero's internal SQLite database and plugins. (Local only).
- **Google Drive Storage** (`~/Library/CloudStorage/.../papers`):
  - `papers/`: All PDFs stored completely flat.
  - `notes/`: Markdown note files matching the Zotero citekeys (`[citekey].md`).
  - `papers_index.json`: The AI-readable Master Corpus Manifest.
  - `papers_index.parquet`: Columnar manifest enriched with abstracts and 768-d `allenai/specter2_base` vectors for instant DuckDB semantic search.

---

## Initial Setup (For New Users)

Before running the workflow for the first time, configure Zotero and your local taxonomy:

1. **Configure Zotero Better BibTeX Auto-Export:**
   - In Zotero, right-click "My Library" (or a specific collection) and select **Export Library**.
   - Choose **Better CSL JSON** as the format.
   - Check the **"Keep updated"** box to enable automatic background syncing.
   - Save the file directly into this repository as `references/canonical_library.json`.
2. **Configure Your Taxonomy:**
   - Open `config/taxonomy.py`.
   - Replace the example dictionary with your own research projects and keywords. The Python bridge script uses these keywords to read the PDF and automatically tag your papers using regex matching (future updates will include a switch to LLM API calls).
3. **Configure Zotero Attanger (Optional but Recommended):**
   - Set up the Zotero Attanger plugin to automatically move and rename your PDFs from Zotero into your centralized `papers/` cloud storage folder.

---

## The Ingestion Workflow

To ingest new papers into the library seamlessly without duplication:

### 1. Capture & Link (The Zotero Layer)
1. **Download:** Save a PDF to your Downloads folder (or any other source folder, specified in the Zotero Attanger plugin).
2. **Ingest to Zotero:** Drag and drop the PDF into Zotero. 
   * *Zotero's built-in engine reads the text, queries Crossref/Google Scholar, and generates publisher metadata (Title, Authors, DOI).*
3. **Move to Drive:** Right-click the item in Zotero and select "Rename and Move" (using the **Zotero Attanger** plugin). 
   * *Attanger automatically moves the PDF to your Google Drive `papers/` folder and replaces Zotero's internal attachment with a lightweight link.*
4. **Auto-Export:** The **Better BibTeX** plugin instantly detects the new item, generates a clean citekey (e.g. `mustermann2022mouse`), and auto-updates `references/canonical_library.json` in the background.

### 2. Bridge & Sync (The Python Layer)
Once the paper is safely in Zotero and Google Drive, run the bridge script to sync the new data to your AI and Obsidian systems:

```bash
~/miniconda3/bin/python scripts/zotero_bridge.py
```

**What the script does behind the scenes:**
1. It reads Zotero's JSON export and spots the brand-new citekey.
2. It pulls the abstract from Zotero (or extracts it from the first 2 pages of the PDF in Google Drive).
3. It runs Title + Abstract + PDF text through `config/taxonomy.py` to automatically categorize the paper into your projects.
4. It appends the metadata and citekey to `papers_index.json` and incrementally embeds the new paper with `allenai/specter2_base` into `papers_index.parquet`.
5. It generates a brand-new Markdown stub in your `notes/` folder, pre-filled with YAML frontmatter and the abstract.

---

## Semantic Search (SPECTER2 + DuckDB)

Agents (or you) can search across your library locally and offline using `scripts/search_library.py`:

```bash
# Hybrid semantic (SPECTER2) + keyword search:
~/miniconda3/bin/python scripts/search_library.py "how do CAR-T therapies target solid tumors?"

# Filter by project and publication year:
~/miniconda3/bin/python scripts/search_library.py "mRNA vaccine efficacy" --project Treatments --year-min 2020

# Find papers most similar to a specific paper (instant vector lookup):
~/miniconda3/bin/python scripts/search_library.py --similar mustermann2022mouse
```

---

## Data Structures

### 1. Master Corpus Manifest (`papers_index.json`)
The Master Manifest is a lightweight catalog of the entire library designed for **instant AI agent ingestion into working memory**. Instead of a custom Python parser, it inherits metadata directly from Zotero:

```json
{
  "id": "mustermann2022mouse",
  "citekey": "mustermann2022mouse",
  "file": "papers/Mustermann-2022-Mouse-Work.pdf",
  "title": "Some really cool mouse work",
  "authors": ["Mustermann F", "Musterfrau S"],
  "year": 2022,
  "doi": "xx.yyyy/journal.2022.nn.mmm",
  "projects": ["Mouse_Methods"]
}
```

### 2. Individual Markdown Notes (`notes/[citekey].md`)
Each paper has a dedicated Markdown note pre-populated with YAML frontmatter. Because the filenames match the Zotero citekey, Obsidian links and AI agents can seamlessly reference citations:

```yaml
---
id: mustermann2022mouse
citekey: mustermann2022mouse
type: paper
doi: xx.yyyy/journal.2022.nn.mmm
year: 2022
projects:
  - Mouse_Methods
status: unread
updated: 2026-09-30
file: papers/Mustermann-2022-Mouse-Work.pdf
---

# Some really cool mouse work

**Authors:** Mustermann F, Musterfrau S  
**Projects:** Mouse_Methods

## Abstract
(Automatically populated from Zotero or PDF text)

## Notes
- 
```

---

## Utility Scripts Overview

- `scripts/zotero_bridge.py`: Ingestion pipeline connecting Zotero to the AI manifest, SPECTER2 embeddings, and Markdown vault (`--reclassify` re-runs taxonomy tagging across the full library).
- `scripts/search_library.py`: Hybrid semantic (`allenai/specter2_base`) + keyword search across `papers_index.parquet` via DuckDB.
- `scripts/embed_library.py`: Incremental SPECTER2 embedding builder for `papers_index.parquet` (`--force` rebuilds all embeddings from scratch).
- `scripts/reconcile_manifest_keys.py`: Reconciles a newly generated Zotero BBT library back to the master manifest by matching DOIs and PDF filenames.
- `scripts/sync_obsidian_stubs.py`: Renames and safely updates the YAML headers of existing Markdown notes when citekeys change.