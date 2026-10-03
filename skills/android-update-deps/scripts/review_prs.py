#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Review the open dependency PRs that Dependabot or Renovate opened on an Android project.

For every bot PR it compares the base and head versions of the files the PR changes
(the version catalog, the Gradle wrapper, inline versions in build scripts) and runs
each bump through the same analysis as plan.py, judging the version the PR proposes:
hidden requirements, coupled blocks and toolchain items, vulnerabilities, 16 KB
alignment, a newer stable release. It adds what only a PR has: the CI result, merge
conflicts, files that aren't dependency files, an author that isn't a verified bot,
and other PRs touching the same entry. Each PR gets a verdict: merge candidate,
review first, or hold.

Read-only: it never comments, approves, merges or closes anything, and it doesn't
read PR descriptions (they carry third-party changelog text). Needs the GitHub CLI
(`gh`), authenticated.

Usage:
    python3 scripts/review_prs.py [repo] [-R owner/name] [--pr N ...] [--author LOGIN ...]
                                  [--kotlin X] [--compile-sdk N] [--json]

With a local checkout the project context (compileSdk, effective Kotlin, which entries
are used) comes from its working tree; without one (-R only) it comes from the default
branch through the GitHub API, without cloning.

Exit codes: 0 review produced (also when there are no bot PRs); 1 gh unavailable, not a
GitHub repository or no version catalog; 2 bad arguments.
"""
import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plan  # noqa: E402  (plan.py and risk.py sit next to this script)
import risk  # noqa: E402

BOT = re.compile(r"(?i)\b(app/)?(dependabot|renovate)(\[bot\]|-bot)?\b")
PR_FIELDS = ("number,title,url,author,isDraft,mergeable,headRefOid,baseRefOid,baseRefName,files,"
             "statusCheckRollup,createdAt")
INLINE = re.compile(r"[\"']([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+):([A-Za-z0-9_.+-]+)[\"']")
WRAPPER_URL = re.compile(r"distributionUrl=.*?gradle-([0-9][^-/]*(?:-(?!bin|all)[^-/]+)?)-(bin|all)\.zip")
FAILING = {"FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
CANCELLED = {"CANCELLED", "STALE"}
PENDING = {"", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING", "REQUESTED"}
MAX_REMOTE_FILES = 400


class GhError(RuntimeError):
    pass


def run_gh(args, cwd=None):
    try:
        done = subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        raise GhError("the GitHub CLI (gh) is not installed")
    if done.returncode != 0:
        raise GhError((done.stderr or done.stdout).strip() or f"gh {' '.join(args)} failed")
    return done.stdout


class GitHub:
    """The few read-only calls the review needs. `runner(args, cwd)` returns stdout."""

    def __init__(self, slug, runner=run_gh):
        self.slug, self.run = slug, runner

    def json(self, *args):
        return json.loads(self.run(list(args), None) or "null")

    def file(self, path, ref):
        try:
            return self.run(["api", "-H", "Accept: application/vnd.github.raw",
                             f"repos/{self.slug}/contents/{urllib.parse.quote(path)}?ref={ref}"], None)
        except GhError:
            return None  # absent at that ref

    def merge_base(self, base, head):
        """Where the PR branched off: diffing against the base branch's tip would also
        show what changed on the base branch since."""
        try:
            return self.json("api", f"repos/{self.slug}/compare/{base}...{head}")["merge_base_commit"]["sha"]
        except (GhError, KeyError, TypeError):
            return base

    def default_head(self):
        branch = self.json("api", f"repos/{self.slug}")["default_branch"]
        return self.json("api", f"repos/{self.slug}/commits/{urllib.parse.quote(branch)}")["sha"]

    def tree(self, ref):
        data = self.json("api", f"repos/{self.slug}/git/trees/{ref}?recursive=1")
        return [e["path"] for e in data.get("tree", []) if e.get("type") == "blob"], bool(data.get("truncated"))

    def prs(self, numbers=None):
        if numbers:
            return [self.json("pr", "view", str(n), "-R", self.slug, "--json", PR_FIELDS) for n in numbers]
        return self.json("pr", "list", "-R", self.slug, "--state", "open", "--limit", "100", "--json", PR_FIELDS)


def resolve_slug(repo, explicit, runner=run_gh):
    if explicit:
        return explicit
    try:
        return json.loads(runner(["repo", "view", "--json", "nameWithOwner"], str(repo)))["nameWithOwner"]
    except (GhError, ValueError, KeyError) as e:
        raise GhError(f"{repo} is not a GitHub repository gh can see ({e}); pass -R owner/name")


# ---------------------------------------------------------------- project context

def remote_build_texts(gh, ref, warnings):
    """plan.build_texts for a ref on GitHub: the same files, fetched in parallel."""
    paths, truncated = gh.tree(ref)
    if truncated:
        warnings.append("the repository tree is too large for one API call: some build files were not read")
    paths = [p for p in paths if not plan.SKIP_DIRS & set(p.split("/"))]

    def fetch(wanted):
        if len(wanted) > MAX_REMOTE_FILES:
            warnings.append(f"{len(wanted)} files: only the first {MAX_REMOTE_FILES} were read")
            wanted = wanted[:MAX_REMOTE_FILES]
        with concurrent.futures.ThreadPoolExecutor(max_workers=plan.WORKERS) as pool:
            return dict(zip(wanted, pool.map(lambda p: gh.file(p, ref), wanted)))

    texts = fetch([p for p in paths if p.endswith((".gradle.kts", ".gradle"))])
    dirs = plan.convention_dirs({p: t or "" for p, t in texts.items()})
    texts.update(fetch([p for p in paths if p.endswith(".kt") and plan.is_convention_source(p, dirs)]))
    catalogs = sorted(p for p in paths if p.endswith(".versions.toml") and "build" not in p.split("/"))
    return {p: t for p, t in texts.items() if t is not None}, catalogs


def project_context(repo, gh, args, warnings):
    """The baseline the bumps are judged against: local working tree, else the default branch."""
    catalog = plan.find_catalog(repo) if repo else None
    if catalog:
        texts, cat_path = plan.build_texts(repo), catalog.relative_to(repo).as_posix()
        cat_text, where = catalog.read_text(encoding="utf-8"), f"local checkout {repo}"
    else:
        head = gh.default_head()
        texts, catalogs = remote_build_texts(gh, head, warnings)
        cat_path = "gradle/libs.versions.toml" if "gradle/libs.versions.toml" in catalogs else \
            (catalogs[0] if catalogs else None)
        cat_text = gh.file(cat_path, head) if cat_path else None
        where = f"default branch of {gh.slug} at {head[:7]}"
        if cat_text is None:
            raise GhError(f"no version catalog (*.versions.toml) in {gh.slug}")
    cat = plan.parse_catalog_text(cat_text)
    units = plan.build_units(cat)
    net = plan.Net(offline=False)
    refs = plan.scan_references(texts, cat, Path(cat_path).name.split(".versions.toml")[0] or "libs")
    warnings.append("unused entries can't be told apart: the build looks catalog entries up dynamically"
                    if refs["dynamic"] else
                    "unused entries come from a static scan of the build files: confirm before removing any")
    base, sdk_sources, kotlin_source = plan.project_baseline(texts, cat, units, refs, net, args.kotlin,
                                                             args.compile_sdk, False, warnings)
    ctx = {"net": net, "base": base, "report": None, "children": plan.governed_children(cat), "refs": refs,
           "lint": {}, "cooldown": plan.COOLDOWN_DAYS, "offline": False}
    return {"cat": cat, "cat_path": cat_path, "cat_text": cat_text, "units": units, "ctx": ctx, "where": where,
            "baseline": dict(base, kotlinSource=kotlin_source, compileSdkSources=sdk_sources)}


# ---------------------------------------------------------------- what a PR changes

def file_kind(path):
    name = path.rsplit("/", 1)[-1]
    if name.endswith(".versions.toml"):
        return "catalog"
    if name == "gradle-wrapper.properties":
        return "wrapper"
    if name in ("gradle-wrapper.jar", "gradlew", "gradlew.bat"):
        return "wrapper-files"
    if name.endswith((".gradle", ".gradle.kts")):
        return "build"
    if name == "verification-metadata.xml" or name.endswith(".lockfile"):
        return "integrity"
    if path.startswith(".github/workflows/") or path.startswith(".github/actions/"):
        return "actions"
    if path.startswith(".idea/"):
        return "ide"  # e.g. Renovate keeps .idea/kotlinc.xml in step with Kotlin
    return "other"


def catalog_changes(base_text, head_text):
    """[{key, from, to}] by unit key (a version ref, or libraries./plugins.<alias> for an
    inline version), plus notes for entries added or removed."""
    before, after = plan.parse_catalog_text(base_text or ""), plan.parse_catalog_text(head_text or "")
    changes, notes = [], []
    for key, v in after["versions"].items():
        old = before["versions"].get(key)
        if old is None:
            notes.append(f"adds version `{key}`")
        elif old.get("value") != v.get("value"):
            changes.append({"key": key, "from": old.get("value"), "to": v.get("value")})
    for section in ("libraries", "plugins"):
        for alias, e in after[section].items():
            old = before[section].get(alias)
            if old is None:
                notes.append(f"adds {section[:-1] if section == 'plugins' else 'library'} `{alias}`")
            elif not e["ref"] and e["value"] and old.get("value") != e["value"]:
                changes.append({"key": f"{section}.{alias}", "from": old.get("value"), "to": e["value"]})
        notes += [f"removes `{a}` from [{section}]" for a in before[section] if a not in after[section]]
    return changes, notes


def wrapper_version(text):
    m = WRAPPER_URL.search(text or "")
    return m.group(1) if m else None


def inline_changes(base_text, head_text):
    """Inline "group:name:version" strings in a build script whose version changed."""
    before = {f"{g}:{n}": v for g, n, v in INLINE.findall(base_text or "")}
    return [{"key": f"{g}:{n}", "from": before[f"{g}:{n}"], "to": v}
            for g, n, v in INLINE.findall(head_text or "") if f"{g}:{n}" in before and before[f"{g}:{n}"] != v]


def ci_state(rollup):
    states = [str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in rollup or []]
    if not states:
        return "none"
    if any(s in FAILING for s in states):
        return "failing"
    if any(s in PENDING for s in states):
        return "pending"
    if any(s in CANCELLED for s in states):
        return "incomplete"  # e.g. a run cancelled by a newer push: not a failure
    return "passing"


def failing_checks(rollup):
    names = [c.get("workflowName") or c.get("name") or c.get("context") or "?" for c in rollup or []
             if str(c.get("conclusion") or c.get("state") or "").upper() in FAILING]
    return sorted(set(names))


def is_verified_bot(author, extra=()):
    login = (author or {}).get("login") or ""
    return login in extra or (bool((author or {}).get("is_bot")) and bool(BOT.search(login)))


# ---------------------------------------------------------------- review

def inspect_pr(pr, gh, context):
    """What the PR changes, before analysis."""
    files = [f["path"] for f in pr.get("files") or []]
    kinds = {p: file_kind(p) for p in files}
    out = {"changes": [], "other_catalogs": [], "inline": [], "wrapper": None, "notes": [], "blockers": []}
    base, head = gh.merge_base(pr["baseRefOid"], pr["headRefOid"]), pr["headRefOid"]
    for path, kind in kinds.items():
        if kind == "catalog":
            changes, notes = catalog_changes(gh.file(path, base), gh.file(path, head))
            if path == context["cat_path"]:
                out["changes"] += changes
                out["notes"] += notes
            else:
                out["other_catalogs"] += [dict(c, catalog=path) for c in changes]
        elif kind == "wrapper":
            head_text = gh.file(path, head) or ""
            out["wrapper"] = {"from": wrapper_version(gh.file(path, base)), "to": wrapper_version(head_text),
                              "checksum": "distributionSha256Sum=" in head_text,
                              "regenerated": any(k == "wrapper-files" for k in kinds.values())}
        elif kind == "build":
            out["inline"] += [dict(c, file=path) for c in
                              inline_changes(gh.file(path, base), gh.file(path, head))]
    actions = [p for p, k in kinds.items() if k == "actions"]
    if actions:
        out["notes"].append(f"also updates GitHub Actions ({len(actions)} file(s)): outside this skill, review "
                            "them separately")
    other = [p for p, k in kinds.items() if k == "other"]
    if other:
        out["blockers"].append("changes files that aren't dependency files: " + ", ".join(other[:5]) +
                               (f" and {len(other) - 5} more" if len(other) > 5 else ""))
    return out


def analyse_change(change, context):
    unit = context["units"].get(change["key"])
    if unit is None:
        return {"key": change["key"], "current": change["from"], "target": change["to"], "tier": "decision",
                "kind": "unknown", "flags": [], "own_confirmation": False, "members_view": [],
                "reasons": ["no library or plugin uses this version ref"]}
    ctx = dict(context["ctx"], force={change["key"]: change["to"]})
    item = plan.analyse(dict(unit, current=change["from"]), ctx)
    item.pop("sibling", None)  # alternatives matter less here: the PR proposes one version
    return item


def wrapper_review(w):
    reasons = ["Gradle wrapper: own explicit confirmation"]
    if not w["checksum"]:
        reasons.append("no distributionSha256Sum: the downloaded distribution isn't verified")
    if not w["regenerated"]:
        reasons.append("gradle-wrapper.jar and the scripts weren't regenerated: run the wrapper task twice on the branch")
    return {"key": "gradle-wrapper", "kind": "wrapper", "current": w["from"], "target": w["to"], "tier": "care",
            "own_confirmation": True, "flags": [], "members_view": ["gradle-wrapper.properties"], "reasons": reasons,
            "notes_url": f"https://docs.gradle.org/{w['to']}/release-notes.html" if w["to"] else None}


def verdict(pr):
    blockers, cares = list(pr["blockers"]), []
    if not pr["verified_author"]:
        blockers.append(f"author {pr['author']} isn't a verified Dependabot/Renovate bot")
    if pr["draft"]:
        blockers.append("draft")
    if pr["mergeable"] == "CONFLICTING":
        blockers.append("conflicts with the base branch: the bot can rebase it")
    if pr.get("shared_failures"):
        cares.append(f"{', '.join(pr['shared_failures'])} fails on every bot PR: probably broken on the base "
                     "branch, check it there")
    if pr["ci"] == "failing" and pr.get("failing_checks"):
        blockers.append("CI is failing: " + ", ".join(pr["failing_checks"]))
    elif pr["ci"] == "pending":
        cares.append("CI hasn't finished")
    elif pr["ci"] == "incomplete":
        cares.append("some CI checks were cancelled: re-run them")
    elif pr["ci"] == "none":
        cares.append("no CI checks: build it locally before merging")
    for i in pr["items"]:
        if i["tier"] == "decision":
            blockers.append(f"`{i['key']}` needs a decision")
        elif i["tier"] == "care":
            cares.append(f"`{i['key']}` needs care" + (" (own confirmation)" if i.get("own_confirmation") else ""))
    if not pr["items"] and not pr["inline"] and not pr["other_catalogs"]:
        cares.append("no dependency change found in the catalog, wrapper or build scripts")
    pr["blockers"], pr["cares"] = blockers, cares
    pr["verdict"] = "hold" if blockers else "review" if cares else "merge candidate"


def shared_failures(prs):
    """A check failing on every bot PR (three or more) is most likely broken on the base
    branch, not by each bump: it doesn't block a PR on its own."""
    with_ci = [p for p in prs if p["ci"] != "none"]
    if len(with_ci) < 3:
        return
    common = set.intersection(*(set(p["failing_checks"]) for p in with_ci))
    for p in with_ci:
        if common and set(p["failing_checks"]) >= common:
            p["shared_failures"] = sorted(common)
            p["failing_checks"] = sorted(set(p["failing_checks"]) - common)


def cross_pr(prs):
    """Several PRs moving the same entry: the lower target is superseded."""
    by_key = {}
    for pr in prs:
        for i in pr["items"]:
            if i.get("target"):
                by_key.setdefault(i["key"], []).append((pr, i["target"]))
    for key, entries in by_key.items():
        if len(entries) < 2:
            continue
        top = max(entries, key=lambda e: plan.vkey(e[1]))
        for pr, target in entries:
            others = ", ".join(f"#{p['number']}" for p, _ in entries if p is not pr)
            if pr is not top[0] and plan.vkey(target) < plan.vkey(top[1]):
                pr["blockers"].append(f"`{key}` {target} is superseded by #{top[0]['number']} ({top[1]})")
            else:
                pr["notes"].append(f"`{key}` is also changed by {others}")


def review(gh, context, raw_prs, extra_authors=()):
    prs = []
    for raw in raw_prs:
        author = raw.get("author") or {}
        found = inspect_pr(raw, gh, context)
        prs.append({"number": raw["number"], "title": raw.get("title", ""), "url": raw.get("url"),
                    "author": author.get("login"), "verified_author": is_verified_bot(author, extra_authors),
                    "draft": bool(raw.get("isDraft")), "mergeable": raw.get("mergeable"),
                    "ci": ci_state(raw.get("statusCheckRollup")),
                    "failing_checks": failing_checks(raw.get("statusCheckRollup")), **found})
    jobs = [(pr, c) for pr in prs for c in pr["changes"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=plan.WORKERS) as pool:
        results = list(pool.map(lambda job: analyse_change(job[1], context), jobs))
    for pr in prs:
        pr["items"] = [item for (owner, _), item in zip(jobs, results) if owner is pr]
        plan.cross_unit_rules(pr["items"], context["units"], context["ctx"]["base"], context["ctx"]["net"])
        if pr["wrapper"] and pr["wrapper"]["to"] and pr["wrapper"]["to"] != pr["wrapper"]["from"]:
            pr["items"].append(wrapper_review(pr["wrapper"]))
        for i in pr["items"]:
            i["flags"] = sorted(set(i.get("flags", [])))
            for k in ("members", "comments", "rich", "sibling", "fixes_16k", "needs", "moves", "vulns_detail"):
                i.pop(k, None)
    cross_pr(prs)
    shared_failures(prs)
    for pr in prs:
        verdict(pr)
    order = {"merge candidate": 0, "review": 1, "hold": 2}
    prs.sort(key=lambda p: (order[p["verdict"]], p["number"]))
    return prs


# ---------------------------------------------------------------- output

VERDICTS = (("merge candidate", "Merge candidates (still need your OK)"), ("review", "Review first"),
            ("hold", "Hold"))


def bump_text(i):
    arrow = f"{i.get('current')} → {i.get('target')}"
    flags = "".join(f" {plan.FLAG_LABELS[f]}" for f in i.get("flags", []))
    return f"`{i['key']}` {arrow} ({i['tier']}){flags}"


def to_markdown(result):
    lines = [f"Repository: {result['repo']} · {len(result['prs'])} dependency-bot PR(s) · context: {result['where']}",
             f"Baseline: AGP {result['baseline']['agp'] or '?'}, compileSdk {result['baseline']['compileSdk'] or '?'}, "
             f"Kotlin {result['baseline']['kotlin'] or '?'} ({result['baseline']['kotlinSource']})", ""]
    for key, title in VERDICTS:
        prs = [p for p in result["prs"] if p["verdict"] == key]
        if not prs:
            continue
        lines += [f"### {title}", "", "| PR | bumps | CI | notes |", "|---|---|---|---|"]
        for p in prs:
            bumps = [bump_text(i) for i in p["items"]]
            bumps += [f"`{c['key']}` {c['from']} → {c['to']} (inline in {c['file']})" for c in p["inline"]]
            bumps += [f"`{c['key']}` {c['from']} → {c['to']} ({c['catalog']}, not analysed)" for c in p["other_catalogs"]]
            notes = p["blockers"] + p["cares"]
            for i in p["items"]:
                notes += [f"{i['key']}: {r}" for r in i["reasons"]]
                if i.get("notes_url"):
                    notes.append(f"[{i['key']} release notes]({i['notes_url']})")
            notes += p["notes"]
            lines.append(f"| [#{p['number']}]({p['url']}) {p['title']} | {'<br>'.join(bumps) or '—'} | {p['ci']} | "
                         f"{'; '.join(dict.fromkeys(notes)) or '—'} |")
        lines.append("")
    if result["warnings"]:
        lines += ["### Warnings", ""] + [f"- {w}" for w in result["warnings"]]
    return "\n".join(lines)


def lf(text):
    """Line endings as the API serves them: a Windows checkout may use CRLF."""
    return (text or "").replace("\r\n", "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description="Review the open Dependabot/Renovate PRs of an Android project.")
    p.add_argument("repo", nargs="?", help="local checkout (default: . when it has a version catalog)")
    p.add_argument("-R", "--repo-slug", dest="slug", help="GitHub repository as owner/name")
    p.add_argument("--pr", type=int, nargs="+", help="review these PR numbers, whoever opened them")
    p.add_argument("--author", nargs="+", default=[], help="extra bot logins to trust (self-hosted Renovate)")
    p.add_argument("--kotlin", help="effective Kotlin version (from ./gradlew buildEnvironment)")
    p.add_argument("--compile-sdk", type=int, help="override the detected compileSdk")
    p.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    repo = Path(args.repo or ".").resolve()
    local = repo if plan.find_catalog(repo) and (args.repo or not args.slug) else None
    warnings = []
    try:
        slug = resolve_slug(repo, args.slug)
        gh = GitHub(slug)
        raw = gh.prs(args.pr)
        if not args.pr:
            raw = [r for r in raw if is_verified_bot(r.get("author"), args.author)]
        if not raw:
            print(f"No open Dependabot/Renovate PRs in {slug}.")
            return 0
        context = project_context(local, gh, args, warnings)
        prs = review(gh, context, raw, args.author)
    except GhError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if local:
        base_texts = {lf(gh.file(context["cat_path"], r["baseRefOid"])) for r in raw}
        if lf(context["cat_text"]) not in base_texts:
            warnings.append("the local catalog differs from the PRs' base: pull the base branch for an exact context")
    result = {"repo": slug, "where": context["where"], "baseline": context["baseline"], "prs": prs,
              "warnings": warnings}
    if args.json:
        json.dump(result, sys.stdout, indent=2, default=str)
        print()
    else:
        print(to_markdown(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
