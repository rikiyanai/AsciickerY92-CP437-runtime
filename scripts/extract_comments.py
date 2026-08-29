import glob
import os

def extract_cpp_comments(output_filename="COMMENTS.md"):
    """
    Scans all .cpp files in the current directory and its subdirectories,
    extracts inline comments (starting with //), and writes them to a Markdown file.
    """
    cpp_files = glob.glob("**/*.cpp", recursive=True)
    
    if not cpp_files:
        print("No .cpp files found.")
        return

    with open(output_filename, "w", encoding="utf-8") as outfile:
        outfile.write("# C++ Inline Comments\n\n")
        outfile.write("Extracted from .cpp files in the project.\n\n")
        
        for cpp_file in sorted(cpp_files):
            relative_path = os.path.relpath(cpp_file)
            outfile.write(f"## File: `{relative_path}`\n\n")
            
            try:
                with open(cpp_file, "r", encoding="utf-8", errors="ignore") as infile:
                    for line_num, line in enumerate(infile, 1):
                        line = line.strip()
                        if line.startswith("//"):
                            comment_content = line[2:].strip()
                            if comment_content: # Only write non-empty comments
                                outfile.write(f"- Line {line_num}: {comment_content}\n")
                        elif "//" in line:
                            # Handle comments after code
                            code_part, comment_part = line.split("//", 1)
                            comment_content = comment_part.strip()
                            if comment_content:
                                outfile.write(f"- Line {line_num} (after code `{code_part.strip()}`): {comment_content}\n")
                outfile.write("\n") # Add a newline after each file's comments
            except Exception as e:
                outfile.write(f"**Error reading file `{relative_path}`: {e}**\n\n")

    print(f"All inline comments extracted to '{output_filename}'")

if __name__ == "__main__":
    extract_cpp_comments()
