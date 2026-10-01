import json
import os
import sys
import subprocess
import datetime
from pathlib import Path

# Ensure we can import taxonomy.py from the parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from config import taxonomy
except ImportError:
    print("Could not import config.taxonomy. Ensure it exists.")
    sys.exit(1)

def extract_text_from_pdf(pdf_path):
    """Uses pdftotext to grab the first 2 pages for taxonomy classification."""
    try:
        result = subprocess.run(
            ['pdftotext', '-f', '1', '-l', '2', str(pdf_path), '-'],
            capture_output=True, text=True, timeout=8
        )
        return result.stdout
    except Exception as e:
        print(f"Failed to extract text from {pdf_path}: {e}")
        return ""

def create_note_stub(citekey, p_data, notes_dir):
    note_path = os.path.join(notes_dir, f"{citekey}.md")
    if os.path.exists(note_path):
        return # Don't overwrite existing notes
        
    date_str = datetime.datetime.now().strftime('%Y-%m-%d')
    
    yaml = f"""---
id: {citekey}
citekey: {citekey}
type: 
doi: {p_data.get('doi', '')}
year: {p_data.get('year', '')}
status: unread
updated: {date_str}
file: {p_data.get('file', '')}
---

# {p_data.get('title', 'Unknown Title')}

**Authors:** {', '.join(p_data.get('authors', []))}
**Projects:** {', '.join(p_data.get('projects', []))}

## Abstract
(Abstract not extracted - fetch from Zotero or web if needed)

## Notes
- 
"""
    with open(note_path, 'w', encoding='utf-8') as f:
        f.write(yaml)

def run_bridge(manifest_path, canonical_path, notes_dir, drive_base):
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
        
    # Build lookup set of existing citekeys
    existing_keys = set(p.get('citekey', p.get('id')) for p in manifest)
    
    with open(canonical_path, 'r', encoding='utf-8') as f:
        zotero_data = json.load(f)
        
    items = zotero_data if isinstance(zotero_data, list) else zotero_data.get('items', [])
    
    new_items_count = 0
    
    for item in items:
        citekey = item.get('id')
        if not citekey or citekey in existing_keys:
            continue
            
        print(f"New item detected from Zotero: {citekey}")
        
        # 1. Get the PDF file path
        pdf_abs_path = ""
        rel_file = ""
        for att in item.get('attachments', []):
            path = att.get('path', att.get('url', ''))
            if path and path.endswith('.pdf'):
                pdf_abs_path = path
                rel_file = f"papers/{os.path.basename(path)}"
                break
                
        # 2. Extract text and classify
        projects = ["Unclassified"]
        if pdf_abs_path and os.path.exists(pdf_abs_path):
            text = extract_text_from_pdf(pdf_abs_path)
            if text and hasattr(taxonomy, 'TAXONOMY'):
                import re
                matched = []
                for proj, keyword_str in taxonomy.TAXONOMY.items():
                    keywords = [k.strip() for k in keyword_str.split(',')]
                    pattern = r'\b(' + '|'.join(re.escape(k) for k in keywords) + r')\b'
                    if re.search(pattern, text, re.IGNORECASE):
                        matched.append(proj)
                
                if matched:
                    projects = matched
                elif re.search(r'\b(review|protocol|method|textbook)\b', text, re.IGNORECASE):
                    projects = ["General_Reference"]
        
        # Format Authors
        authors = []
        for author in item.get('author', []):
            if 'family' in author and 'given' in author:
                authors.append(f"{author['given']} {author['family']}")
            elif 'literal' in author:
                authors.append(author['literal'])
            elif 'family' in author:
                authors.append(author['family'])
                
        # Handle DOI
        doi = item.get('DOI', '').strip()
        if doi.startswith('https://doi.org/'):
            doi = doi.replace('https://doi.org/', '')
            
        # Year
        year = ""
        if 'issued' in item and 'date-parts' in item['issued']:
            try:
                year = item['issued']['date-parts'][0][0]
            except (IndexError, TypeError):
                pass
                
        p_data = {
            "id": citekey,
            "citekey": citekey,
            "file": rel_file,
            "title": item.get('title', ''),
            "authors": authors,
            "year": int(year) if year else "",
            "doi": doi,
            "projects": projects
        }
        
        # 3. Add to manifest
        manifest.append(p_data)
        
        # 4. Create Note Stub
        create_note_stub(citekey, p_data, notes_dir)
        new_items_count += 1
        
    if new_items_count > 0:
        # Save manifest
        real_manifest_path = os.path.realpath(manifest_path)
        temp_json = real_manifest_path + '.tmp'
        with open(temp_json, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)
        os.replace(temp_json, real_manifest_path)
        print(f"Successfully bridged {new_items_count} new papers from Zotero to the manifest!")
        
        # Save Parquet
        real_parquet_path = os.path.realpath(manifest_path.replace('.json', '.parquet'))
        try:
            import pandas as pd
            df = pd.DataFrame(manifest)
            df.to_parquet(real_parquet_path)
        except ImportError:
            pass
    else:
        print("No new items found in Zotero.")

if __name__ == "__main__":
    MANIFEST = "papers_index.json"
    CANONICAL = "references/canonical_library.json"
    NOTES_DIR = "notes"
    DRIVE_BASE = "/path/to/your/zotero/papers"
    
    os.makedirs(NOTES_DIR, exist_ok=True)
    
    if not os.path.exists(CANONICAL):
        print(f"Error: {CANONICAL} not found.")
        sys.exit(1)
        
    run_bridge(MANIFEST, CANONICAL, NOTES_DIR, DRIVE_BASE)
