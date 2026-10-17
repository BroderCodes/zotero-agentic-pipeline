#!/usr/bin/env python3
"""
embed_library.py - Incremental SPECTER2 Adapter Embedding Builder for papers_index.parquet.

Uses AllenAI's `allenai/specter2_aug2023refresh_base` paired with task-specific
adapters via the `adapters` library (100% local/offline once cached):
  - `allenai/specter2_aug2023refresh` (proximity adapter) -> `embedding` column
    (for document similarity & retrieval indexing)
  - `allenai/specter2_aug2023refresh_classification` (classification adapter) -> `embedding_clf` column
    (for linear/MLP triage classification)

Follows AllenAI's exact SciRepEval specification:
  - Input: `title + tokenizer.sep_token + abstract`
  - Tokenizer: `padding=True, truncation=True, return_token_type_ids=False, max_length=512`
  - Representation: Unnormalized `[CLS]` token (`output.last_hidden_state[:, 0, :]`)
    trained for L2 (Euclidean) distance.

Incremental by default: existing embeddings in papers_index.parquet are reused,
so only newly added papers are embedded. Pass --force to re-embed all papers.
"""

import argparse
import json
import os
import re
import sys
import time

BASE_MODEL = "allenai/specter2_aug2023refresh_base"
PROXIMITY_ADAPTER = "allenai/specter2_aug2023refresh"
QUERY_ADAPTER = "allenai/specter2_aug2023refresh_adhoc_query"
CLASSIFICATION_ADAPTER = "allenai/specter2_aug2023refresh_classification"


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


def _resolve_adapter_path(repo_id):
    """Resolves adapter weights from local Hugging Face cache first (airplane-mode safe)."""
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(repo_id=repo_id, local_files_only=True)
    except Exception:
        return snapshot_download(repo_id=repo_id, local_files_only=False)


def load_specter2_model(device=None, adapters_to_load=("proximity", "classification")):
    """Loads SPECTER2 base model, tokenizer, and requested task adapters (offline-first)."""
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import logging
    import torch
    from adapters import AutoAdapterModel
    from transformers import AutoTokenizer

    logging.getLogger("adapters").setLevel(logging.ERROR)

    if device is None:
        device = "mps" if torch.backends.mps.is_available() else "cpu"

    try:
        tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, local_files_only=True)
        model = AutoAdapterModel.from_pretrained(BASE_MODEL, local_files_only=True)
    except Exception:
        tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
        model = AutoAdapterModel.from_pretrained(BASE_MODEL)

    adapter_map = {
        "proximity": PROXIMITY_ADAPTER,
        "adhoc_query": QUERY_ADAPTER,
        "classification": CLASSIFICATION_ADAPTER,
    }
    for name in adapters_to_load:
        repo_id = adapter_map[name]
        local_dir = _resolve_adapter_path(repo_id)
        model.load_adapter(local_dir, load_as=name, set_active=(name == adapters_to_load[0]))

    model.set_active_adapters(adapters_to_load[0])
    model.to(device)
    model.eval()
    return tokenizer, model, device


def encode_batches(tokenizer, model, device, texts, adapter_name, batch_size=64, show_progress=True):
    """Encodes a list of already-formatted texts using the specified active adapter.
    Returns unnormalized 768-d [CLS] embeddings as float32 numpy array.
    """
    import numpy as np
    import torch
    from tqdm import tqdm

    model.set_active_adapters(adapter_name)
    all_vecs = []
    total = len(texts)
    rng = range(0, total, batch_size)
    if show_progress:
        rng = tqdm(rng, desc=f"SPECTER2 [{adapter_name}]", unit="batch")

    with torch.no_grad():
        for start in rng:
            batch = texts[start : start + batch_size]
            inputs = tokenizer(
                batch,
                padding=True,
                truncation=True,
                return_tensors="pt",
                return_token_type_ids=False,
                max_length=512,
            )
            inputs = {k: v.to(device) for k, v in inputs.items()}
            outputs = model(**inputs)
            # Take the [CLS] token (index 0) unnormalized per AllenAI specification
            cls_vecs = outputs.last_hidden_state[:, 0, :].detach().cpu().numpy().astype(np.float32)
            all_vecs.append(cls_vecs)

    return np.vstack(all_vecs) if all_vecs else np.empty((0, 768), dtype=np.float32)


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
    existing_prox = {}
    existing_clf = {}
    existing_abstracts = {}
    if not force and os.path.exists(real_parquet_path):
        try:
            old_df = pd.read_parquet(real_parquet_path)
            if "embedding" in old_df.columns and "embedding_clf" in old_df.columns:
                for _, row in old_df.iterrows():
                    ck = row.get("citekey") or row.get("id")
                    v_prox = row.get("embedding")
                    v_clf = row.get("embedding_clf")
                    if (
                        ck
                        and v_prox is not None
                        and len(v_prox) == 768
                        and v_clf is not None
                        and len(v_clf) == 768
                    ):
                        existing_prox[ck] = np.asarray(v_prox, dtype=np.float32)
                        existing_clf[ck] = np.asarray(v_clf, dtype=np.float32)
                    if ck and isinstance(row.get("abstract"), str) and row.get("abstract"):
                        existing_abstracts[ck] = row.get("abstract")
        except Exception as e:
            print(f"Note: starting fresh embedding build ({e})")

    zot_by_ck, zot_by_doi = load_zotero_abstracts(canonical_path)

    to_embed_indices = []
    to_embed_pairs = []
    abstracts_col = []
    prox_col = [None] * len(manifest)
    clf_col = [None] * len(manifest)

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

        if not force and ck in existing_prox and ck in existing_clf:
            prox_col[idx] = existing_prox[ck]
            clf_col[idx] = existing_clf[ck]
        else:
            to_embed_indices.append(idx)
            to_embed_pairs.append((title, abstract))

    if to_embed_indices:
        t0 = time.time()
        tokenizer, model, device = load_specter2_model(
            adapters_to_load=("proximity", "classification")
        )
        # Format exactly per AllenAI: title + tokenizer.sep_token + (abstract or '')
        sep = tokenizer.sep_token
        formatted_texts = [f"{t}{sep}{a}" for t, a in to_embed_pairs]

        print(
            f"Embedding {len(to_embed_indices)} papers (reusing {len(existing_prox)} cached) "
            f"with {BASE_MODEL} + [proximity, classification] adapters on {device.upper()}..."
        )
        new_prox = encode_batches(
            tokenizer, model, device, formatted_texts, adapter_name="proximity", batch_size=64
        )
        new_clf = encode_batches(
            tokenizer, model, device, formatted_texts, adapter_name="classification", batch_size=64
        )
        dt = time.time() - t0
        print(
            f"Computed proximity + classification embeddings for {len(to_embed_indices)} papers "
            f"in {dt:.1f}s ({len(to_embed_indices) / max(dt, 0.01):.1f} papers/sec)."
        )

        for i, target_idx in enumerate(to_embed_indices):
            prox_col[target_idx] = new_prox[i]
            clf_col[target_idx] = new_clf[i]
    else:
        print(
            f"All {len(manifest)} papers already have SPECTER2 [proximity + classification] "
            f"embeddings in {parquet_symlink}."
        )

    # Build DataFrame and write atomically to real_parquet_path
    df = pd.DataFrame(manifest)
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df["abstract"] = abstracts_col
    df["embedding"] = [v.tolist() if isinstance(v, np.ndarray) else v for v in prox_col]
    df["embedding_clf"] = [v.tolist() if isinstance(v, np.ndarray) else v for v in clf_col]

    temp_parquet = real_parquet_path + ".tmp"
    df.to_parquet(temp_parquet, index=False)
    os.replace(temp_parquet, real_parquet_path)
    print(
        f"Saved {len(df)} records (with 768-d proximity `embedding` & classification `embedding_clf`) "
        f"to {real_parquet_path}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build/update SPECTER2 adapter embeddings in papers_index.parquet"
    )
    parser.add_argument("--force", action="store_true", help="Re-embed all papers from scratch")
    args = parser.parse_args()

    update_embeddings(force=args.force)
