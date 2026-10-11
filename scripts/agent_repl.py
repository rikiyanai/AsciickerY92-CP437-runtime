#!/usr/bin/env python3
"""
Agent Ops REPL.

A continuous interactive shell for logging agent state.
Useful for:
1. Humans "bridging" external agent thoughts (e.g. from Codex) to the log.
2. Local debugging of the Agent Ops pipeline.
"""

import os
import sys
import subprocess
import shlex
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli_style import clear_screen as _clear_screen  # noqa: E402

# Command map
CMD_HELP = ["/help", "/h", "?", "/?"]
CMD_STATUS = ["/status", "/s"]
CMD_HYPOTHESIS = ["/hypothesis", "/hyp"]
CMD_EDIT = ["/edit", "/e"]
CMD_EXIT = ["/exit", "/quit", "/q"]

def run_hook(args):
    """Runs the unified agent hook via subprocess."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, "agent_hook.py")
    
    cmd = [sys.executable, script_path] + args
    subprocess.run(cmd)

def clear_screen():
    seq = _clear_screen()
    if seq:
        print(seq, end="")

def print_help():
    print("\n--- Agent Ops Shell ---")
    print("  <text>          : Log a generic thought.")
    print("  /hyp <text>     : Log a specific hypothesis (updates summary).")
    print("  /edit <file> <desc> : Log a code edit.")
    print("  /status         : Show current understanding.")
    print("  /exit           : Quit shell.")
    print("-----------------------\n")

def main():
    print("Welcome to Agent Ops. Type your thoughts below.")
    print("Type /help for commands.")
    
    while True:
        try:
            prompt = "\nAgent> "
            user_input = input(prompt).strip()
            
            if not user_input:
                continue
                
            parts = shlex.split(user_input)
            cmd = parts[0].lower()
            
            if cmd in CMD_EXIT:
                print("Exiting...")
                break
                
            elif cmd in CMD_HELP:
                print_help()
                
            elif cmd in CMD_STATUS:
                run_hook([]) # Runs default maintenance (track + link)
                # Cat CURRENT_UNDERSTANDING
                try:
                    with open("CURRENT_UNDERSTANDING.md", "r") as f:
                        print("\n" + f.read())
                except:
                    print("No status file found.")
                    
            elif cmd in CMD_HYPOTHESIS:
                if len(parts) < 2:
                    print("Usage: /hyp <hypothesis text>")
                    continue
                content = " ".join(parts[1:])
                run_hook(["--hypothesis", content])
                
            elif cmd in CMD_EDIT:
                if len(parts) < 3:
                    print("Usage: /edit <file> <description>")
                    continue
                file_path = parts[1]
                desc = " ".join(parts[2:])
                run_hook(["--edit", desc, "--file", file_path])
                
            else:
                # Default: Log as thought
                run_hook([user_input])
                
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
