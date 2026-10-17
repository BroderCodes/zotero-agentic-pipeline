#!/usr/bin/env python3
"""
embed_library.py - Incremental SPECTER2 Embedding Builder for papers_index.parquet.

Uses AllenAI's `allenai/specter2_base` (100% local, trust_remote_code=False)
on Apple Silicon GPU (MPS) or CPU to embed Title + Abstract for every paper in
papers_index.json and store the normalized 768-d vectors in papers_index.parquet.

Incremental by default: existing embeddings in papers_index.parquet are reused,
so only newly added papers are embedded. Pass --force to re-embed all papers.
"""

import argparse
import json
import os
import re
import time

MODEL_NAME = "allenai/specter2_base"


def extract_abstract_from_note(note_path):
    """Reads the ## Abstract section from an existing Markdown note in notes/."""
    if not os.path.exists(note_path):
        return ""
    try:
        with open(note_path, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(
            r"##\s+Abstract\s*\n+(.*?)(?:\n+##\s+|\Z)",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not m:
            return ""
        abs_text = m.group(1).strip()
        if (
            not abs_text
            or "Abstract not extracted" in abs_text
            or "No abstract extractable" in abs_text
        ):
            return ""
        return re.sub(r"\s+", " ", abs_text)
    except Exception:
        return ""


def load_zotero_abstracts(canonical_path):
    """Loads clean abstracts from Zotero's canonical_library.json keyed by citekey and DOI."""
    by_ck = {}
    by_doi = {}
    if not os.path.exists(canonical_path):
        return by_ck, by_doi
    try:
        with open(canonical_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data if isinstance(data, list) else data.get("items", [])
        for it in items:
            raw_abs = it.get("abstract", "")
            if not raw_abs:
                continue
            clean_abs = re.sub(r"<[^>]+>", "", str(raw_abs))
            clean_abs = re.sub(r"\s+", " ", clean_abs).strip()
            if not clean_abs:
                continue
            ck = it.get("id")
            if ck:
                by_ck[ck] = clean_abs
            doi = (it.get("DOI") or "").strip().lower().replace("https://doi.org/", "")
            if doi:
                by_doi[doi] = clean_abs
    except Exception as e:
        print(f"Warning: could not read {canonical_path}: {e}")
    return by_ck, by_doi


def update_embeddings(
    manifest_path="papers_index.json",
    canonical_path="references/canonical_library.json",
    notes_dir="notes",
    force=False,
):
    import numpy as np
    import pandas as pd

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    parquet_symlink = manifest_path.replace(".json", ".parquet")
    real_parquet_path = os.path.realpath(parquet_symlink)

    # Load existing embeddings from Parquet if available and not forcing rebuild
    existing_vecs = {}
    existing_abstracts = {}
    if not force and os.path.exists(real_parquet_path):
        try:
            old_df = pd.read_parquet(real_parquet_path)
            if "embedding" in old_df.columns:
                for _, row in old_df.iterrows():
                    ck = row.get("citekey") or row.get("id")
                    vec = row.get("embedding")
                    if ck and vec is not None and len(vec) == 768:
                        existing_vecs[ck] = np.asarray(vec, dtype=np.float32)
                    if ck and isinstance(row.get("abstract"), str) and row.get("abstract"):
                        existing_abstracts[ck] = row.get("abstract")
        except Exception as e:
            print(f"Note: starting fresh embedding build ({e})")

    zot_by_ck, zot_by_doi = load_zotero_abstracts(canonical_path)

    # Prepare records and identify which items need embedding
    to_embed_indices = []
    to_embed_texts = []
    abstracts_col = []
    vectors_col = [None] * len(manifest)

    for idx, p in enumerate(manifest):
        ck = p.get("citekey") or p.get("id")
        doi = (p.get("doi") or "").strip().lower().replace("https://doi.org/", "")
        title = (p.get("title") or "").strip()

        # Resolve abstract: Zotero -> existing Parquet -> Markdown note
        abstract = (
            zot_by_ck.get(ck)
            or (zot_by_doi.get(doi) if doi else "")
            or existing_abstracts.get(ck)
            or extract_abstract_from_note(os.path.join(notes_dir, f"{ck}.md"))
            or ""
        )
        abstracts_col.append(abstract)

        if not force and ck in existing_vecs:
            vectors_col[idx] = existing_vecs[ck]
        else:
            to_embed_indices.append(idx)
            # SPECTER2 input format: Title + [SEP] + Abstract
            combined_text = f"{title} [SEP] {abstract}" if abstract else title
            to_embed_texts.append(combined_text)

    if to_embed_indices:
        import torch
        from sentence_transformers import SentenceTransformer

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        print(
            f"Embedding {len(to_embed_indices)} papers (reusing {len(existing_vecs)} cached) "
            f"with {MODEL_NAME} on {device.upper()}..."
        )
        t0 = time.time()
        try:
            model = SentenceTransformer(
                MODEL_NAME, device=device, trust_remote_code=False, local_files_only=True
            )
        except Exception:
            model = SentenceTransformer(
                MODEL_NAME, device=device, trust_remote_code=False
            )
        new_vecs = model.encode(
            to_embed_texts,
            batch_size=64,
            show_progress_bar=True,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        dt = time.time() - t0
        print(
            f"Embedded {len(to_embed_indices)} papers in {dt:.1f}s "
            f"({len(to_embed_indices) / max(dt, 0.01):.1f} papers/sec)."
        )

        for i, target_idx in enumerate(to_embed_indices):
            vectors_col[target_idx] = new_vecs[i].astype(np.float32)
    else:
        print(f"All {len(manifest)} papers already have SPECTER2 embeddings in {parquet_symlink}.")

    # Build DataFrame and write atomically to real_parquet_path
    df = pd.DataFrame(manifest)
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df["abstract"] = abstracts_col
    df["embedding"] = [v.tolist() if isinstance(v, np.ndarray) else v for v in vectors_col]

    temp_parquet = real_parquet_path + ".tmp"
    df.to_parquet(temp_parquet, index=False)
    os.replace(temp_parquet, real_parquet_path)
    print(f"Saved {len(df)} records (with 768-d SPECTER2 vectors) to {real_parquet_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build/update SPECTER2 embeddings in papers_index.parquet")
    parser.add_argument("--force", action="store_true", help="Re-embed all papers from scratch")
    args = parser.parse_args()

    update_embeddings(force=args.force)
