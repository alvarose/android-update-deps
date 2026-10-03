#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Aggregate the gradle-versions-plugin (ben-manes) report(s) for this skill.

Reads <repo>/build/dependencyUpdates/report.json (the merged report written by
ben-manes 0.55+, including when injected with scripts/versions.init.gradle.kts)
plus any per-project **/build/dependencyUpdates/report.json written by older
plugin versions.

It deduplicates outdated entries by group:name, labels AGP-internal tooling and
Kotlin toolchain artifacts so they are not mistaken for catalog updates, lists
step-down candidates (latest patch / minor) for the "highest version that still
compiles" fallback, lists unresolved JitPack (com.github.*) libraries to check by
hand, and reads the Gradle wrapper update from the report's `gradle` section.

Usage:
    python3 scripts/aggregate-updates.py [repo-path] [--json]

Exit codes: 0 report(s) read; 1 no report found (run the detection task first).
No external dependencies.
"""
import argparse
import json
import sys
from pathlib import Path

# Artifacts that AGP / the Kotlin toolchain add to internal configurations.
# They show up in the report but are not entries of libs.versions.toml: they
# move when AGP or Kotlin move, never on their own.
AGP_INTERNAL_GROUPS = ("com.android.tools.utp", "com.android.tools.lint")
AGP_INTERNAL_COORDS = {"com.android.tools.build:aapt2", "com.android.tools.build:aapt2-proto"}
# The Kotlin plugin contributes the stdlib/reflect/test artifacts at its own
# version; even when declared in the catalog they move with the `kotlin` ref.
KOTLIN_TOOLCHAIN_PREFIXES = (
    "org.jetbrains.kotlin:kotlin-compiler",
    "org.jetbrains.kotlin:kotlin-build-tools",
    "org.jetbrains.kotlin:kotlin-scripting",
    "org.jetbrains.kotlin:kotlin-daemon",
    "org.jetbrains.kotlin:kotlin-stdlib",
    "org.jetbrains.kotlin:kotlin-reflect",
    "org.jetbrains.kotlin:kotlin-test",
)


def find_reports(root: Path):
    """Root report first, then per-project ones. With ben-manes 0.55+ the root
    report is the merged one (it already covers every project); older versions
    write one report per project, so all of them are needed. Entries are
    deduplicated by group:name keeping the first seen, i.e. the root's."""
    merged = root / "build" / "dependencyUpdates" / "report.json"
    others = sorted(
        p for p in root.glob("**/build/dependencyUpdates/report.json")
        if "partials" not in p.parts and p != merged
    )
    return ([merged] if merged.is_file() else []) + others


def best_available(available):
    """The accepted newer version. Which field holds it depends on the task's
    `revision` (milestone by default). `preRelease` is deliberately ignored."""
    if not isinstance(available, dict):
        return None
    for key in ("release", "milestone", "integration"):
        if available.get(key):
            return available[key]
    return None


def category(coord: str) -> str:
    group = coord.split(":", 1)[0]
    if group.startswith(AGP_INTERNAL_GROUPS) or coord in AGP_INTERNAL_COORDS:
        return "agp-internal"
    if coord.startswith(KOTLIN_TOOLCHAIN_PREFIXES) or (
        group == "org.jetbrains.kotlin" and coord.endswith("-embeddable")
    ):
        return "kotlin-toolchain"
    if coord.endswith(".gradle.plugin"):
        return "plugin"
    return "library"


def gradle_update(data):
    """(running, current) when the `gradle` section reports a stable update.
    Present only when the task checks Gradle (gradleReleaseChannel / rejectPreReleases)."""
    g = data.get("gradle") or {}
    current = g.get("current") or {}
    running = g.get("running") or {}
    if current.get("isUpdateAvailable") and current.get("version"):
        return {"current": running.get("version") or "?", "available": current["version"]}
    return None


def collect(reports):
    outdated, jitpack, unresolved, skipped = {}, {}, {}, []
    gradle = None
    for path in reports:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: could not read {path}: {exc}", file=sys.stderr)
            continue
        for dep in (data.get("outdated") or {}).get("dependencies", []):
            coord = f"{dep.get('group')}:{dep.get('name')}"
            avail = dep.get("available") or {}
            target = best_available(avail)
            if not target or coord in outdated:
                continue
            outdated[coord] = {
                "coordinate": coord,
                "current": dep.get("version"),
                "latest": target,
                "patch": avail.get("patch"),
                "minor": avail.get("minor"),
                "category": category(coord),
            }
        for dep in (data.get("unresolved") or {}).get("dependencies", []):
            group = dep.get("group") or ""
            coord = f"{group}:{dep.get('name')}"
            entry = {"coordinate": coord, "current": dep.get("version") or "?"}
            if group.startswith("com.github."):
                jitpack.setdefault(coord, entry)
            else:
                entry["reason"] = (dep.get("reason") or "").splitlines()[0] if dep.get("reason") else ""
                unresolved.setdefault(coord, entry)
        sk = data.get("skipped")
        if isinstance(sk, dict):
            skipped.extend(sk.get("configurations") or [])
        if gradle is None:
            gradle = gradle_update(data)
    return {
        "gradle": gradle,
        "outdated": sorted(outdated.values(), key=lambda e: (e["category"], e["coordinate"])),
        "jitpack": sorted(jitpack.values(), key=lambda e: e["coordinate"]),
        "unresolved": sorted(unresolved.values(), key=lambda e: e["coordinate"]),
        "skipped": skipped,
    }


def print_text(result, reports):
    print(f"Reports read: {len(reports)}")
    g = result["gradle"]
    print("\n== Gradle wrapper ==")
    print(f"  {g['current']} -> {g['available']}  (own item, own confirmation)" if g
          else "  up to date, or the task does not check Gradle")

    titles = {
        "plugin": "Gradle plugins ([plugins] in the catalog)",
        "library": "Libraries",
        "kotlin-toolchain": "Kotlin toolchain (moves with the Kotlin/AGP decision, not on its own)",
        "agp-internal": "AGP-internal tooling (noise: moves with AGP, ignore)",
    }
    for cat in ("plugin", "library", "kotlin-toolchain", "agp-internal"):
        items = [e for e in result["outdated"] if e["category"] == cat]
        if not items:
            continue
        print(f"\n== {titles[cat]}: {len(items)} ==")
        for e in items:
            steps = [f"{k} {e[k]}" for k in ("patch", "minor") if e[k] and e[k] != e["latest"]]
            extra = f"  (step-down: {', '.join(steps)})" if steps else ""
            print(f"  {e['coordinate']}: {e['current']} -> {e['latest']}{extra}")

    print(f"\n== JitPack resolved by the build (check by hand - references/reference.md#jitpack): {len(result['jitpack'])} ==")
    print("  (unused catalog entries never appear here: take the com.github.* list from the catalog)")
    for e in result["jitpack"]:
        print(f"  {e['coordinate']} (current: {e['current']})")
    if result["unresolved"]:
        print(f"\n== Other unresolved: {len(result['unresolved'])} ==")
        for e in result["unresolved"]:
            print(f"  {e['coordinate']} ({e['current']}): {e['reason']}")
    if result["skipped"]:
        print(f"\n== Skipped configurations: {len(result['skipped'])} ==")
        for s in result["skipped"]:
            print(f"  {s}")
    print("\nNext: map plugins/libraries to their keys in gradle/libs.versions.toml,")
    print("group coupled blocks and BOM-governed artifacts - or let scripts/plan.py do it (SKILL step 2).")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Aggregate ben-manes dependencyUpdates report(s) for android-update-deps.")
    parser.add_argument("repo", nargs="?", default=".", help="Android project root (default: .)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    root = Path(args.repo).resolve()
    reports = find_reports(root)
    if not reports:
        print(f"No dependencyUpdates report.json found under {root}.", file=sys.stderr)
        print("Run the detection task first (see SKILL step 1).", file=sys.stderr)
        return 1

    result = collect(reports)
    if args.json:
        json.dump({"reports": [str(p) for p in reports], **result}, sys.stdout, indent=2)
        print()
    else:
        print_text(result, reports)
    return 0


if __name__ == "__main__":
    sys.exit(main())
