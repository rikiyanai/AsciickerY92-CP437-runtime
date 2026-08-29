#!/usr/bin/env python3
"""Check CLI propagation rules coverage: find argparse scripts not in help_check.

Original proposal: BFC-169663197dca
Source: claude session b8ccc721-1b98-4166-9ee7-c70321a072f9, unlinked
Generalized: hardcoded worktree path → --repo CLI argument

Usage:
    python3 scripts/adhoc/propagation_coverage_check.py [--repo .]
"""
import argparse
import json
from pathlib import Path


def load_propagation_rules(repo_root: Path) -> dict:
    rules_path = repo_root / "scripts/hooks/propagation_rules.json"
    with open(rules_path) as f:
        return json.load(f)


def find_argparse_scripts(repo_root: Path) -> set:
    argparse_files = set()
    for py_file in repo_root.glob("scripts/**/*.py"):
        try:
            content = py_file.read_text()
            if "import argparse" in content or "from argparse" in content or "ArgumentParser" in content:
                rel_path = "scripts/" + str(py_file.relative_to(repo_root / "scripts"))
                argparse_files.add(rel_path)
        except Exception:
            pass
    return argparse_files


def main():
    parser = argparse.ArgumentParser(description="Check propagation rules coverage of argparse scripts")
    parser.add_argument("--repo", default=".", help="Repo root path (default: cwd)")
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    rules_data = load_propagation_rules(repo_root)

    # Extract all help_check requirements and CLI-NEW-CMD triggers
    cli_new_cmd_rule = None
    all_help_check_scripts = set()

    for rule in rules_data.get("rules", []):
        if rule.get("id") == "CLI-NEW-CMD":
            cli_new_cmd_rule = rule
        all_help_check_scripts.update(rule.get("help_check", []))

    all_help_check_scripts.discard("_trigger_")

    print("=" * 70)
    print("PROPAGATION RULES ANALYSIS")
    print("=" * 70)

    if cli_new_cmd_rule:
        print("\nCLI-NEW-CMD Trigger Scripts (baseline):")
        for script in sorted(cli_new_cmd_rule.get("triggers", [])):
            print(f"  {script}")

    print(f"\nTotal help_check entries from ALL rules: {len(all_help_check_scripts)}")

    argparse_files = find_argparse_scripts(repo_root)
    print(f"\nTotal Python files with argparse: {len(argparse_files)}")

    # Coverage analysis
    uncovered = argparse_files - all_help_check_scripts
    covered_not_found = all_help_check_scripts - argparse_files

    print("\n" + "=" * 70)
    print("COVERAGE ANALYSIS")
    print("=" * 70)

    print(f"\nUNCOVERED scripts ({len(uncovered)} total):")
    for script in sorted(uncovered):
        print(f"  {script}")

    if covered_not_found:
        print(f"\nCovered in rules but NOT FOUND in repo ({len(covered_not_found)}):")
        for script in sorted(covered_not_found):
            print(f"  {script}")

    if cli_new_cmd_rule:
        cli_new_cmd_set = set(cli_new_cmd_rule.get("triggers", []))
        uncovered_cli = [s for s in cli_new_cmd_set if s not in argparse_files]
        if uncovered_cli:
            print(f"\nCLI-NEW-CMD triggers without argparse ({len(uncovered_cli)}):")
            for script in sorted(uncovered_cli):
                print(f"  {script}")

    print(f"\nCoverage: {len(argparse_files - uncovered)}/{len(argparse_files)} "
          f"({100 * len(argparse_files - uncovered) // max(1, len(argparse_files))}%)")


if __name__ == '__main__':
    main()
