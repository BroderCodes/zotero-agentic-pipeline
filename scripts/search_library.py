#!/usr/bin/env python3
"""
search_library.py - Hybrid Semantic (SPECTER2) + Keyword Search via DuckDB.

Examples:
  # Natural language semantic + keyword search:
  ~/miniconda3/bin/python scripts/search_library.py "how do CAR-T therapies target solid tumors?"

  # Filter by project or publication year:
  ~/miniconda3/bin/python scripts/search_library.py "mRNA vaccine efficacy" --project Treatments --year-min 2020

  # Find papers most similar to a known paper in your library (instant, 0ms model load):
  ~/miniconda3/bin/python scripts/search_library.py --similar mustermann2022mouse
"""

import argparse
import json
import os
import re
import sys

MODEL_NAME = "allenai/specter2_base"

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from",
    "how", "in", "into", "is", "it", "of", "on", "or", "that", "the", "to", "what",
    "when", "where", "which", "who", "why", "with", "affect", "effects", "effect",
    "role", "study", "studies", "papers", "paper", "about", "between", "across"
}


def encode_query(query_text):
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    try:
        model = SentenceTransformer(
            MODEL_NAME, device=device, trust_remote_code=False, local_files_only=True
        )
    except Exception:
        model = SentenceTransformer(
            MODEL_NAME, device=device, trust_remote_code=False
        )
    vec = model.encode(
        [query_text],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )[0]
    return vec


def run_search(
    query=None,
    similar_to=None,
    project=None,
    year_min=None,
    year_max=None,
    top_k=10,
    as_json=False,
    parquet_path="papers_index.parquet",
):
    import duckdb
    import numpy as np

    real_parquet = os.path.realpath(parquet_path)
    if not os.path.exists(real_parquet):
        print(f"Error: {real_parquet} not found. Run scripts/embed_library.py first.", file=sys.stderr)
        sys.exit(1)

    con = duckdb.connect()

    # Load candidate rows using DuckDB SQL filtering
    where_clauses = ["embedding IS NOT NULL"]
    params = []

    if project:
        where_clauses.append("list_contains(projects, ?)")
        params.append(project)
    if year_min is not None:
        where_clauses.append("year >= ?")
        params.append(year_min)
    if year_max is not None:
        where_clauses.append("year <= ?")
        params.append(year_max)

    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT id, citekey, title, authors, year, doi, file, projects, abstract, embedding
        FROM read_parquet('{real_parquet}')
        WHERE {where_sql}
    """
    df = con.execute(sql, params).df()
    if df.empty:
        print("No matching papers found for the given filters.")
        return

    # Stack normalized 768-d vectors into a contiguous float32 matrix
    matrix = np.vstack(df["embedding"].values).astype(np.float32)

    if similar_to:
        # Look up the target paper's embedding directly from the parquet file
        target_df = con.execute(
            f"SELECT citekey, title, embedding FROM read_parquet('{real_parquet}') WHERE citekey = ? OR id = ?",
            [similar_to, similar_to],
        ).df()
        if target_df.empty or target_df.iloc[0]["embedding"] is None:
            print(f"Error: citekey '{similar_to}' not found in {parquet_path}.", file=sys.stderr)
            sys.exit(1)
        q_vec = np.asarray(target_df.iloc[0]["embedding"], dtype=np.float32)
        semantic_scores = matrix @ q_vec
        hybrid_scores = semantic_scores
    else:
        q_vec = encode_query(query)
        semantic_scores = matrix @ q_vec

        # Compute lightweight lexical keyword boost on Title + Abstract
        keywords = [
            w.lower()
            for w in re.findall(r"[a-zA-Z0-9\-]{3,}", query or "")
            if w.lower() not in STOPWORDS
        ]
        lexical_boost = np.zeros(len(df), dtype=np.float32)
        if keywords:
            titles_low = df["title"].fillna("").str.lower()
            abs_low = df["abstract"].fillna("").str.lower()
            for kw in keywords:
                in_title = titles_low.str.contains(re.escape(kw), regex=True).to_numpy(dtype=np.float32)
                in_abs = abs_low.str.contains(re.escape(kw), regex=True).to_numpy(dtype=np.float32)
                lexical_boost += 0.035 * in_title + 0.015 * in_abs
            lexical_boost = np.clip(lexical_boost, 0.0, 0.15)

        hybrid_scores = semantic_scores + lexical_boost

    df["score"] = hybrid_scores
    df["semantic_score"] = semantic_scores
    if similar_to:
        df = df[df["citekey"] != similar_to]

    top_df = df.sort_values("score", ascending=False).head(top_k)

    results = []
    for _, row in top_df.iterrows():
        authors_list = list(row["authors"]) if row["authors"] is not None else []
        projs_list = list(row["projects"]) if row["projects"] is not None else []
        abs_str = str(row["abstract"] or "")
        results.append(
            {
                "citekey": row["citekey"],
                "title": row["title"],
                "authors": authors_list[:5],
                "year": int(row["year"]) if pd.notna(row["year"]) else None,
                "doi": row["doi"],
                "file": row["file"],
                "note": f"notes/{row['citekey']}.md",
                "projects": projs_list,
                "score": round(float(row["score"]), 4),
                "abstract_snippet": (abs_str[:320] + "...") if len(abs_str) > 320 else abs_str,
            }
        )

    if as_json:
        print(json.dumps(results, indent=2))
        return

    header = f"Top {len(results)} results for: '{similar_to or query}'"
    print(f"\n{header}\n" + "=" * len(header))
    for rank, r in enumerate(results, 1):
        auth_str = ", ".join(r["authors"]) + (" et al." if len(r["authors"]) == 5 else "")
        proj_str = ", ".join(r["projects"])
        print(f"\n{rank}. [{r['citekey']}] ({r['year'] or 'n.d.'}) | Score: {r['score']:.3f} | Projects: {proj_str}")
        print(f"   Title:   {r['title']}")
        if auth_str:
            print(f"   Authors: {auth_str}")
        print(f"   Note:    {r['note']}  |  PDF: {r['file']}")
        if r["abstract_snippet"]:
            print(f"   Abstract: {r['abstract_snippet']}")


if __name__ == "__main__":
    import pandas as pd

    parser = argparse.ArgumentParser(description="Hybrid SPECTER2 + DuckDB Literature Search")
    parser.add_argument("query", nargs="?", default=None, help="Natural language search query")
    parser.add_argument("--similar", default=None, help="Find papers similar to a given citekey")
    parser.add_argument("--project", default=None, help="Filter by project name (e.g. Treatments)")
    parser.add_argument("--year-min", type=int, default=None, help="Minimum publication year")
    parser.add_argument("--year-max", type=int, default=None, help="Maximum publication year")
    parser.add_argument("--top", type=int, default=10, help="Number of results to return (default: 10)")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    if not args.query and not args.similar:
        parser.error("Provide either a search query or --similar <citekey>.")

    run_search(
        query=args.query,
        similar_to=args.similar,
        project=args.project,
        year_min=args.year_min,
        year_max=args.year_max,
        top_k=args.top,
        as_json=args.json,
    )
