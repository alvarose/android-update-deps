#!/usr/bin/env python3
"""Aggregate and deduplicate the gradle-versions-plugin report.json files.

Walks <repo>/**/build/dependencyUpdates/report.json, collects the 'outdated'
dependencies deduplicated by group:name, and separately lists the 'unresolved'
JitPack libs (com.github.*) that must be reviewed by hand (see reference.md#jitpack).

Usage:
    python scripts/aggregate-updates.py [repo-path]

With no argument it uses the current directory. No external dependencies.
"""
import json
import sys
from pathlib import Path


def find_reports(root: Path):
    return sorted(root.glob("**/build/dependencyUpdates/report.json"))


def best_available(available):
    if not isinstance(available, dict):
        return None
    for key in ("release", "milestone", "integration"):
        val = available.get(key)
        if val:
            return val
    return None


def collect(reports):
    outdated = {}   # group:name -> (current, target)
    jitpack = {}    # group:name -> current
    for path in reports:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  [warning] could not read {path}: {exc}", file=sys.stderr)
            continue
        for dep in data.get("outdated", {}).get("dependencies", []):
            key = f"{dep.get('group')}:{dep.get('name')}"
            target = best_available(dep.get("available"))
            if target:
                outdated[key] = (dep.get("version"), target)
        for dep in data.get("unresolved", {}).get("dependencies", []):
            group = dep.get("group", "")
            if group.startswith("com.github."):
                key = f"{group}:{dep.get('name')}"
                jitpack[key] = dep.get("version", "?")
    return outdated, jitpack


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    reports = find_reports(root)
    if not reports:
        print(f"No report.json found under {root}.")
        print("Run first: ./gradlew dependencyUpdates --no-parallel")
        return 1

    print(f"Reports found: {len(reports)}")
    outdated, jitpack = collect(reports)

    print(f"\n== Outdated (deduped by group:name): {len(outdated)} ==")
    for key in sorted(outdated):
        current, target = outdated[key]
        print(f"  {key}: {current} -> {target}")

    print(f"\n== JitPack (unresolved, review by hand - reference.md#jitpack): {len(jitpack)} ==")
    for key in sorted(jitpack):
        print(f"  {key} (current: {jitpack[key]})")
    if not jitpack:
        print("  (none)")

    print("\nNext: map each key to its entry in gradle/libs.versions.toml,")
    print("group the coupled blocks, and prepare the proposal (SKILL step 5).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
