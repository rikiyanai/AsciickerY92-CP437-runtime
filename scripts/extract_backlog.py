import re
import os

def extract_backlog_items(input_filename="COMMENTS.md", output_filename="Backlog.md"):
    if not os.path.exists(input_filename):
        print(f"Error: {input_filename} not found.")
        return

    backlog = {}
    current_file = None

    # Regex for TODO, FIXME, XXX
    todo_pattern = re.compile(r'\b(TODO|FIXME|XXX)\b', re.IGNORECASE)
    
    # Regex for potential commented-out code
    # Matches common C++ constructs preceded by //
    code_pattern = re.compile(r'^(if|else|for|while|return|void|int|float|double|char|bool|class|struct|switch|case|break|continue|#include|#define|#if|#endif|static|virtual|override|public:|private:|protected:|[a-zA-Z_]\w*\s*\([^;]*\);)', re.IGNORECASE)

    with open(input_filename, "r", encoding="utf-8") as infile:
        for line in infile:
            line = line.strip()
            if line.startswith("## File:"):
                current_file = line.replace("## File: ", "").strip("` ")
                backlog[current_file] = []
            elif line.startswith("- Line"):
                # Format: - Line 123: Comment text
                # OR: - Line 123 (after code `...`): Comment text
                match = re.match(r'- Line (\d+)(?: \(after code `.*`\))?: (.*)', line)
                if match:
                    line_num = match.group(1)
                    comment_text = match.group(2).strip()
                    
                    is_todo = todo_pattern.search(comment_text)
                    is_code = code_pattern.search(comment_text)
                    
                    if is_todo or is_code:
                        category = "TODO/FIXME" if is_todo else "Commented-out Code"
                        backlog[current_file].append({
                            "line": line_num,
                            "category": category,
                            "text": comment_text
                        })

    with open(output_filename, "w", encoding="utf-8") as outfile:
        outfile.write("# Project Backlog\n\n")
        outfile.write("Extracted from inline comments in .cpp files.\n\n")
        
        has_items = False
        for file_path, items in sorted(backlog.items()):
            if items:
                has_items = True
                outfile.write(f"## {file_path}\n\n")
                for item in items:
                    outfile.write(f"- [ ] **{item['category']}** (Line {item['line']}): {item['text']}\n")
                outfile.write("\n")
        
        if not has_items:
            outfile.write("No backlog items found.\n")

    print(f"Backlog items extracted to '{output_filename}'")

if __name__ == "__main__":
    extract_backlog_items()
