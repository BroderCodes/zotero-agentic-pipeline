import argparse
import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Ensure we can import taxonomy.py from the parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from config import taxonomy
except ImportError:
    print("Could not import config.taxonomy. Ensure it exists.")
    sys.exit(1)


def compile_taxonomy_patterns():
    r"""
    Compiles regex patterns for each project in taxonomy.TAXONOMY.
    - Expands parentheticals like 'Term (ACRONYM)' into separate terms.
    - Matches short uppercase acronyms case-sensitively.
    - Matches multi-word phrases across spaces, hyphens, or PDF line breaks ([-\s]+).
    - Allows optional plural 's'/'es' at the end of keywords.
    """
    if not hasattr(taxonomy, 'TAXONOMY'):
        return {}

    compiled = {}
    for proj, keyword_str in taxonomy.TAXONOMY.items():
        if proj in ("Unclassified", "General_Reference"):
            continue

        raw_chunks = [k.strip() for k in keyword_str.split(',') if k.strip()]
        expanded_keywords = []
        for chunk in raw_chunks:
            # Extract parenthetical acronyms if present: e.g. "Term (ACRONYM)"
            m_paren = re.match(r'^(.*?)\s*\(([^)]+)\)\s*(.*)$', chunk)
            if m_paren:
                main_term = (m_paren.group(1) + " " + m_paren.group(3)).strip()
                acronym = m_paren.group(2).strip()
                if main_term:
                    expanded_keywords.append(main_term)
                if acronym:
                    expanded_keywords.append(acronym)
            else:
                expanded_keywords.append(chunk)

        patterns = []
        for kw in expanded_keywords:
            # Check if keyword is an uppercase acronym (with optional trailing 's')
            clean_acro = kw[:-1] if (kw.endswith('s') and len(kw) > 2 and kw[:-1].isupper()) else kw
            if clean_acro.isupper() and 2 <= len(clean_acro) <= 6:
                if clean_acro.endswith('S'):
                    pat = rf'\b{re.escape(clean_acro)}\b'
                else:
                    pat = rf'\b{re.escape(clean_acro)}s?\b'
                patterns.append(re.compile(pat))  # Case-sensitive for short acronyms
            else:
                # Split by whitespace or hyphens so PDF linebreaks/hyphens match seamlessly
                tokens = [t for t in re.split(r'[-\s/]+', kw.strip()) if t]
                if not tokens:
                    continue
                escaped_tokens = [re.escape(t) for t in tokens[:-1]]
                last = tokens[-1]
                last_low = last.lower()
                if last_low.endswith(('ss', 'x', 'ch', 'sh')):
                    last_pat = re.escape(last) + r'(?:es)?'
                elif last_low.endswith('is') and len(last) > 3:
                    last_pat = re.escape(last[:-2]) + r'(?:is|es)'
                elif last_low.endswith('y') and len(last) > 3 and last_low[-2] not in 'aeiou':
                    last_pat = re.escape(last[:-1]) + r'(?:y|ies)'
                elif last_low.endswith('s') and len(last) > 3:
                    last_pat = re.escape(last[:-1]) + r's?'
                else:
                    last_pat = re.escape(last) + r's?'
                escaped_tokens.append(last_pat)
                joined = r'[-\s]+'.join(escaped_tokens)
                patterns.append(re.compile(rf'\b{joined}\b', re.IGNORECASE))

        compiled[proj] = patterns
    return compiled


TAXONOMY_PATTERNS = compile_taxonomy_patterns()
GENERAL_REF_PATTERN = re.compile(r'\b(reviews?|protocols?|methods?|textbooks?)\b', re.IGNORECASE)


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


def clean_abstract(abstract_text):
    """Cleans HTML/JATS tags and excess whitespace from Zotero abstracts."""
    if not abstract_text:
        return ""
    cleaned = re.sub(r'<[^>]+>', '', str(abstract_text))
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def extract_abstract_fallback(pdf_text):
    """Fallback to extract an abstract snippet from the first 2 PDF pages if Zotero abstract is missing."""
    if not pdf_text:
        return ""
    lines = [l.strip() for l in pdf_text.splitlines() if l.strip()]
    abstract_start = -1
    for idx, l in enumerate(lines[:40]):
        if re.match(r'^(?:abstract|summary)\b', l, re.IGNORECASE):
            abstract_start = idx
            break

    if abstract_start >= 0:
        collected = []
        for l in lines[abstract_start:]:
            if collected and re.match(r'^(?:introduction|keywords|background|significance|results|1\.|\xa9)\b', l, re.IGNORECASE):
                break
            if len(collected) == 0 and l.lower().rstrip(':') in ('abstract', 'summary'):
                continue
            collected.append(l)
            if len(" ".join(collected)) > 1600:
                break
        abstract_text = " ".join(collected).strip()
        if len(abstract_text) > 40:
            return abstract_text

    # Second fallback for journals (e.g. Nature/Science) that use an unlabeled summary paragraph
    blocks = [b.strip() for b in re.split(r'\n\s*\n', pdf_text) if b.strip()]
    for b in blocks[:8]:
        flat = re.sub(r'\s+', ' ', b).strip()
        if len(flat) < 250 or len(flat) > 2200:
            continue
        low = flat.lower()
        if '✉' in flat or re.search(r'\d+,\d+', flat):
            continue
        if any(skip in low for skip in ('university', 'department', 'institute', 'received:', 'accepted:', 'corresponding author')):
            continue
        if flat.count('. ') >= 2 and any(w in low for w in (' the ', ' and ', ' that ', ' with ', ' here ')):
            return flat
    return ""


def classify_text(text="", title="", abstract=""):
    """Classifies a paper across all taxonomy projects using Title + Abstract + PDF text."""
    combined = "\n".join(part for part in (title, abstract, text) if part)
    if not combined.strip():
        return ["Unclassified"]

    if TAXONOMY_PATTERNS:
        matched = []
        for proj, patterns in TAXONOMY_PATTERNS.items():
            if any(p.search(combined) for p in patterns):
                matched.append(proj)
        if matched:
            return matched
        if GENERAL_REF_PATTERN.search(combined):
            return ["General_Reference"]
    elif hasattr(taxonomy, 'assign_projects'):
        return taxonomy.assign_projects(combined)

    return ["Unclassified"]


def create_note_stub(citekey, p_data, notes_dir, abstract="", overwrite=False):
    """Creates or updates the Obsidian Markdown note stub with Zotero abstract and projects."""
    note_path = os.path.join(notes_dir, f"{citekey}.md")
    cleaned_abs = clean_abstract(abstract or p_data.get('abstract', ''))
    abstract_block = cleaned_abs if cleaned_abs else "(Abstract not extracted - fetch from Zotero or web if needed)"
    projects_list = p_data.get('projects', ['Unclassified'])
    yaml_projects = "\n".join(f"  - {proj}" for proj in projects_list)
    date_str = datetime.datetime.now().strftime('%Y-%m-%d')

    if os.path.exists(note_path):
        if not overwrite:
            return
        # Safe in-place update preserving user notes in body
        try:
            with open(note_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 1. Update YAML frontmatter projects & file
            parts = re.split(r'^(?:---|\.\.\.)\r?\n', content, maxsplit=2, flags=re.MULTILINE)
            if len(parts) >= 3:
                fm = parts[1]
                body = parts[2]

                if re.search(r'^projects:\s*\n(?:\s*-\s*.*\n)+', fm, flags=re.MULTILINE):
                    fm = re.sub(
                        r'^projects:\s*\n(?:\s*-\s*.*\n)+',
                        f"projects:\n{yaml_projects}\n",
                        fm,
                        flags=re.MULTILINE
                    )
                elif re.search(r'^projects:.*$', fm, flags=re.MULTILINE):
                    fm = re.sub(
                        r'^projects:.*$',
                        f"projects:\n{yaml_projects}",
                        fm,
                        flags=re.MULTILINE
                    )
                else:
                    fm = fm.rstrip('\n') + f"\nprojects:\n{yaml_projects}\n"

                fm = re.sub(r'^type:\s*$', 'type: paper', fm, flags=re.MULTILINE)

                if p_data.get('file'):
                    if re.search(r'^file:\s*$', fm, flags=re.MULTILINE):
                        fm = re.sub(r'^file:\s*$', f"file: {p_data['file']}", fm, flags=re.MULTILINE)

                body = re.sub(
                    r'^\*\*Projects:\*\*.*$',
                    f"**Projects:** {', '.join(projects_list)}",
                    body,
                    flags=re.MULTILINE
                )

                if cleaned_abs:
                    body = re.sub(
                        r'\(Abstract not extracted - fetch from Zotero or web if needed\)|No abstract extractable from PDF text\.',
                        lambda _: cleaned_abs,
                        body
                    )

                with open(note_path, 'w', encoding='utf-8') as f:
                    f.write(f"---\n{fm}---\n{body}")
                return
        except Exception as e:
            print(f"Warning: could not update note {note_path}: {e}")
            return

    yaml = f"""---
id: {citekey}
citekey: {citekey}
type: paper
doi: {p_data.get('doi', '')}
year: {p_data.get('year', '')}
projects:
{yaml_projects}
status: unread
updated: {date_str}
file: {p_data.get('file', '')}
---

# {p_data.get('title', 'Unknown Title')}

**Authors:** {', '.join(p_data.get('authors', []))}  
**Projects:** {', '.join(projects_list)}

## Abstract
{abstract_block}

## Notes
- 
"""
    with open(note_path, 'w', encoding='utf-8') as f:
        f.write(yaml)


def build_drive_pdf_index(drive_base):
    """Pre-indexes PDFs in papers/ for fast matching."""
    papers_dir = os.path.join(drive_base, "papers")
    if not os.path.exists(papers_dir):
        return papers_dir, {}, []
    pdf_files = [f for f in os.listdir(papers_dir) if f.endswith('.pdf')]
    norm_list = [(re.sub(r'[^a-z0-9]', '', f.lower()), f) for f in pdf_files]
    return papers_dir, set(pdf_files), norm_list


def find_pdf_in_drive(drive_base, item, citekey, drive_index=None):
    # 1. Check explicit attachments if available
    for att in item.get('attachments', []):
        path = att.get('path', att.get('url', ''))
        if path and path.endswith('.pdf') and os.path.exists(path):
            return path, f"papers/{os.path.basename(path)}"

    if drive_index is not None:
        papers_dir, pdf_set, norm_list = drive_index
    else:
        papers_dir, pdf_set, norm_list = build_drive_pdf_index(drive_base)

    if not pdf_set:
        return "", ""

    # 2. Check if citekey.pdf exists
    if f"{citekey}.pdf" in pdf_set:
        return os.path.join(papers_dir, f"{citekey}.pdf"), f"papers/{citekey}.pdf"

    # 3. Match against files in papers/ using title and author/year
    title = item.get('title', '').strip()
    norm_title = re.sub(r'[^a-z0-9]', '', title.lower()) if title else ""

    first_author = ""
    authors = item.get('author', [])
    if authors and isinstance(authors[0], dict):
        if 'family' in authors[0]:
            first_author = authors[0]['family'].lower()
        elif 'literal' in authors[0]:
            first_author = authors[0]['literal'].split()[-1].lower()

    year = ""
    if 'issued' in item and 'date-parts' in item['issued']:
        try:
            year = str(item['issued']['date-parts'][0][0])
        except (IndexError, TypeError):
            pass

    for norm_fname, fname in norm_list:
        if len(norm_title) > 10 and (norm_title[:35] in norm_fname or norm_title in norm_fname):
            return os.path.join(papers_dir, fname), f"papers/{fname}"
        if first_author and year and (first_author in norm_fname and year in norm_fname):
            if len(norm_title) > 8 and norm_title[:15] in norm_fname:
                return os.path.join(papers_dir, fname), f"papers/{fname}"

    return "", ""


def save_manifest_and_parquet(manifest, manifest_path):
    real_manifest_path = os.path.realpath(manifest_path)
    temp_json = real_manifest_path + '.tmp'
    with open(temp_json, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    os.replace(temp_json, real_manifest_path)

    try:
        from scripts.embed_library import update_embeddings
        update_embeddings(manifest_path=manifest_path)
    except Exception as e:
        print(f"Warning: Could not update SPECTER2 embeddings in parquet ({e}); falling back to plain parquet.")
        real_parquet_path = os.path.realpath(manifest_path.replace('.json', '.parquet'))
        try:
            import pandas as pd
            df = pd.DataFrame(manifest)
            df['year'] = pd.to_numeric(df['year'], errors='coerce').astype('Int64')
            df.to_parquet(real_parquet_path)
        except Exception as e2:
            print(f"Warning: Could not save parquet: {e2}")


def reclassify_library(manifest_path, canonical_path, notes_dir, drive_base):
    """Re-classifies all papers in manifest using Title + Zotero Abstract + PDF text, and updates notes."""
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    zotero_by_key = {}
    zotero_by_doi = {}
    if os.path.exists(canonical_path):
        with open(canonical_path, 'r', encoding='utf-8') as f:
            zotero_data = json.load(f)
        items = zotero_data if isinstance(zotero_data, list) else zotero_data.get('items', [])
        for it in items:
            ck = it.get('id')
            if ck:
                zotero_by_key[ck] = it
            doi = it.get('DOI', '').strip().lower().replace('https://doi.org/', '')
            if doi:
                zotero_by_doi[doi] = it

    drive_index = build_drive_pdf_index(drive_base)

    def process_entry(p):
        key = p.get('citekey') or p.get('id')
        doi = (p.get('doi') or '').strip().lower().replace('https://doi.org/', '')
        z_item = zotero_by_key.get(key) or (zotero_by_doi.get(doi) if doi else None) or {}

        rel_file = p.get('file', '')
        if not rel_file and z_item:
            _, rel_file = find_pdf_in_drive(drive_base, z_item, key, drive_index=drive_index)
            if rel_file:
                p['file'] = rel_file

        pdf_abs_path = os.path.join(drive_base, rel_file) if rel_file else ""
        pdf_text = ""
        if pdf_abs_path and os.path.exists(pdf_abs_path):
            pdf_text = extract_text_from_pdf(pdf_abs_path)

        z_abstract = clean_abstract(z_item.get('abstract', ''))
        if not z_abstract and pdf_text:
            z_abstract = extract_abstract_fallback(pdf_text)

        title = p.get('title', '') or z_item.get('title', '')
        new_projects = classify_text(text=pdf_text, title=title, abstract=z_abstract)
        p['projects'] = new_projects

        create_note_stub(key, p, notes_dir, abstract=z_abstract, overwrite=True)
        return new_projects

    print(f"Re-classifying {len(manifest)} library entries using updated taxonomy...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(process_entry, manifest))

    save_manifest_and_parquet(manifest, manifest_path)
    print("Re-classification complete! Updated papers_index.json, papers_index.parquet, and notes/.")


def run_bridge(manifest_path, canonical_path, notes_dir, drive_base):
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    manifest_by_key = {p.get('citekey', p.get('id')): p for p in manifest}

    with open(canonical_path, 'r', encoding='utf-8') as f:
        zotero_data = json.load(f)

    items = zotero_data if isinstance(zotero_data, list) else zotero_data.get('items', [])
    drive_index = build_drive_pdf_index(drive_base)

    new_items_count = 0
    updated_items_count = 0

    for item in items:
        citekey = item.get('id')
        if not citekey:
            continue

        z_abstract = clean_abstract(item.get('abstract', ''))
        title = item.get('title', '')

        # If paper already in manifest, check if it needs file linking / taxonomy update
        if citekey in manifest_by_key:
            existing = manifest_by_key[citekey]
            if not existing.get('file'):
                pdf_abs_path, rel_file = find_pdf_in_drive(drive_base, item, citekey, drive_index=drive_index)
                if rel_file:
                    existing['file'] = rel_file
                    text = extract_text_from_pdf(pdf_abs_path)
                    if not z_abstract and text:
                        z_abstract = extract_abstract_fallback(text)
                    existing['projects'] = classify_text(text=text, title=title, abstract=z_abstract)
                    updated_items_count += 1
                    create_note_stub(citekey, existing, notes_dir, abstract=z_abstract, overwrite=True)
            continue

        # 1. Get the PDF file path
        pdf_abs_path, rel_file = find_pdf_in_drive(drive_base, item, citekey, drive_index=drive_index)
        if not rel_file:
            continue

        print(f"New item detected from Zotero: {citekey} -> {rel_file}")

        # 2. Extract text and classify (using Title + Zotero Abstract + PDF pages)
        text = ""
        if pdf_abs_path and os.path.exists(pdf_abs_path):
            text = extract_text_from_pdf(pdf_abs_path)

        if not z_abstract and text:
            z_abstract = extract_abstract_fallback(text)

        projects = classify_text(text=text, title=title, abstract=z_abstract)

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
        year = None
        if 'issued' in item and 'date-parts' in item['issued']:
            try:
                y = item['issued']['date-parts'][0][0]
                if y:
                    year = int(y)
            except (IndexError, TypeError, ValueError):
                pass

        p_data = {
            "id": citekey,
            "citekey": citekey,
            "file": rel_file,
            "title": title,
            "authors": authors,
            "year": year if year is not None else "",
            "doi": doi,
            "projects": projects
        }

        # 3. Add to manifest
        manifest.append(p_data)
        manifest_by_key[citekey] = p_data

        # 4. Create Note Stub (with immediate abstract)
        create_note_stub(citekey, p_data, notes_dir, abstract=z_abstract, overwrite=True)
        new_items_count += 1

    if new_items_count > 0 or updated_items_count > 0:
        save_manifest_and_parquet(manifest, manifest_path)
        print(f"Successfully bridged {new_items_count} new papers and updated {updated_items_count} existing papers!")
    else:
        print("No new items found in Zotero.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zotero-to-AI Bridge & Taxonomy Classifier")
    parser.add_argument(
        "--reclassify",
        action="store_true",
        help="Re-classify all papers in the manifest and update notes with Zotero abstracts and new project tags."
    )
    args = parser.parse_args()

    MANIFEST = "papers_index.json"
    CANONICAL = "references/canonical_library.json"
    NOTES_DIR = "notes"
    DRIVE_BASE = "/path/to/your/zotero/papers"

    os.makedirs(NOTES_DIR, exist_ok=True)

    if args.reclassify:
        reclassify_library(MANIFEST, CANONICAL, NOTES_DIR, DRIVE_BASE)
    else:
        if not os.path.exists(CANONICAL):
            print(f"Error: {CANONICAL} not found.")
            sys.exit(1)
        run_bridge(MANIFEST, CANONICAL, NOTES_DIR, DRIVE_BASE)
