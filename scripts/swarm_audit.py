import os
import re
import sys

def parse_backlog(backlog_path, target_file):
    """Parses Backlog.md for items related to the target file."""
    items = []
    target_base = os.path.basename(target_file)
    
    with open(backlog_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    in_section = False
    for line in lines:
        if line.startswith('## '):
            if target_base in line:
                in_section = True
            else:
                in_section = False
        
        if in_section and line.strip().startswith('- [ ]'):
            # Extract content line from backlog item
            # Format: - [ ] **Type** (Line N): Content
            match = re.match(r'- \[ \] \*\*.*?\*\* \(Line \d+\): (.*)', line.strip())
            if match:
                content = match.group(1).strip()
                # Remove common prefixes/suffixes if necessary
                items.append(content)
            else:
                 # Fallback for simpler format
                 parts = line.split('): ', 1)
                 if len(parts) > 1:
                     items.append(parts[1].strip())

    return items

def scan_paths(content, file_path):
    """Scans for path-like strings."""
    # Regex for potential paths: 
    # Starts with / or ./ or ../ or [drive]:/
    # OR contains slash and extension
    # Exclude simpler short strings to avoid false positives
    
    paths = []
    # Quote pattern: "..." or '...'
    quote_pattern = re.compile(r'(["\'])(.*?)\1')
    
    for match in quote_pattern.finditer(content):
        val = match.group(2)
        if len(val) < 3: continue
        
        # Heuristics for paths
        is_path = False
        if '/' in val or '\\' in val:
            if '.' in os.path.basename(val): # Has extension
                is_path = True
            if val.startswith('/') or val.startswith('./'):
                is_path = True
            if ':' in val and ('/' in val or '\\' in val): # Windows drive or URL
                is_path = True
        
        if is_path:
            # Filter out some common non-path noise if needed
            if val.count(' ') > 0 and not val.startswith('/'): # Sentences with slashes?
                pass 
            else:
                paths.append((match.start(), val))
                
    return paths

def audit_file(file_path, backlog_path, manifest_path):
    print(f"Auditing {file_path}...")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    content_str = "".join(lines)
    backlog_items = parse_backlog(backlog_path, file_path)
    
    new_lines = []
    modified = False
    
    # 1. Backlog Integration
    # We iterate lines and try to match backlog items
    # usage: map content -> list of items
    # naive approach: for each line, check if it matches any backlog item
    
    # Pre-process backlog items for easier matching (strip whitespace, etc)
    # But backlog items might be partial.
    
    # Better approach:
    # For each line in file, if it contains the backlog content (canonicalized), add TODO.
    
    final_lines = list(lines)
    
    for i, line in enumerate(final_lines):
        clean_line = line.strip()
        if not clean_line: continue
        
        for item in backlog_items:
            # Check if item content is in this line
            # Be careful of partial matches
            if item in clean_line:
                if "TODO: [Backlog Ref]" not in line:
                    # Insert TODO
                    # append to end of line
                    final_lines[i] = line.rstrip() + f" // TODO: [Backlog Ref] {item}\n"
                    modified = True
                    # Remove used item? Maybe. Duplicate items might exist.
    
    # 2. Path Scanning & Flagging
    # We iterate lines again (or do it in same pass? separate for clarity)
    
    path_modified = False
    
    for i, line in enumerate(final_lines):
        # Skip if already flagged
        if "TODO: Hardcoded Path" in line: continue
        
        # Simple line-based path scan
        paths_in_line = scan_paths(line, file_path)
        if paths_in_line:
             for _, p_str in paths_in_line:
                 with open(manifest_path, 'a', encoding='utf-8') as mf:
                     mf.write(f"| {os.path.basename(file_path)} | `{p_str}` | {i+1} | - |\n")
             
             # Insert TODO
             final_lines[i] = line.rstrip() + " // TODO: Hardcoded Path? Verify safety.\n"
             path_modified = True

    # 3. Write back if modified
    if modified or path_modified:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(final_lines)
        print(f"Updated {file_path} with backlog items/paths.")
    else:
        print(f"No changes for {file_path}.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python swarm_audit.py <file_path>")
        sys.exit(1)
        
    target = sys.argv[1]
    backlog = "Backlog.md"
    manifest = "docs/PATH_MANIFEST.md"
    
    audit_file(target, backlog, manifest)
