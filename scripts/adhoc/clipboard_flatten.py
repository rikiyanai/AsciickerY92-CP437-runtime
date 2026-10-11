#!/usr/bin/env python3
"""Flatten wrapped clipboard text into a single pastable line.

Original proposal: BFC-bbf82262b8d2
Source: claude session 95801774-7944-4f1b-86ea-38c425f327ee, FL-1443
Generalized: added --dry-run flag, stdin support, cross-platform detection

Usage:
    pbpaste | python3 scripts/adhoc/clipboard_flatten.py
    python3 scripts/adhoc/clipboard_flatten.py --dry-run < wrapped.txt
"""
import argparse
import subprocess
import sys
import platform


def flatten(text):
    lines = text.split('\n')
    if len(lines) <= 1:
        return text, len(lines)

    def in_quoted_string(s):
        in_q = False
        q_char = None
        i = 0
        while i < len(s):
            c = s[i]
            if c == '\\' and i + 1 < len(s):
                i += 2
                continue
            if c in ('"', "'"):
                if in_q and c == q_char:
                    in_q = False
                    q_char = None
                elif not in_q:
                    in_q = True
                    q_char = c
            i += 1
        return in_q

    result = lines[0].rstrip()
    for line in lines[1:]:
        stripped = line.lstrip()
        if not stripped:
            continue
        if not result:
            result = stripped
            continue

        is_indented = line[0] in (' ', '\t') if line else False
        last = result[-1]
        first = stripped[0]
        in_quote = in_quoted_string(result)

        if is_indented:
            if last == '-' and first.islower():
                result += stripped
            elif in_quote:
                result += ' ' + stripped
            elif (last.isalnum() or last == '_') and (first.islower() or first == '_' or first.isdigit()):
                last_token = result.rsplit(None, 1)[-1] if result else ''
                if last_token.startswith('--'):
                    result += ' ' + stripped
                else:
                    result += stripped
            else:
                result += ' ' + stripped
        else:
            result += ' ' + stripped

    return result, len(lines)


def get_clipboard_text():
    """Read from clipboard using platform-specific tools."""
    if platform.system() == 'Darwin':
        return subprocess.check_output(['pbpaste']).decode('utf-8').rstrip('\n')
    elif platform.system() == 'Linux':
        try:
            return subprocess.check_output(['xclip', '-selection', 'clipboard', '-o']).decode('utf-8').rstrip('\n')
        except (FileNotFoundError, subprocess.CalledProcessError):
            try:
                return subprocess.check_output(['xsel', '--clipboard', '--output']).decode('utf-8').rstrip('\n')
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass
    # Fallback: read from stdin
    return sys.stdin.read().rstrip('\n')


def set_clipboard_text(text):
    """Write to clipboard using platform-specific tools."""
    if platform.system() == 'Darwin':
        subprocess.run(['pbcopy'], input=text.encode('utf-8'))
    elif platform.system() == 'Linux':
        try:
            subprocess.run(['xclip', '-selection', 'clipboard'], input=text.encode('utf-8'))
            return
        except FileNotFoundError:
            try:
                subprocess.run(['xsel', '--clipboard', '--input'], input=text.encode('utf-8'))
                return
            except FileNotFoundError:
                pass
    # Fallback: print to stdout
    print(text)


def main():
    parser = argparse.ArgumentParser(description="Flatten wrapped clipboard text to one line")
    parser.add_argument("--dry-run", action="store_true", help="Print result instead of copying to clipboard")
    args = parser.parse_args()

    text = get_clipboard_text()
    flattened, n = flatten(text)

    if n <= 1:
        print("Already one line.")
        return

    if args.dry_run:
        print(flattened)
    else:
        set_clipboard_text(flattened)
        print(f"Flattened {n} lines -> 1")


if __name__ == '__main__':
    main()
