import json
import os
import pandas as pd

def load_canonical_library(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    lookup_doi = {}
    lookup_file = {}
    
    items = data if isinstance(data, list) else data.get('items', [])
    
    for item in items:
        citekey = item.get('id')
        if not citekey:
            continue
            
        # Primary key: Clean DOI
        doi = item.get('DOI', '').strip().lower()
        if doi:
            if doi.startswith('https://doi.org/'):
                doi = doi.replace('https://doi.org/', '')
            lookup_doi[doi] = citekey
            
        # Secondary key: Filename from attachments
        attachments = item.get('attachments', [])
        for att in attachments:
            filepath = att.get('path', att.get('url', ''))
            if filepath and filepath.endswith('.pdf'):
                basename = os.path.basename(filepath)
                lookup_file[basename] = citekey
                stem = os.path.splitext(basename)[0]
                lookup_file[stem] = citekey
                
        # Also map by title just in case as a third fallback
        title = item.get('title', '').strip().lower()
        if title:
            # We don't return a separate title lookup to avoid passing too many dicts, 
            # but we could. For now, DOI and file stem are robust enough.
            pass
            
    return lookup_doi, lookup_file

def reconcile(manifest_path, canonical_path):
    if not os.path.exists(canonical_path):
        print(f"Warning: {canonical_path} not found.")
        print("Please set up Zotero Better BibTeX auto-export to this path before reconciling.")
        return
        
    lookup_doi, lookup_file = load_canonical_library(canonical_path)
    
    with open(manifest_path, 'r', encoding='utf-8') as f:
        papers = json.load(f)
        
    stats = {"matched_doi": 0, "matched_filename": 0, "unmatched": 0}
    
    for p in papers:
        matched = False
        
        # 1. Try matching by DOI
        doi = p.get('doi', '').strip().lower()
        if doi.startswith('https://doi.org/'):
            doi = doi.replace('https://doi.org/', '')
            
        if doi and doi in lookup_doi:
            p['citekey'] = lookup_doi[doi]
            stats["matched_doi"] += 1
            matched = True
        else:
            # 2. Try matching by PDF filename or stem
            if 'file' in p:
                filename = os.path.basename(p['file'])
                stem = os.path.splitext(filename)[0]
                
                if filename in lookup_file:
                    p['citekey'] = lookup_file[filename]
                    stats["matched_filename"] += 1
                    matched = True
                elif stem in lookup_file:
                    p['citekey'] = lookup_file[stem]
                    stats["matched_filename"] += 1
                    matched = True
                    
        if not matched:
            stats["unmatched"] += 1
            # If unmatched, we can default the citekey to the existing id
            # to ensure sync_obsidian_stubs.py still works without breaking
            if 'citekey' not in p:
                p['citekey'] = p['id']
            
    print("Reconciliation Stats:", stats)
    
    # Write updated dataset back to JSON atomically
    real_manifest_path = os.path.realpath(manifest_path)
    temp_json = real_manifest_path + '.tmp'
    with open(temp_json, 'w', encoding='utf-8') as f:
        json.dump(papers, f, indent=2)
    os.replace(temp_json, real_manifest_path)
    print(f"Atomically updated {manifest_path}")
        
    # Export to Parquet
    real_parquet_path = os.path.realpath(manifest_path.replace('.json', '.parquet'))
    df = pd.DataFrame(papers)
    # Ensure nested structures like lists are handled (e.g. authors, projects)
    # pyarrow requires string types for complex arrays, or native array types
    # It handles lists of strings natively.
    df.to_parquet(real_parquet_path)
    print(f"Exported to {manifest_path.replace('.json', '.parquet')}")

if __name__ == "__main__":
    MANIFEST = "papers_index.json"
    CANONICAL = "references/canonical_library.json"
    
    os.makedirs(os.path.dirname(CANONICAL), exist_ok=True)
    reconcile(MANIFEST, CANONICAL)
