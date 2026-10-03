"""Risk and compliance checks used by plan.py. Standard library only.

* known vulnerabilities from OSV (https://osv.dev), for the current and the proposed version,
* 16 KB page-size alignment of the native libraries an AAR ships (Google Play requires
  16 KB support from apps targeting Android 15+; only 64-bit ABIs are checked),
* Android lint findings already on disk: Google Play SDK Index (policy, vulnerability,
  deprecation) and Aligned16KB,
* where to read the release notes of an item,
* files that must be regenerated after a bump (dependency verification, lockfiles) and
  dependency bots that may propose the same bumps.

`net` is plan.Net: get(url) -> bytes | None, post_json(url, payload) -> dict | None.
"""
import io
import re
import struct
import zipfile
from xml.etree import ElementTree

OSV_QUERY = "https://api.osv.dev/v1/query"
PAGE_16K = 16384
ABIS_64 = ("arm64-v8a", "x86_64")
# Android lint issue IDs (lint-checks 32.x, GradleDetector and PageAlignmentDetector).
LINT_IDS = {
    "PlaySdkIndexNonCompliant": "Play SDK Index: policy issue",
    "PlaySdkIndexVulnerability": "Play SDK Index: vulnerability",
    "PlaySdkIndexDeprecated": "Play SDK Index: deprecated",
    "PlaySdkIndexGenericIssues": "Play SDK Index: issue",
    "RiskyLibrary": "Play SDK Index: risky library",
    "OutdatedLibrary": "Play SDK Index: outdated (blocking)",
    "Aligned16KB": "16 KB alignment",
}
# Findings that block a Play release, or that the author marked as a security issue.
LINT_BLOCKING = {"PlaySdkIndexNonCompliant", "PlaySdkIndexVulnerability", "OutdatedLibrary", "Aligned16KB"}
SKIP_DIRS = {".gradle", ".git", ".idea", ".kotlin", "node_modules"}
COORD = re.compile(r"\b([A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)\b")


def local(tag):
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------- vulnerabilities

def osv(net, coord, version):
    """Known vulnerabilities of `group:name` at `version`. [] when none, None when unknown."""
    data = net.post_json(OSV_QUERY, {"package": {"ecosystem": "Maven", "name": coord}, "version": version})
    if data is None:
        return None
    out = []
    for v in data.get("vulns") or []:
        events = [e for a in v.get("affected") or [] if (a.get("package") or {}).get("name") in (None, coord)
                  for r in a.get("ranges") or [] for e in r.get("events") or []]
        severity = (v.get("database_specific") or {}).get("severity")
        out.append({"id": v["id"], "cve": [a for a in v.get("aliases") or [] if a.startswith("CVE-")],
                    "severity": severity, "summary": (v.get("summary") or "").strip(),
                    "fixed": sorted({e["fixed"] for e in events if e.get("fixed")}),
                    # some advisories record the last affected release instead of the fix
                    "last_affected": sorted({e["last_affected"] for e in events if e.get("last_affected")})})
    return out


def vulnerabilities(net, coords, version):
    """Union over the members of a block, by advisory id."""
    found = {}
    for coord in coords:
        for v in osv(net, coord, version) or []:
            found.setdefault(v["id"], v)
    return sorted(found.values(), key=lambda v: v["id"])


def describe(vulns, limit=3):
    parts = []
    for v in vulns[:limit]:
        bits = [b for b in (v["severity"], ", ".join(v["cve"])) if b]
        parts.append(v["id"] + (f" ({'; '.join(bits)})" if bits else ""))
    more = f" and {len(vulns) - limit} more" if len(vulns) > limit else ""
    return ", ".join(parts) + more


# ---------------------------------------------------------------- 16 KB page size

def _load_alignment(elf):
    """Smallest p_align of the PT_LOAD segments, or None if this isn't a readable ELF."""
    if len(elf) < 0x34 or elf[:4] != b"\x7fELF":
        return None
    is64, order = elf[4] == 2, "<" if elf[5] == 1 else ">"
    try:
        if is64:
            phoff, = struct.unpack_from(order + "Q", elf, 0x20)
            phentsize, phnum = struct.unpack_from(order + "HH", elf, 0x36)
        else:
            phoff, = struct.unpack_from(order + "I", elf, 0x1C)
            phentsize, phnum = struct.unpack_from(order + "HH", elf, 0x2A)
        aligns = []
        for i in range(phnum):
            at = phoff + i * phentsize
            if struct.unpack_from(order + "I", elf, at)[0] != 1:  # PT_LOAD
                continue
            aligns.append(struct.unpack_from(order + "Q", elf, at + 0x30)[0] if is64
                          else struct.unpack_from(order + "I", elf, at + 0x1C)[0])
    except struct.error:
        return None
    return min(aligns) if aligns else None


def misaligned_16k(aar):
    """Native libraries of the 64-bit ABIs below 16 KB alignment: None when the AAR has
    none (nothing to check), [] when all are aligned, else their paths inside the AAR."""
    if not aar:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(aar))
    except zipfile.BadZipFile:
        return None
    libs = [n for n in z.namelist() if n.endswith(".so") and any(f"/{abi}/" in f"/{n}" for abi in ABIS_64)]
    if not libs:
        return None
    bad = []
    for name in libs:
        align = _load_alignment(z.read(name))
        if align is not None and align < PAGE_16K:
            bad.append(name)
    return bad


# ---------------------------------------------------------------- lint reports

def lint_findings(repo):
    """(findings, newest report mtime) from Android lint XML reports under build/."""
    findings, newest = [], None
    for path in sorted(repo.glob("**/build/reports/lint-results*.xml")):
        if SKIP_DIRS & set(path.relative_to(repo).parts):
            continue
        try:
            root = ElementTree.parse(path).getroot()
        except (ElementTree.ParseError, OSError):
            continue
        newest = max(newest or 0, path.stat().st_mtime)
        for issue in root.iter("issue"):
            if issue.get("id") not in LINT_IDS:
                continue
            loc = issue.find("location")
            findings.append({"id": issue.get("id"), "severity": issue.get("severity"),
                             "message": issue.get("message") or "",
                             "file": (loc.get("file") if loc is not None else "") or "",
                             "line": int(loc.get("line")) if loc is not None and (loc.get("line") or "").isdigit() else None,
                             "report": str(path.relative_to(repo).as_posix())})
    return findings, newest


def lint_coords(message):
    return {f"{g}:{n}" for g, n in COORD.findall(message)}


# ---------------------------------------------------------------- release notes

def pom_url(pom):
    """The project's SCM or home URL from a POM, preferring the SCM."""
    if not pom:
        return None
    try:
        root = ElementTree.fromstring(pom)
    except ElementTree.ParseError:
        return None
    found = {}
    for child in root:
        if local(child.tag) == "url" and child.text:
            found["url"] = child.text.strip()
        if local(child.tag) == "scm":
            for sub in child:
                if local(sub.tag) == "url" and sub.text:
                    found["scm"] = sub.text.strip()
    return found.get("scm") or found.get("url")


def pom_parent(pom):
    """(groupId, artifactId, version) of the POM's <parent>, which may hold its URL."""
    if not pom:
        return None
    try:
        root = ElementTree.fromstring(pom)
    except ElementTree.ParseError:
        return None
    for child in root:
        if local(child.tag) == "parent":
            fields = {local(c.tag): (c.text or "").strip() for c in child}
            if fields.get("groupId") and fields.get("artifactId") and fields.get("version"):
                return fields["groupId"], fields["artifactId"], fields["version"]
    return None


def release_notes(kind, group, name, pom=None):
    """Where to read what changed. Fixed pages for the toolchain and AndroidX, else the
    project URL from the POM (its GitHub releases when it is hosted there)."""
    if kind == "agp":
        return "https://developer.android.com/build/releases/gradle-plugin"
    if kind == "kotlin":
        return "https://github.com/JetBrains/kotlin/releases"
    if kind == "ksp":
        return "https://github.com/google/ksp/releases"
    if kind == "wrapper":
        return "https://gradle.org/releases/"
    group = group or ""
    if group == "androidx.compose" and name == "compose-bom":
        return "https://developer.android.com/develop/ui/compose/bom/bom-mapping"
    if group.startswith("androidx."):
        return "https://developer.android.com/jetpack/androidx/releases/" + group[len("androidx."):].replace(".", "-")
    if group.startswith("com.google.firebase"):
        return "https://firebase.google.com/support/release-notes/android"
    url = pom_url(pom)
    if not url:
        return None
    # SCM URLs: scm:git:git@github.com:o/r.git, scm:https://…, git://github.com/o/r.git
    url = re.sub(r"^scm:", "", url)
    url = re.sub(r"^git:(?!//)", "", url)
    url = url.replace("git@github.com:", "https://github.com/").replace("git://", "https://")
    m = re.match(r"https?://github\.com/([^/\s]+)/([^/\s#?]+?)(?:\.git)?(?:[/#?].*)?$", url)
    return f"https://github.com/{m.group(1)}/{m.group(2)}/releases" if m else url


# ---------------------------------------------------------------- repository setup

RENOVATE = ("renovate.json", "renovate.json5", ".renovaterc", ".renovaterc.json", ".github/renovate.json",
            ".github/renovate.json5", ".gitlab/renovate.json")


def build_integrity(repo):
    """Steps a bump needs beyond editing the catalog, and bots that propose bumps too."""
    notes = []
    if (repo / "gradle" / "verification-metadata.xml").is_file():
        notes.append("dependency verification (gradle/verification-metadata.xml): after editing, run "
                     "./gradlew --write-verification-metadata sha256 help and review the new entries before "
                     "committing them: each one is a new artifact you trust")
    locks = [p for p in repo.glob("**/*.lockfile") if not ({"build"} | SKIP_DIRS) & set(p.relative_to(repo).parts)]
    if locks:
        notes.append(f"dependency locking ({len(locks)} lockfile(s)): after editing, update them with the build's "
                     "--update-locks <group:name> (or --write-locks) and commit them with the bump")
    bots = [f for f in RENOVATE if (repo / f).is_file()]
    for name in ("dependabot.yml", "dependabot.yaml"):
        path = repo / ".github" / name
        if path.is_file() and re.search(r"package-ecosystem:\s*[\"']?gradle", path.read_text(encoding="utf-8", errors="replace")):
            bots.append(f".github/{name}")
    if bots:
        notes.append(f"dependency bot configured ({', '.join(bots)}): check its open PRs for the same bumps, "
                     "so the two don't conflict")
    return notes


# ---------------------------------------------------------------- hand-offs

def handoff(kind, coord, cur_major, target_major):
    """A specialised skill to hand a migration to, if the agent has it installed."""
    if kind == "agp" and cur_major < 9 <= target_major:
        return ("AGP 8 -> 9 is a migration, not a bump: hand it to the agp-9-upgrade skill "
                "(github.com/android/skills) if it's installed")
    if coord and coord.startswith("com.android.billingclient:") and target_major > cur_major:
        return ("Play Billing major upgrade: hand it to the play-billing-library-version-upgrade skill "
                "(github.com/android/skills) if it's installed")
    return None
