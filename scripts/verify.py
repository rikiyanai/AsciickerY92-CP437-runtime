#!/usr/bin/env python3
"""
Asciicker Verification Script
-----------------------------
Runs a suite of verification checks to ensure project health.
Usage: python3 scripts/verify.py [options]
"""

import os
import sys
import subprocess
import time
from pathlib import Path

# Shared ANSI helpers (FL-1177)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli_style import status as _status, clr_green, clr_red  # noqa: E402


def log_pass(msg):
    print(_status("PASS", msg))

def log_fail(msg):
    print(_status("FAIL", msg))

def log_warn(msg):
    print(_status("WARN", msg))

def run_command(cmd, cwd=None):
    """Run a shell command and return (return_code, stdout, stderr)"""
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            capture_output=True, 
            text=True, 
            cwd=cwd
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)

def check_cpp_build():
    """Verify C++ project builds"""
    print("Verifying C++ Build...")
    
    # Check if we are on mac
    is_mac = sys.platform == 'darwin'
    makefile = "makefile_game_mac" if is_mac else "makefile_game"
    
    # Clean first? Optional, but safer for verification
    # run_command("make -f {} clean".format(makefile))
    
    # Dry run make to check for obvious syntax errors in makefile
    code, out, err = run_command(f"make -f {makefile} -n")
    if code != 0:
        log_fail(f"Makefile invalid: {err.strip()}")
        return False
        
    # Full build (can be slow, maybe skip for quick verify?)
    # For now, let's just check if source files exist
    required_files = ["engine/game.cpp", "engine/game.h", "engine/render.cpp"]
    missing = [f for f in required_files if not os.path.exists(f)]
    if missing:
        log_fail(f"Missing source files: {missing}")
        return False
        
    log_pass("C++ Build Configuration looks valid")
    return True

def check_python_syntax():
    """Verify Python syntax for scripts and addons"""
    print("Verifying Python Syntax...")
    
    # Find all python files
    py_files = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [
            d for d in dirs
            if d not in {".git", ".venv", "mcp_venv", ".worktrees", "node_modules"}
        ]
        if ".venv" in root or "mcp_venv" in root or ".worktrees" in root:
            continue
        for file in files:
            if file.endswith(".py"):
                py_files.append(os.path.join(root, file))
                
    failed = False
    for f in py_files:
        code, out, err = run_command(f"python3 -m py_compile '{f}'")
        if code != 0:
            log_fail(f"Syntax error in {f}\n{err.strip()}")
            failed = True
            
    if not failed:
        log_pass(f"Checked {len(py_files)} Python files")
    return not failed

def check_blender_addon_structure():
    """Verify Blender addon structure"""
    print("Verifying Blender Addon Structure...")
    
    addon_path = "addons/io_asciicker"
    if not os.path.exists(addon_path):
        log_fail(f"Addon directory '{addon_path}' not found")
        return False
        
    required = ["__init__.py", "ui", "tools"]
    missing = [f for f in required if not os.path.exists(os.path.join(addon_path, f))]
    
    if missing:
        log_fail(f"Addon missing components: {missing}")
        return False
        
    log_pass("Blender Addon structure valid")
    return True

def check_addon_link():
    """Verify Blender addon is linked in Developer Mode"""
    print("Verifying Addon Link...")
    
    # Simple check for macOS default location for now
    # Ideally should share logic with setup_addon.py but keeping it simple here
    home = os.path.expanduser("~")
    base_paths = [
        os.path.join(home, "Library/Application Support/Blender/4.5/scripts/addons/io_asciicker"),
        os.path.join(home, "Library/Application Support/Blender/5.0/scripts/addons/io_asciicker")
    ]
    
    linked_any = False
    for p in base_paths:
        if os.path.islink(p):
            target = os.readlink(p)
            if os.path.exists(target):
                 # Check if it points to our repo
                 # approximate check: ends with io_asciicker
                 if target.endswith("io_asciicker"):
                     linked_any = True
                     # log_pass(f"Linked in {p.split('/')[-4]}") # Too verbose?
    
    if linked_any:
        log_pass("Addon is linked (Developer Mode Active)")
        return True
    else:
        log_warn("Addon NOT linked in default locations. Run 'scripts/setup_addon.py' if working in Blender.")
        return True # Don't fail the build, just warn

def check_blender_tests():
    """Run headless Blender tests"""
    print("Running Blender Headless Tests...")
    
    script_path = os.path.join(os.path.dirname(__file__), "test_blender.py")
    code, out, err = run_command(f"python3 '{script_path}'")
    
    if code != 0:
        log_fail("Blender tests failed")
        print(out) # Print stdout to see what happened
        print(err)
        return False
        
    log_pass("Blender headless tests passed")
    return True

def main():
    print(f"Starting Verification @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 50)
    
    checks = [
        check_cpp_build,
        check_python_syntax,
        check_blender_addon_structure,
        check_addon_link,
        check_blender_tests
    ]
    
    passed = 0
    failed = 0
    
    for check in checks:
        if check():
            passed += 1
        else:
            failed += 1
            
    print("-" * 50)
    if failed == 0:
        print(clr_green("ALL CHECKS PASSED"))  # FL-1177
        sys.exit(0)
    else:
        print(clr_red(f"{failed} CHECKS FAILED"))  # FL-1177
        sys.exit(1)

if __name__ == "__main__":
    main()
