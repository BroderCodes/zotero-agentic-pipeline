import json
import os
import re

def update_yaml_frontmatter(content, new_citekey):
    """Safely updates id and citekey in YAML frontmatter without touching body."""
    # Split content on the first two '---' markers
    parts = re.split(r'^(?:---|\.\.\.)\r?\n', content, maxsplit=2, flags=re.MULTILINE)
    
    # parts[0] is everything before the first '---' (usually empty string)
    # parts[1] is the frontmatter
    # parts[2] is the rest of the body
    if len(parts) >= 3:
        frontmatter = parts[1]
        body = parts[2]
        
        # Update id: ...
        if re.search(r'^id:\s*.*$', frontmatter, flags=re.MULTILINE):
            frontmatter = re.sub(r'^id:\s*.*$', f"id: {new_citekey}", frontmatter, flags=re.MULTILINE)
        else:
            frontmatter = f"id: {new_citekey}\n" + frontmatter
            
        # Add or update citekey: ...
        if re.search(r'^citekey:\s*.*$', frontmatter, flags=re.MULTILINE):
            frontmatter = re.sub(r'^citekey:\s*.*$', f"citekey: {new_citekey}", frontmatter, flags=re.MULTILINE)
        else:
            frontmatter = f"citekey: {new_citekey}\n" + frontmatter
            
        return f"---\n{frontmatter}---\n{body}"
    else:
        # If no valid frontmatter exists, don't modify
        return content

def sync_stubs(manifest_path, notes_dir):
    with open(manifest_path, 'r', encoding='utf-8') as f:
        papers = json.load(f)
        
    renamed_count = 0
    updated_manifest = False
    
    for p in papers:
        old_id = p.get('id')
        new_citekey = p.get('citekey')
        
        if new_citekey and new_citekey != old_id:
            old_path = os.path.join(notes_dir, f"{old_id}.md")
            new_path = os.path.join(notes_dir, f"{new_citekey}.md")
            
            if os.path.exists(old_path):
                # 1. Update markdown content
                with open(old_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                new_content = update_yaml_frontmatter(content, new_citekey)
                
                with open(old_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                
                # 2. Rename the file
                # If new_path already exists (collision), just replace or warn.
                # Assuming safe to replace for now since BBT handles collisions.
                os.replace(old_path, new_path)
                renamed_count += 1
                
                # 3. Update the id in manifest to keep it in sync for future runs
                p['id'] = new_citekey
                updated_manifest = True
            elif os.path.exists(new_path):
                # Already renamed in a previous run, just update manifest ID
                p['id'] = new_citekey
                updated_manifest = True

    print(f"Synchronized notes: {renamed_count} files renamed and updated.")
    
    if updated_manifest:
        # Write back the updated manifest so 'id' matches the new note filenames
        real_manifest_path = os.path.realpath(manifest_path)
        temp_json = real_manifest_path + '.tmp'
        with open(temp_json, 'w', encoding='utf-8') as f:
            json.dump(papers, f, indent=2)
        os.replace(temp_json, real_manifest_path)
        print("Updated manifest IDs to match new citekeys.")
        
        # If parquet exists, update it too
        real_parquet_path = os.path.realpath(manifest_path.replace('.json', '.parquet'))
        if os.path.exists(real_parquet_path):
            import pandas as pd
            df = pd.DataFrame(papers)
            df.to_parquet(real_parquet_path)

if __name__ == "__main__":
    MANIFEST = "papers_index.json"
    NOTES_DIR = "notes"
    
    sync_stubs(MANIFEST, NOTES_DIR)
