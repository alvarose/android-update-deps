#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Build a draft update proposal for an Android version catalog.

Joins gradle/libs.versions.toml with the available versions and classifies every
catalog entry the way SKILL.md step 3 describes, so the agent starts from facts
instead of re-deriving them by hand:

* groups libraries/plugins that share a `version.ref` into one unit (coupled block)
  and lists the BOM-governed children,
* labels toolchain units (AGP, Kotlin, KSP and its version scheme, Gradle wrapper),
* takes candidates from the ben-manes report when there is one, otherwise straight
  from Maven metadata (Google Maven, Maven Central, Gradle Plugin Portal); JitPack
  libraries always come from JitPack metadata,
* checks that the target exists for every member of a block, reads the target AAR's
  minCompileSdk / minAndroidGradlePluginVersion and the kotlin-stdlib in its POM,
  measures release age, honours hold comments, and searches the highest compatible
  version when the latest doesn't fit,
* finds unused entries by scanning the build files for catalog references,
* tiers each unit: safe / care (own confirmation for toolchain) / decision.

The output is a draft: the agent still reads release notes, adjusts judgement and
must get explicit confirmation (SKILL step 4) before editing anything.

Usage:
    python3 scripts/plan.py [repo] [--source auto|report|metadata] [--kotlin X]
                            [--compile-sdk N] [--cooldown-days N] [--offline] [--json]

Exit codes: 0 plan produced; 1 no version catalog found; 2 bad arguments.
"""
import argparse
import concurrent.futures
import email.utils
import importlib.util
import io
import json
import re
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import risk  # noqa: E402  (risk.py sits next to this script)

# A release younger than this has not had time for regressions to be reported.
# Dependabot uses the same default cooldown.
COOLDOWN_DAYS = 3
# Skipping this many minor versions or more is not "safe" by default (SKILL step 3).
MULTI_MINOR = 3
# AAR downloads per unit when searching an older compatible version; bounds the run time.
MAX_STEPDOWN_PROBES = 6
# Network timeout per request, in seconds; Maven repos answer well under this.
HTTP_TIMEOUT = 20
# Parallel network lookups; polite for public repositories.
WORKERS = 8

GOOGLE = "https://dl.google.com/android/maven2"
CENTRAL = "https://repo1.maven.org/maven2"
PORTAL = "https://plugins.gradle.org/m2"
JITPACK = "https://jitpack.io"
GRADLE_CURRENT = "https://services.gradle.org/versions/current"
USER_AGENT = "android-update-deps/plan.py (+https://github.com/alvarose/android-update-deps)"

GOOGLE_GROUPS = (
    "androidx.", "android.arch.", "com.android.", "com.google.android.", "com.google.firebase",
    "com.google.gms", "com.google.mlkit", "com.google.ar", "com.google.testing.platform",
)
UNSTABLE = re.compile(r"(?i)(alpha|beta|rc|cr|dev|eap|snapshot|preview|canary|milestone|nightly|[.-]m\d+)")
HOLD = re.compile(
    r"(?i)(\bhold\b|\bheld\b|\bpinn?(ed)?\b|@pin|@keep|noinspection|"
    r"do ?n[o']t (upgrade|update|bump)|no (actualizar|subir|tocar))")
OLD_KSP = re.compile(r"^\d+\.\d+\.\d+-\d+\.\d+\.\d+$")
TOKEN = re.compile(r"\d+|[A-Za-z]+")


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


# ---------------------------------------------------------------- versions

def vkey(v):
    return tuple((1, int(t)) if t.isdigit() else (0, t.lower()) for t in TOKEN.findall(v or ""))


def is_stable(v):
    return bool(v) and not UNSTABLE.search(v)


def nums(v):
    parts = [int(x) for x in re.findall(r"\d+", (v or "").split("-")[0])][:3]
    return parts + [0] * (3 - len(parts))


def delta(cur, tgt):
    a, b = nums(cur), nums(tgt)
    if a[0] != b[0]:
        return "major", b[0] - a[0]
    if a[1] != b[1]:
        return "minor", b[1] - a[1]
    return "patch", b[2] - a[2]


# ---------------------------------------------------------------- catalog

SECTION = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*$")
ENTRY = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=\s*(.+?)\s*$")


def strip_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i], line[i + 1:].strip()
    return line, None


def split_top(s):
    parts, buf, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return parts


def parse_value(v):
    v = v.strip()
    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        return v[1:-1]
    if v.startswith("{") and v.endswith("}"):
        out = {}
        for part in split_top(v[1:-1]):
            if "=" in part:
                k, val = part.split("=", 1)
                out[k.strip().strip("\"'")] = parse_value(val)
        return out
    if v.startswith("[") and v.endswith("]"):
        return [parse_value(x) for x in split_top(v[1:-1])]
    return v


def version_of(spec):
    """(value, ref, rich) from a library/plugin spec dict."""
    if "version.ref" in spec:
        return None, spec["version.ref"], False
    ver = spec.get("version")
    if isinstance(ver, dict):
        if "ref" in ver:
            return None, ver["ref"], False
        return ver.get("strictly") or ver.get("require") or ver.get("prefer"), None, True
    return ver, None, False


def parse_catalog(path):
    return parse_catalog_text(path.read_text(encoding="utf-8"))


def parse_catalog_text(text):
    cat = {"versions": {}, "libraries": {}, "plugins": {}, "bundles": {}}
    section, pending, bundles = None, [], []
    for n, raw in enumerate(text.splitlines(), 1):
        code, comment = strip_comment(raw)
        if not code.strip():
            pending = pending + [comment] if comment is not None else []
            continue
        m = SECTION.match(code)
        if m:
            section, pending = m.group(1), []
            continue
        if section == "bundles":  # arrays may span lines: parsed once the section is read
            bundles.append(code)
            continue
        m = ENTRY.match(code) if section in cat else None
        if not m:
            pending = []
            continue
        key, value = m.group(1), parse_value(m.group(2))
        entry = {"line": n, "comments": [c for c in pending + [comment] if c]}
        pending = []
        if section == "versions":
            if isinstance(value, dict):
                entry.update(value=value.get("strictly") or value.get("require") or value.get("prefer"), rich=True)
            else:
                entry.update(value=value, rich=False)
        elif section == "libraries":
            if isinstance(value, str):
                parts = value.split(":")
                if len(parts) < 2:
                    continue
                entry.update(group=parts[0], name=parts[1], value=parts[2] if len(parts) > 2 else None, ref=None, rich=False)
            else:
                module = value.get("module")
                group, name = module.split(":", 1) if module else (value.get("group"), value.get("name"))
                val, ref, rich = version_of(value)
                entry.update(group=group, name=name, value=val, ref=ref, rich=rich)
            if not entry.get("group") or not entry.get("name"):
                continue
        else:  # plugins
            if isinstance(value, str):
                pid, _, val = value.partition(":")
                entry.update(id=pid, value=val or None, ref=None, rich=False)
            else:
                val, ref, rich = version_of(value)
                entry.update(id=value.get("id"), value=val, ref=ref, rich=rich)
            if not entry.get("id"):
                continue
        cat[section][key] = entry
    for m in re.finditer(r"([A-Za-z0-9_.-]+)\s*=\s*\[(.*?)\]", "\n".join(bundles), re.S):
        cat["bundles"][m.group(1)] = re.findall(r"[\"']([^\"']+)[\"']", m.group(2))
    return cat


def find_catalog(repo):
    default = repo / "gradle" / "libs.versions.toml"
    if default.is_file():
        return default
    found = sorted(p for p in repo.glob("**/*.versions.toml") if "build" not in p.parts)
    return found[0] if found else None


# ---------------------------------------------------------------- project baseline

SDK_PATTERNS = (
    re.compile(r"compileSdk\s*=\s*(\d+)"),
    re.compile(r"compileSdk\s*\(\s*(\d+)\s*\)"),
    re.compile(r"compileSdkVersion\s*[=(]?\s*(\d+)"),
    re.compile(r"compileSdk\s*\{[^}]*?release\(\s*(\d+)", re.S),
)


# Kotlin constants in convention plugins: `const val COMPILE_SDK = 36`, `const val sdkCompile = 37`.
CONST = re.compile(r"\bval\s+([A-Za-z_]\w*)\s*(?::\s*Int)?\s*=\s*(\d+)\b")
SDK_BY_NAME = re.compile(r"compileSdk(?:Version)?\s*=\s*(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\b(?!\s*\()")
SDK_MINOR = re.compile(r"(?:minorApiLevel|compileSdkMinor)\s*=\s*(\d+)")
SKIP_DIRS = {"build", ".gradle", ".git", ".idea", ".kotlin", "node_modules"}


INCLUDE_BUILD = re.compile(r"\bincludeBuild\s*\(?\s*[\"']([^\"']+)[\"']")


def convention_dirs(gradle_texts):
    """Where convention plugins live: build-logic/, buildSrc/ and every includeBuild()
    that a settings script names (e.g. element-x's plugins/)."""
    dirs = {"build-logic", "buildSrc"}
    for path, text in gradle_texts.items():
        if path.rsplit("/", 1)[-1].startswith("settings.gradle"):
            for line in text.splitlines():
                m = INCLUDE_BUILD.search(line)
                if m and not line.lstrip().startswith("//"):
                    dirs.add(m.group(1).strip("./").rstrip("/"))
    return dirs


def is_convention_source(path, dirs):
    return any(path == d or path.startswith(d + "/") for d in dirs) or bool({"build-logic", "buildSrc"} & set(path.split("/")))


def build_texts(repo):
    """Gradle scripts, plus the Kotlin sources of convention plugins."""
    def keep(f):
        return not SKIP_DIRS & set(f.relative_to(repo).parts)

    read = lambda f: f.read_text(encoding="utf-8", errors="replace")  # noqa: E731
    texts = {f.relative_to(repo).as_posix(): read(f)
             for pattern in ("**/*.gradle.kts", "**/*.gradle") for f in repo.glob(pattern) if keep(f)}
    dirs = convention_dirs(texts)
    for f in repo.glob("**/*.kt"):
        rel = f.relative_to(repo).as_posix()
        if keep(f) and is_convention_source(rel, dirs):
            texts[rel] = read(f)
    return texts


def detect_compile_sdk(texts, cat):
    """(compileSdk, minor API level, sources) from the catalog, the build scripts, or
    Kotlin constants in convention plugins, e.g. `compileSdk = APP_COMPILE_SDK`.
    The lowest value wins: every module must satisfy a library's requirement."""
    values = {}
    for key, v in cat["versions"].items():
        if re.search(r"(?i)compile.?sdk", key) and (v.get("value") or "").isdigit():
            values[f"catalog:{key}"] = int(v["value"])
    consts = {name: int(num) for t in texts.values() for name, num in CONST.findall(t)}
    minors = []
    for rel, text in texts.items():
        for pat in SDK_PATTERNS:
            for m in pat.finditer(text):
                values[rel] = int(m.group(1))
        for m in SDK_BY_NAME.finditer(text):
            if m.group(1) in consts:
                values[f"{rel} ({m.group(1)})"] = consts[m.group(1)]
        minors += [int(x) for x in SDK_MINOR.findall(text)]
    return (min(values.values()) if values else None), (min(minors) if minors else 0), values


# A reference must end the accessor: `libs.androidx.core` is not `libs.androidx.core.ktx`.
ACCESSOR_END = r"(?:\.get\(\)|\.asProvider\(\)|(?![\w.]))"
APPLY_FALSE = re.compile(r"\bapply\s*\(?\s*false\b")
FIND_LITERAL = re.compile(r"\bfind(Library|Plugin|Bundle)\(\s*[\"']([^\"']+)[\"']")
CATALOG_VAR = re.compile(r"\b(?:val|var)\s+(\w+)\s*(?::\s*LibrariesFor\w+\s*)?=[^\n]*\bLibrariesFor\w+")
FIND_DYNAMIC = re.compile(r"\bfind(?:Library|Plugin|Bundle)\(\s*[^\s\"')]")


def accessor(alias):
    return re.sub(r"[-_.]", ".", alias)


def scan_references(texts, cat, name="libs"):
    """Which catalog entries the build files reference: library aliases (directly or
    through a bundle), plugins applied by some module, and plugins only declared with
    `apply false`. When the build also looks entries up dynamically (findLibrary(variable)),
    `dynamic` is set: what is referenced is still used, but a missing reference proves nothing."""
    dynamic = any(FIND_DYNAMIC.search(t) for t in texts.values())
    # the catalog can also be reached through a variable: `val catalog = the<LibrariesForLibs>()`
    names = {name} | {v for t in texts.values() for v in CATALOG_VAR.findall(t)}
    prefix = "(?:" + "|".join(sorted(re.escape(n) for n in names)) + ")"
    libs = {a: re.compile(rf"\b{prefix}\.{re.escape(accessor(a))}{ACCESSOR_END}") for a in cat["libraries"]}
    bundles = {a: re.compile(rf"\b{prefix}\.bundles\.{re.escape(accessor(a))}{ACCESSOR_END}") for a in cat["bundles"]}
    plugins = {a: re.compile(rf"\b{prefix}\.plugins\.{re.escape(accessor(a))}{ACCESSOR_END}|[\"']{re.escape(e['id'])}[\"']")
               for a, e in cat["plugins"].items()}
    used, applied, declared = set(), set(), set()

    def use_bundle(alias):
        used.update(cat["bundles"].get(alias, []))

    for text in texts.values():
        for line in text.splitlines():
            if line.lstrip().startswith("//") or ("." not in line and "\"" not in line and "'" not in line):
                continue
            used.update(a for a, rx in libs.items() if rx.search(line))
            for a, rx in bundles.items():
                if rx.search(line):
                    use_bundle(a)
            for a, rx in plugins.items():
                if rx.search(line):
                    (declared if APPLY_FALSE.search(line) else applied).add(a)
            for kind, alias in FIND_LITERAL.findall(line):
                norm = accessor(alias)
                pool = {"Library": cat["libraries"], "Plugin": cat["plugins"], "Bundle": cat["bundles"]}[kind]
                for a in pool:
                    if accessor(a) == norm:
                        {"Library": used.add, "Plugin": applied.add, "Bundle": use_bundle}[kind](a)
    return {"libraries": used, "applied": applied, "declared": declared - applied, "dynamic": dynamic}


def usage(unit, refs):
    """used / declared (plugins only behind `apply false`) / unused, or None if unknown."""
    if refs is None:
        return None
    members = unit["members"]
    if any(m["alias"] in (refs["libraries"] if m["section"] == "libraries" else refs["applied"]) for m in members):
        return "used"
    if refs.get("dynamic"):
        return None  # a dynamic lookup may apply it
    if any(m["section"] == "plugins" and m["alias"] in refs["declared"] for m in members):
        return "declared"
    return "unused"


def wrapper_version(repo):
    props = repo / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not props.is_file():
        return None
    m = re.search(r"gradle-([0-9][^-/]*(?:-(?!bin|all)[^-/]+)?)-(bin|all)\.zip", props.read_text(encoding="utf-8"))
    return m.group(1) if m else None


# ---------------------------------------------------------------- network

class Net:
    def __init__(self, offline=False):
        self.offline = offline
        self._cache, self._lock = {}, threading.Lock()

    def _open(self, url, method="GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        return urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)

    def get(self, url):
        if self.offline:
            return None
        with self._lock:
            if url in self._cache:
                return self._cache[url]
        data = None
        try:
            with self._open(url) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403, 404):  # absent, or private (JitPack answers 401)
                warn(f"{url}: HTTP {e.code}")
        except (urllib.error.URLError, OSError) as e:
            warn(f"{url}: {e}")
        with self._lock:
            self._cache[url] = data
        return data

    def post_json(self, url, payload):
        """POST a JSON body and parse the JSON answer (cached by URL and body)."""
        if self.offline:
            return None
        body = json.dumps(payload, sort_keys=True)
        key = f"POST {url} {body}"
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        data = None
        req = urllib.request.Request(url, data=body.encode(), method="POST",
                                     headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            warn(f"{url}: {e}")
        with self._lock:
            self._cache[key] = data
        return data

    def age_days(self, url):
        if self.offline:
            return None
        try:
            with self._open(url, "HEAD") as r:
                stamp = r.headers.get("Last-Modified")
        except (urllib.error.URLError, OSError):
            return None
        if not stamp:
            return None
        when = email.utils.parsedate_to_datetime(stamp)
        return (datetime.now(timezone.utc) - when).days


def gpath(group):
    return group.replace(".", "/")


def repos_for(group=None, plugin_id=None):
    if plugin_id:
        if plugin_id.startswith(("com.android.", "com.google.")):
            return [GOOGLE, PORTAL]
        return [PORTAL, GOOGLE, CENTRAL]
    if group.startswith("com.github."):
        return [JITPACK, CENTRAL]  # some com.github.* artifacts are published to Maven Central
    if group.startswith(GOOGLE_GROUPS):
        return [GOOGLE, CENTRAL]
    return [CENTRAL, GOOGLE]


def local(tag):
    return tag.rsplit("}", 1)[-1]


def metadata(net, group, name, repos, current=None):
    """(repo, versions). Some repositories hold partial mirrors (e.g. Google Maven
    has an old subset of the KSP plugin markers), so every candidate repository is
    read: the versions are merged, and the repo returned is the one that publishes
    the current version (else the one with the most versions)."""
    found = []
    for base in repos:
        data = net.get(f"{base}/{gpath(group)}/{name}/maven-metadata.xml")
        if not data:
            continue
        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError:
            continue
        versions = [e.text.strip() for e in root.iter() if local(e.tag) == "version" and e.text]
        if versions:
            found.append((base, versions))
    if not found:
        return None, []
    best = next((b for b, vs in found if current in vs), None) or max(found, key=lambda f: len(f[1]))[0]
    merged = sorted({v for _, vs in found for v in vs}, key=vkey)
    return best, merged


def artifact(base, group, name, version):
    return f"{base}/{gpath(group)}/{name}/{version}/{name}-{version}"


def aar_requirements(net, base, group, name, version):
    """Requirements declared by the artifact's AAR. Kotlin Multiplatform libraries
    publish a small root `.aar` without metadata; the real one is `<name>-android`."""
    fallback = None
    for art in (name, f"{name}-android"):
        data = net.get(artifact(base, group, art, version) + ".aar")
        if not data:
            continue
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            text = z.read("META-INF/com/android/build/gradle/aar-metadata.properties").decode()
        except (zipfile.BadZipFile, KeyError):
            fallback = fallback or {"artifact": art}
            continue
        props = {k.strip(): v.strip() for k, v in (l.split("=", 1) for l in text.splitlines() if "=" in l)}
        sdk, minor = props.get("minCompileSdk", ""), props.get("minCompileMinorSdk", "")
        return {"artifact": art, "minCompileSdk": int(sdk) if sdk.isdigit() else None,
                "minCompileMinorSdk": int(minor) if minor.isdigit() else 0,
                "minAgp": props.get("minAndroidGradlePluginVersion")}
    return fallback


def pom_deps(net, base, group, name, version):
    data = net.get(artifact(base, group, name, version) + ".pom")
    if not data:
        return None
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None
    deps = []
    for el in root.iter():
        if local(el.tag) == "dependency":
            fields = {local(c.tag): (c.text or "").strip() for c in el}
            deps.append(fields)
    return deps


def stdlib_of(net, base, group, name, version):
    for dep in pom_deps(net, base, group, name, version) or []:
        if dep.get("groupId") == "org.jetbrains.kotlin" and dep.get("artifactId", "").startswith("kotlin-stdlib"):
            return dep.get("version")
    return None


def bundled_kgp(net, agp):
    """The Kotlin Gradle plugin AGP depends on: the built-in Kotlin of AGP 9+ unless
    the build puts a newer one on the classpath."""
    for dep in pom_deps(net, GOOGLE, "com.android.tools.build", "gradle", agp) or []:
        if dep.get("groupId") == "org.jetbrains.kotlin" and dep.get("artifactId") == "kotlin-gradle-plugin":
            return dep.get("version")
    return None


# ---------------------------------------------------------------- units

def kind_of(members):
    for m in members:
        coord = m.get("coord", "")
        pid = m.get("id") or ""
        if pid.startswith("com.android.") or coord == "com.android.tools.build:gradle":
            return "agp"
        if pid == "com.google.devtools.ksp" or coord.startswith("com.google.devtools.ksp:"):
            return "ksp"
        if pid.startswith("org.jetbrains.kotlin.") or coord.startswith("org.jetbrains.kotlin:kotlin-gradle-plugin"):
            return "kotlin"
    if any(m.get("name", "").endswith("bom") for m in members):
        return "bom"
    return "plugin" if all("id" in m for m in members) else "library"


def build_units(cat):
    units = {}
    for section in ("libraries", "plugins"):
        for alias, e in cat[section].items():
            member = dict(e, alias=alias, section=section)
            if section == "libraries":
                member["coord"] = f"{e['group']}:{e['name']}"
            if e["ref"]:
                key = e["ref"]
                ver = cat["versions"].get(key)
                value = ver["value"] if ver else None
                rich = ver["rich"] if ver else False
                comments = (ver or {}).get("comments", [])
            elif e["value"]:
                key, value, rich, comments = f"{section}.{alias}", e["value"], e["rich"], []
            else:
                continue  # version-less: governed by a BOM/platform or a plugin
            u = units.setdefault(key, {"key": key, "current": value, "rich": rich,
                                       "comments": list(comments), "members": []})
            u["members"].append(member)
            u["comments"] += e["comments"]
    for u in units.values():
        u["kind"] = kind_of(u["members"])
    return units


def governed_children(cat):
    boms = {a: e for a, e in cat["libraries"].items() if e["name"].endswith("bom") and (e["ref"] or e["value"])}
    out = {}
    for alias, e in cat["libraries"].items():
        if e["ref"] or e["value"]:
            continue
        owner = next((b for b, be in boms.items() if e["group"].startswith(be["group"])), None)
        out[alias] = {"coord": f"{e['group']}:{e['name']}", "bom": owner}
    return out


# ---------------------------------------------------------------- candidates

def load_aggregate():
    spec = importlib.util.spec_from_file_location("aggregate_updates", HERE / "aggregate-updates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def report_data(repo, catalog):
    agg = load_aggregate()
    reports = agg.find_reports(repo)
    if not reports:
        return None
    newest = max(p.stat().st_mtime for p in reports)
    seen = set()
    for path in reports:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for sec in ("current", "outdated", "exceeded", "unresolved", "undeclared"):
            for dep in (data.get(sec) or {}).get("dependencies", []):
                seen.add(f"{dep.get('group')}:{dep.get('name')}")
    result = agg.collect(reports)
    result["seen"] = seen
    result["reports"] = [str(p) for p in reports]
    # edited after the last detection run: entries may not match the catalog any more
    result["stale"] = catalog.stat().st_mtime > newest
    return result


def member_coord(m):
    return m["coord"] if m["section"] == "libraries" else f"{m['id']}:{m['id']}.gradle.plugin"


def member_lookup(net, m, current):
    if m["section"] == "libraries":
        return metadata(net, m["group"], m["name"], repos_for(group=m["group"]), current)
    return metadata(net, m["id"], f"{m['id']}.gradle.plugin", repos_for(plugin_id=m["id"]), current)


def stable_between(versions, low, high=None):
    out = [v for v in versions if is_stable(v) and vkey(v) > vkey(low) and (high is None or vkey(v) <= vkey(high))]
    return sorted(set(out), key=vkey, reverse=True)


# ---------------------------------------------------------------- analysis

def requirement_gaps(req, stdlib, base):
    """Unmet requirements as {kind, value, msg}; kind is compileSdk, agp or kotlin."""
    gaps = []
    if req:
        need = (req.get("minCompileSdk") or 0, req.get("minCompileMinorSdk") or 0)
        have = (base["compileSdk"] or 0, base.get("compileSdkMinor") or 0)
        if need[0] and base["compileSdk"] and need > have:
            label = f"{need[0]}.{need[1]}" if need[1] else str(need[0])
            gaps.append({"kind": "compileSdk", "value": label,
                         "msg": f"needs compileSdk {label} (project {base['compileSdk']})"})
        if req.get("minAgp") and base["agp"] and vkey(req["minAgp"]) > vkey(base["agp"]):
            gaps.append({"kind": "agp", "value": req["minAgp"],
                         "msg": f"needs AGP >= {req['minAgp']} (project {base['agp']})"})
    if stdlib and base["kotlin"]:
        s, k = nums(stdlib), nums(base["kotlin"])
        if (s[0] - k[0]) * 100 + (s[1] - k[1]) > 1:
            gaps.append({"kind": "kotlin", "value": stdlib,
                         "msg": f"pulls kotlin-stdlib {stdlib}; project Kotlin {base['kotlin']}"})
    return gaps


def library_gaps(net, base, m, version, repo_url):
    if m["section"] != "libraries" or not repo_url or repo_url == JITPACK:
        return []
    req = aar_requirements(net, repo_url, m["group"], m["name"], version)
    art = (req or {}).get("artifact", m["name"])
    stdlib = stdlib_of(net, repo_url, m["group"], art, version)
    gaps = requirement_gaps(req, stdlib, base)
    if gaps:
        return gaps
    # a thin wrapper (core-ktx) can declare less than the artifact it pulls in at the
    # same version (core): check up to two such siblings
    siblings = [d for d in pom_deps(net, repo_url, m["group"], art, version) or []
                if d.get("groupId") == m["group"] and d.get("version") == version
                and d.get("artifactId") != art and d.get("scope", "compile") in ("compile", "runtime")]
    for dep in siblings[:2]:
        sub = requirement_gaps(aar_requirements(net, repo_url, m["group"], dep["artifactId"], version), None, base)
        if sub:
            return [dict(g, msg=f"{dep['artifactId']} {version} {g['msg']}") for g in sub]
    return []


def bom_managed(net, repo_url, bom, version):
    deps = pom_deps(net, repo_url, bom["group"], bom["name"], version) or []
    return {f"{d.get('groupId')}:{d.get('artifactId')}": d.get("version") for d in deps}


def bom_gaps(net, base, unit, version, children, repo_url, current=None):
    """Requirements of the children the project uses, at the BOM `version`. With
    `current`, `moved` says what each child moves from and to (a BOM bump is the sum
    of its children's bumps)."""
    bom = unit["members"][0]
    managed = bom_managed(net, repo_url, bom, version)
    before = bom_managed(net, repo_url, bom, current) if current else {}
    gaps, moved = [], []
    used = [c for c in children.values() if c["bom"] == bom["alias"]]
    for child in used[:MAX_STEPDOWN_PROBES]:
        cver = managed.get(child["coord"])
        if not cver:
            continue
        group, name = child["coord"].split(":")
        old = before.get(child["coord"])
        if old and old != cver:
            major = " (major)" if nums(old)[0] != nums(cver)[0] else ""
            moved.append(f"{name} {old} -> {cver}{major}")
        elif not old:
            moved.append(f"{name} {cver}")
        req = aar_requirements(net, GOOGLE if group.startswith(GOOGLE_GROUPS) else CENTRAL, group, name, cver)
        for g in requirement_gaps(req, None, base):
            if g["msg"] not in {x["msg"] for x in gaps}:
                gaps.append(g)
    return gaps, moved


def usage_state(unit, ctx, coords):
    """Static references decide; a fresh report settles what the scan can't (dynamic
    lookups) and vouches for entries it resolved."""
    report = ctx["report"]
    fresh = report is not None and not report["stale"]
    state = usage(unit, ctx.get("refs"))
    if fresh:
        seen = any(c in report["seen"] for c in coords)
        if state == "unused" and seen:
            state = "used"
        elif state is None:
            state = "used" if seen else "unused"
    return state


def unused_reason(kind, state, base):
    if kind == "kotlin" and base["agp"] and nums(base["agp"])[0] >= 9:
        return ("no module applies the plugins this ref feeds: under AGP 9 built-in Kotlin it doesn't "
                "control the compiler (see the Kotlin row in references/reference.md)")
    if state == "declared":
        return "declared with `apply false` but no module applies it: remove it, or keep it for a planned use"
    return "not referenced by any build file: likely an unused catalog entry (suggest removing it separately)"


def analyse(unit, ctx):
    net, base, report = ctx["net"], ctx["base"], ctx["report"]
    u = dict(unit, reasons=[], tier="safe", own_confirmation=False, target=None, alternative=None,
             moves=[], age_days=None, needs=[], sibling=None, flags=[], vulns=[], notes_url=None)
    u["members_view"] = [m["alias"] for m in unit["members"]]
    cur = unit["current"]
    if unit["rich"] or not cur:
        u.update(tier="decision", reasons=["rich or missing version constraint: review by hand"])
        return u
    holds = [c for c in unit["comments"] if HOLD.search(c)]
    kind = unit["kind"]

    coords = [member_coord(m) for m in unit["members"]]
    is_jitpack = any(m.get("group", "").startswith("com.github.") for m in unit["members"])
    state = usage_state(unit, ctx, coords)
    unused = state in ("unused", "declared")
    if unused:
        # still looked up below: an unpublished current version matters for the decision
        u.update(tier="decision", reasons=[unused_reason(kind, state, base)])

    # versions published for every member (target must exist for all of them)
    lookups = [member_lookup(net, m, cur) for m in unit["members"]]
    repo_url = lookups[0][0]
    common = None
    for _, versions in lookups:
        if versions:
            common = set(versions) if common is None else common & set(versions)
    versions = sorted(common or [], key=vkey)

    target = None
    if report is not None and not is_jitpack:
        for e in report["outdated"]:
            # an entry whose current version differs from the catalog is stale: ignore it
            if e["coordinate"] in coords and e.get("current") == cur and is_stable(e["latest"]):
                target = e["latest"] if target is None or vkey(e["latest"]) < vkey(target) else target
        if target and versions and target not in versions:
            target = None  # not published for every member of the block
    if target is None and versions:
        newer = stable_between(versions, cur)
        target = newer[0] if newer else None
    if target and vkey(target) <= vkey(cur):
        target = None
    forced = ctx.get("force", {}).get(unit["key"])
    if forced:
        # reviewing a proposed bump (a bot PR): judge that version, and say if a newer one exists
        if target and vkey(target) > vkey(forced):
            u["latest"] = target
            u["reasons"].append(f"newer stable available: {target}")
        if versions and forced not in versions:
            u["reasons"].append(f"{forced} is not published for every member of the block")
            u["tier"] = "decision"
        if not is_stable(forced):
            u["reasons"].append(f"{forced} is a pre-release")
        if vkey(forced) <= vkey(cur):
            u["reasons"].append(f"{forced} is not newer than {cur}: a downgrade")
            u["tier"] = "decision"
        target = forced

    if versions and cur not in versions:
        u["reasons"].append(f"current version {cur} is not published in its repository")
        u["tier"] = "decision"
    latest_stable = max((v for v in versions if is_stable(v)), key=vkey, default=None)
    if latest_stable and vkey(cur) > vkey(latest_stable):
        u["reasons"].append(f"current {cur} is newer than the latest stable ({latest_stable}): "
                            "keep it deliberately or move back to a stable release")
        u["tier"] = "decision"

    # security and Play findings on the current version matter even with nothing newer
    lib_coords = [m["coord"] for m in unit["members"] if m["section"] == "libraries"]
    check = not ctx["offline"] and not unused and bool(lib_coords)
    vulns = risk.vulnerabilities(net, lib_coords, cur) if check else []
    fix = fix_version(versions, cur, vulns) if vulns else None
    if vulns:
        u["flags"].append("security")
        u["vulns"] = [v["id"] for v in vulns]
        u["reasons"].append(f"security: {cur} is affected by {risk.describe(vulns)}"
                            + (f"; first fixed in {fix}" if fix else
                               "" if target else "; no stable release fixes all of them"))
    findings = [] if unused else ctx.get("lint", {}).get(unit["key"], [])
    for f in findings:
        u["flags"].append("16kb" if f["id"] == "Aligned16KB" else "sdk-index")
        u["reasons"].append(f"lint {f['id']}: {f['message']}")
    blocking = vulns or any(f["id"] in risk.LINT_BLOCKING for f in findings)

    if target is None:
        if blocking and u["tier"] != "decision":
            u["tier"] = "decision"
            u["reasons"].append("no newer stable release: decide whether to keep it, replace it or wait")
        if u["tier"] == "decision":
            return u
        u["tier"] = "current"
        return u
    u["target"] = target
    if kind == "ksp" and OLD_KSP.match(cur):
        u["reasons"].append("old <kotlin>-<ksp> scheme: migrate to KSP 2.3.x before any Kotlin bump")
    if unused:
        return u  # not a bump to propose: no requirement checks

    if kind in ("agp", "kotlin"):
        u["own_confirmation"] = True
        u["reasons"].append(f"{kind.upper() if kind == 'agp' else 'Kotlin'} toolchain: own explicit confirmation")
    if kind == "ksp":
        u["reasons"].append("KSP drives annotation processing: verify codegen")
    level, size = delta(cur, target)
    # BOM versions are dates: their "minor" is a month, so the components decide the risk.
    multi = level == "minor" and size >= MULTI_MINOR and kind != "bom"
    if level == "major":
        u["reasons"].append(f"major jump {cur} -> {target}: read the changelog")
    elif multi:
        u["reasons"].append(f"skips {size} minors: read the release notes in between")
    if holds:
        u["reasons"].append("held in the catalog: " + " | ".join(holds))
    hand = risk.handoff(kind, (lib_coords or [None])[0], nums(cur)[0], nums(target)[0])
    if hand:
        u["reasons"].append(hand)

    target_vulns = risk.vulnerabilities(net, lib_coords, target) if check else []
    if target_vulns:
        u["flags"].append("security")
        u["reasons"].append(f"security: {target} is also affected by {risk.describe(target_vulns)}")
    elif vulns:
        u["reasons"].append(f"{target} isn't affected")
    native = native_16k(net, repo_url, unit["members"][0], target, cur) if check and kind != "bom" else None
    bad_target = bool(native and native[0])
    if native:
        bad_t, bad_c = native
        if bad_t:
            u["flags"].append("16kb")
            u["reasons"].append(f"16 KB page size: {target} ships native libraries that aren't 16 KB aligned "
                                f"({lib_names(bad_t)})")
        elif bad_c:
            u["flags"].append("16kb")
            u["fixes_16k"] = True
            u["reasons"].append(f"16 KB page size: {cur} ships native libraries that aren't 16 KB aligned "
                                f"({lib_names(bad_c)}), which Google Play requires for apps targeting "
                                f"Android 15+; {target} is aligned")
    if not ctx["offline"]:
        u["notes_url"] = notes_url(net, repo_url, unit, target)

    gaps = []
    if not ctx["offline"]:
        if kind == "bom":
            gaps, u["moves"] = bom_gaps(net, base, unit, target, ctx["children"], repo_url, cur)
            if any(mv.endswith("(major)") for mv in u["moves"]):
                u["reasons"].append("a library the BOM manages changes major version: read its release notes")
        else:
            gaps = library_gaps(net, base, unit["members"][0], target, repo_url)
        if repo_url:
            u["age_days"] = net.age_days(artifact(repo_url, *_ga(unit["members"][0]), target) + ".pom")
            if u["age_days"] is not None and u["age_days"] < ctx["cooldown"]:
                u["reasons"].append(f"released {u['age_days']} day(s) ago (cooldown {ctx['cooldown']})")
    u["needs"] = gaps
    u["reasons"] += [g["msg"] for g in gaps]

    checked = False
    if gaps:
        # the latest doesn't fit the project: highest version that does
        u["alternative"], checked = step_down(ctx, unit, versions, cur, target, repo_url, kind), True
    elif level == "major" or multi or kind == "agp":
        # fits, but it's a big jump (or the toolchain): the smallest step is the cautious option
        u["alternative"] = cautious_step(versions, cur, target)
    elif vulns and fix and vkey(fix) < vkey(target):
        u["alternative"] = fix  # the smallest step that closes the advisories
    if u["alternative"] and (vulns or (native and native[1])):
        # an alternative must not keep what makes the current version a problem
        alt = u["alternative"]
        if (vulns and risk.vulnerabilities(net, lib_coords, alt)) or \
                (native and native[1] and risk.misaligned_16k(aar_bytes(net, repo_url, unit["members"][0], alt))):
            u["alternative"] = fix if vulns and fix and vkey(fix) < vkey(target) and vkey(fix) > vkey(alt) else None
            checked = False  # a replacement hasn't been checked for gaps
    if native and native[1] and not u["alternative"]:
        u["alternative"], checked = aligned_step(net, repo_url, unit["members"][0], versions, cur, target), False
    if u["alternative"] == target:
        u["alternative"] = None

    if u["tier"] != "decision":
        risky = u["own_confirmation"] or kind == "ksp" or level == "major" or gaps or holds or multi or \
            target_vulns or bad_target or hand or not is_stable(target) or \
            (u["age_days"] is not None and u["age_days"] < ctx["cooldown"])
        u["tier"] = "care" if risky else "safe"
    u["flags"] = sorted(set(u["flags"]))
    if kind not in ("agp", "kotlin", "ksp") and u["tier"] == "care" and gaps:
        u["reasons"].append("coupled to the compileSdk/AGP/Kotlin bump it needs")
    if u["tier"] == "care" and u["alternative"] and kind not in ("agp", "kotlin", "ksp") and not holds:
        u["sibling"] = safe_sibling(ctx, unit, u, repo_url, checked)
    return u


def fix_version(versions, cur, vulns):
    """The lowest stable version at or above the first fix of every advisory, or None."""
    needed, above = [], []
    for v in vulns:
        later = sorted((f for f in v["fixed"] if vkey(f) > vkey(cur)), key=vkey)
        if later:
            needed.append(later[0])
        elif v.get("last_affected"):
            above.append(max(v["last_affected"], key=vkey))  # any release after it
        else:
            return None
    ok = [v for v in versions if is_stable(v)
          and all(vkey(v) >= vkey(f) for f in needed) and all(vkey(v) > vkey(a) for a in above)]
    return min(ok, key=vkey) if ok else None


def aar_bytes(net, repo_url, m, version):
    if not repo_url or m["section"] != "libraries":
        return None
    req = aar_requirements(net, repo_url, m["group"], m["name"], version)
    art = (req or {}).get("artifact", m["name"])
    return net.get(artifact(repo_url, m["group"], art, version) + ".aar")


def native_16k(net, repo_url, m, target, cur):
    """(misaligned in target, misaligned in current), or None when the target ships no
    64-bit native code. The current AAR is only downloaded when there is native code."""
    bad_target = risk.misaligned_16k(aar_bytes(net, repo_url, m, target))
    if bad_target is None:
        return None
    return bad_target, risk.misaligned_16k(aar_bytes(net, repo_url, m, cur)) or []


def aligned_step(net, repo_url, m, versions, cur, target):
    """The first minor line (its latest patch) below the target whose 64-bit native
    libraries are 16 KB aligned: the smallest step that meets the Play requirement."""
    by_minor = {}
    for v in stable_between(versions, cur, target):  # newest first: keeps each minor's latest patch
        by_minor.setdefault(tuple(nums(v)[:2]), v)
    for line in sorted(by_minor)[:MAX_STEPDOWN_PROBES]:
        v = by_minor[line]
        if v == target:
            break
        if not risk.misaligned_16k(aar_bytes(net, repo_url, m, v)):
            return v
    return None


def lib_names(paths, limit=3):
    names = sorted({p.rsplit("/", 1)[-1] for p in paths})
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")


def notes_url(net, repo_url, unit, version):
    """Release notes of the target: fixed pages for the toolchain and AndroidX, else the
    project URL in the POM (for a plugin, the POM of the artifact its marker points to)."""
    m, kind = unit["members"][0], unit["kind"]
    if m["section"] == "libraries":
        group, name = m["group"], m["name"]
    else:
        group, name = m["id"], None
    known = risk.release_notes(kind, group, name)
    if known or not repo_url:
        return known
    pom = net.get(artifact(repo_url, *_ga(m), version) + ".pom")
    if m["section"] == "plugins":
        impl = next(iter(pom_deps(net, repo_url, *_ga(m), version) or []), None)
        pom = net.get(artifact(repo_url, impl.get("groupId"), impl.get("artifactId"), impl.get("version")) + ".pom") \
            if impl and impl.get("groupId") and impl.get("version") else None
    parent = risk.pom_parent(pom) if not risk.pom_url(pom) else None
    if parent:  # e.g. gson declares its URL in gson-parent
        pom = net.get(artifact(repo_url, *parent) + ".pom")
    return risk.release_notes(kind, group, name, pom)


def safe_sibling(ctx, unit, u, repo_url, checked):
    """The alternative as its own "safe" row when it is low-risk on its own: no gaps,
    not a big jump, out of the cooldown. The care row keeps the latest."""
    cur, alt, kind = unit["current"], u["alternative"], unit["kind"]
    level, size = delta(cur, alt)
    if level == "major" or (level == "minor" and size >= MULTI_MINOR and kind != "bom") or ctx["offline"]:
        return None
    if not checked:
        if kind == "bom":
            gaps, _ = bom_gaps(ctx["net"], ctx["base"], unit, alt, ctx["children"], repo_url)
        else:
            gaps = library_gaps(ctx["net"], ctx["base"], unit["members"][0], alt, repo_url)
        if gaps:
            return None
    age = ctx["net"].age_days(artifact(repo_url, *_ga(unit["members"][0]), alt) + ".pom") if repo_url else None
    if age is not None and age < ctx["cooldown"]:
        return None
    reasons, flags = [f"compatible step; the latest ({u['target']}) is under 'Handle with care'"], []
    if u.get("vulns"):
        reasons.append(f"closes {', '.join(u['vulns'])}")
        flags.append("security")
    if u.get("fixes_16k"):  # the alternative was checked to be aligned in analyse()
        reasons.append("its native libraries are 16 KB aligned")
        flags.append("16kb")
    return {"key": unit["key"], "kind": kind, "current": cur, "target": alt, "tier": "safe",
            "own_confirmation": False, "members_view": u["members_view"], "alternative": None,
            "reasons": reasons, "moves": [], "age_days": age, "needs": [], "flags": flags,
            "notes_url": u.get("notes_url")}


def _ga(m):
    if m["section"] == "libraries":
        return m["group"], m["name"]
    return m["id"], f"{m['id']}.gradle.plugin"


def cautious_step(versions, cur, target):
    """Latest patch of the current minor, else the latest release of the next minor."""
    c = nums(cur)
    newer = stable_between(versions, cur, target)
    same_minor = [v for v in newer if nums(v)[:2] == c[:2]]
    if same_minor:
        return same_minor[0]
    next_minor = [v for v in newer if nums(v)[0] == c[0] and nums(v)[1] == c[1] + 1]
    return next_minor[0] if next_minor else None


def step_down(ctx, unit, versions, cur, target, repo_url, kind):
    """Highest stable version below target that is not a major jump and has no gaps."""
    if ctx["offline"] or not versions:
        return None
    probes = 0
    for v in stable_between(versions, cur, target):
        if v == target or delta(cur, v)[0] == "major":
            continue
        if probes >= MAX_STEPDOWN_PROBES:
            break
        probes += 1
        if kind == "bom":
            gaps, _ = bom_gaps(ctx["net"], ctx["base"], unit, v, ctx["children"], repo_url)
        else:
            gaps = library_gaps(ctx["net"], ctx["base"], unit["members"][0], v, repo_url)
        if not gaps:
            return v
    return None


def gradle_age(build_time):
    """Days since a services.gradle.org `buildTime` (e.g. 20260918101500+0000)."""
    try:
        return (datetime.now(timezone.utc) - datetime.strptime(build_time, "%Y%m%d%H%M%S%z")).days
    except (TypeError, ValueError):
        return None


def wrapper_item(repo, report, net, cooldown=COOLDOWN_DAYS):
    cur = wrapper_version(repo)
    if not cur:
        return None
    # services.gradle.org is authoritative (a report can be stale); the report is the offline fallback
    target, age = None, None
    if not net.offline:
        data = net.get(GRADLE_CURRENT)
        if data:
            try:
                info = json.loads(data)
                target, age = info.get("version"), gradle_age(info.get("buildTime"))
            except json.JSONDecodeError:
                target = None
    if target is None:
        target = ((report or {}).get("gradle") or {}).get("available")
    if not target or vkey(target) <= vkey(cur):
        return None
    reasons = ["Gradle wrapper: own explicit confirmation; upgrade with the wrapper task + "
               f"--gradle-distribution-sha256-sum (https://services.gradle.org/distributions/gradle-{target}-bin.zip.sha256), run it twice"]
    if age is not None and age < cooldown:
        reasons.append(f"released {age} day(s) ago (cooldown {cooldown})")
    return {"key": "gradle-wrapper", "kind": "wrapper", "current": cur, "target": target, "tier": "care",
            "own_confirmation": True, "members_view": ["gradle/wrapper/gradle-wrapper.properties"],
            "reasons": reasons, "alternative": None, "moves": [], "age_days": age, "needs": [], "flags": [],
            "notes_url": f"https://docs.gradle.org/{target}/release-notes.html"}


def agp_notes(net, agp, target, kotlin):
    """What an AGP bump drags along: its bundled Kotlin, and the Gradle/Studio minimums."""
    notes = []
    before, after = bundled_kgp(net, agp), bundled_kgp(net, target)
    if before and after and before != after:
        raised = f", raising the effective Kotlin from {kotlin}" if kotlin and vkey(after) > vkey(kotlin) else ""
        notes.append(f"bundled Kotlin Gradle plugin {before} -> {after}{raised}")
    elif after:
        notes.append(f"bundled Kotlin Gradle plugin unchanged ({after})")
    notes.append("check the minimum Gradle and Android Studio versions for AGP "
                 f"{target}: https://developer.android.com/build/releases/gradle-plugin")
    return notes


def project_baseline(texts, cat, units, refs, net, kotlin_arg=None, compile_sdk_arg=None, offline=False,
                     warnings=None):
    """(base, compileSdk sources, Kotlin source): the compileSdk, AGP and effective
    Kotlin that requirements are checked against."""
    warnings = [] if warnings is None else warnings
    sdk, sdk_minor, sdk_sources = detect_compile_sdk(texts, cat)
    agp = next((u["current"] for u in units.values() if u["kind"] == "agp"), None)
    kotlin_unit = next((u for u in units.values() if u["kind"] == "kotlin"), None)
    kotlin, kotlin_source = (kotlin_unit or {}).get("current"), "catalog"
    if kotlin_arg:
        kotlin, kotlin_source = kotlin_arg, "--kotlin"
    elif agp and nums(agp)[0] >= 9:
        # built-in Kotlin: AGP's own KGP, unless the build applies or puts a newer one on the classpath
        bundled = None if offline else bundled_kgp(net, agp)
        if bundled:
            kotlin, kotlin_source = bundled, f"bundled with AGP {agp}"
            if kotlin_unit and usage(kotlin_unit, refs) == "used" and vkey(kotlin_unit["current"]) > vkey(bundled):
                kotlin, kotlin_source = kotlin_unit["current"], "catalog (applied, above AGP's bundled KGP)"
        warnings.append(f"AGP 9 built-in Kotlin: effective Kotlin {'inferred' if bundled else 'unknown (catalog used)'}; "
                        "confirm it with ./gradlew buildEnvironment (the org.jetbrains.kotlin:kotlin-gradle-plugin "
                        "line) and pass --kotlin")
    base = {"compileSdk": compile_sdk_arg or sdk, "compileSdkMinor": sdk_minor, "agp": agp, "kotlin": kotlin}
    if base["compileSdk"] is None:
        warnings.append("compileSdk not found: pass --compile-sdk to check AAR requirements")
    return base, sdk_sources, kotlin_source


def cross_unit_rules(items, units, base, net, offline=False):
    """Rules between items: Kotlin waits for an old-scheme KSP; how Kotlin moves on
    AGP 9; what an AGP bump drags along."""
    agp, kotlin = base["agp"], base["kotlin"]
    agp9 = bool(agp) and nums(agp)[0] >= 9
    old_ksp = any(OLD_KSP.match(u["current"] or "") for u in units.values() if u["kind"] == "ksp")
    for i in items:
        if i["kind"] == "kotlin" and i.get("target"):
            if old_ksp:
                i["reasons"].append("blocked until KSP leaves the old <kotlin>-<ksp> scheme")
            if agp9:
                i["reasons"].append("AGP 9 built-in Kotlin: move it on the top-level buildscript classpath "
                                    "(references/reference.md, Kotlin row)")
        if i["kind"] == "agp" and i.get("target") and i["tier"] != "safe" and not offline:
            i["reasons"] += agp_notes(net, agp, i["target"], kotlin)


def map_lint(findings, units, cat, catalog):
    """Lint findings per unit key: by line when lint points at the catalog (it reports
    on the entry), else by a group:name coordinate in the message."""
    by_line, by_coord = {}, {}
    for key, u in units.items():
        if key in cat["versions"]:
            by_line[cat["versions"][key]["line"]] = key
        for m in u["members"]:
            by_line[m["line"]] = key
            if m["section"] == "libraries":
                by_coord[m["coord"]] = key
    mapped, rest = {}, []
    for f in findings:
        key = by_line.get(f["line"]) if f["file"].replace("\\", "/").endswith(catalog.name) else None
        key = key or next((by_coord[c] for c in sorted(risk.lint_coords(f["message"])) if c in by_coord), None)
        if key:
            if f["message"] not in {x["message"] for x in mapped.get(key, [])}:
                mapped.setdefault(key, []).append(f)
        else:
            rest.append(f)
    return mapped, rest


def requirement_items(items, base, sdk_sources, warnings):
    """Requirements the proposed versions share. A compileSdk raise becomes its own
    decision; AGP and Kotlin requirements are attached to those items."""
    needs = {}
    for i in items:
        if i["tier"] == "safe":
            continue
        for g in i.get("needs", []):
            needs.setdefault(g["kind"], {}).setdefault(g["value"], set()).add(i["key"])
    extra = []
    for kind, by_value in needs.items():
        top = max(by_value, key=vkey)
        users = sorted({k for keys in by_value.values() for k in keys})
        if kind == "compileSdk":
            extra.append({"key": "compileSdk", "kind": "sdk", "current": str(base["compileSdk"]), "target": top,
                          "tier": "decision", "own_confirmation": True, "members_view": sorted(sdk_sources) or ["build files"],
                          "reasons": [f"needed by {', '.join(users)} at their latest versions; raise it in every module "
                                      f"(install SDK platform {top}); targetSdk stays out of scope"],
                          "alternative": None, "moves": [], "age_days": None, "needs": []})
            continue
        host = next((i for i in items if i["kind"] == kind and i.get("target") and i["tier"] != "safe"), None)
        msg = f"needed at >= {top} by {', '.join(users)}" if kind == "agp" else \
            f"{', '.join(users)} pull kotlin-stdlib up to {top}"
        if host:
            host["reasons"].append(msg)
        else:
            warnings.append(f"{'AGP' if kind == 'agp' else 'Kotlin'}: {msg}")
    return extra


# ---------------------------------------------------------------- output

TIERS = (
    ("safe", "Safe"),
    ("care", "Handle with care"),
    ("decision", "Needs a decision (not a bump)"),
)


FLAG_LABELS = {"security": "**security**", "16kb": "**16 KB**", "sdk-index": "**Play SDK Index**"}


def to_markdown(plan):
    b = plan["baseline"]
    lines = [f"Baseline: AGP {b['agp'] or '?'}, compileSdk {b['compileSdk'] or '?'}, "
             f"Kotlin {b['kotlin'] or '?'} ({b['kotlinSource']}), source: {plan['source']}", ""]
    listed = {(i["key"], i["target"]) for i in plan["items"] if i["tier"] == "safe"}
    for tier, title in TIERS:
        items = [i for i in plan["items"] if i["tier"] == tier]
        if not items:
            continue
        lines += [f"### {title}", "", "| item | current → proposed | notes |", "|---|---|---|"]
        for i in items:
            arrow = f"{i['current']} → {i['target']}" if i.get("target") else (i["current"] or "?")
            notes = [FLAG_LABELS[f] for f in i.get("flags", [])] + list(i["reasons"])
            if i.get("own_confirmation"):
                notes.insert(0, "**own confirmation**")
            if i.get("alternative") and tier != "safe" and (i["key"], i["alternative"]) in listed:
                notes.append(f"compatible alternative {i['alternative']} is listed under Safe")
            elif i.get("alternative"):
                notes.append(f"cautious alternative: {i['alternative']}")
            if i.get("moves"):
                notes.append("moves " + ", ".join(i["moves"]))
            if i.get("notes_url"):
                notes.append(f"[release notes]({i['notes_url']})")
            lines.append(f"| `{i['key']}` ({', '.join(i['members_view'])}) | {arrow} | {'; '.join(notes) or '—'} |")
        lines.append("")
    if plan["not_in_catalog"]:
        lines += ["### Outside the catalog (inline in build files or transitive)", ""]
        lines += [f"- {e['coordinate']}: {e['current']} → {e['latest']}" for e in plan["not_in_catalog"]]
        lines.append("")
    if plan["before_applying"]:
        lines += ["### Before applying", ""] + [f"- {n}" for n in plan["before_applying"]] + [""]
    if plan["warnings"]:
        lines += ["### Warnings", ""] + [f"- {w}" for w in plan["warnings"]]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Draft an update proposal for an Android version catalog.")
    p.add_argument("repo", nargs="?", default=".", help="Android project root (default: .)")
    p.add_argument("--source", choices=("auto", "report", "metadata"), default="auto",
                   help="candidates from the ben-manes report or Maven metadata (auto: report if present)")
    p.add_argument("--kotlin", help="effective Kotlin version (from ./gradlew buildEnvironment)")
    p.add_argument("--compile-sdk", type=int, help="override the detected compileSdk")
    p.add_argument("--cooldown-days", type=int, default=COOLDOWN_DAYS)
    p.add_argument("--offline", action="store_true", help="no network: skip metadata, AAR and age checks")
    p.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    repo = Path(args.repo).resolve()
    catalog = find_catalog(repo)
    if not catalog:
        print(f"No version catalog (*.versions.toml) under {repo}.", file=sys.stderr)
        return 1
    cat = parse_catalog(catalog)
    units = build_units(cat)
    children = governed_children(cat)
    net = Net(offline=args.offline)

    report = report_data(repo, catalog) if args.source in ("auto", "report") else None
    if args.source == "report" and report is None:
        print("No dependencyUpdates report found; run detection first or use --source metadata.", file=sys.stderr)
        return 1
    source = "report" if report else "metadata"
    warnings = []
    if report and report["stale"]:
        warnings.append("the report is older than the catalog: its stale entries were ignored; rerun "
                        "detection for a fresh report")
    if args.offline and source == "metadata":
        warnings.append("offline without a report: no candidates can be computed")

    texts = build_texts(repo)
    refs = scan_references(texts, cat, catalog.name.split(".versions.toml")[0] or "libs")
    if refs["dynamic"]:
        warnings.append("the build looks catalog entries up dynamically (findLibrary(variable)): unused "
                        "entries are only detected from a fresh report")
    elif source == "metadata" or report["stale"]:
        warnings.append("unused entries come from a static scan of the build files (no fresh report): "
                        "confirm before removing any")
    base, sdk_sources, kotlin_source = project_baseline(texts, cat, units, refs, net, args.kotlin,
                                                        args.compile_sdk, args.offline, warnings)
    agp, kotlin = base["agp"], base["kotlin"]

    findings, lint_time = risk.lint_findings(repo)
    lint, unmapped = map_lint(findings, units, cat, catalog)
    if lint_time and catalog.stat().st_mtime > lint_time:
        warnings.append("the lint report is older than the catalog: its Play SDK Index findings may be stale")
    warnings += [f"lint {f['id']} outside the catalog: {f['message']} ({f['report']})" for f in unmapped]

    ctx = {"net": net, "base": base, "report": report, "children": children, "refs": refs, "lint": lint,
           "cooldown": args.cooldown_days, "offline": args.offline}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        items = list(pool.map(lambda u: analyse(u, ctx), units.values()))
    items = [i for i in items if i["tier"] != "current"]
    items += [i["sibling"] for i in items if i.get("sibling")]

    cross_unit_rules(items, units, base, net, args.offline)
    items += requirement_items(items, base, sdk_sources, warnings)
    w = wrapper_item(repo, report, net, args.cooldown_days)
    if w:
        items.append(w)
    for i in items:
        i["flags"] = sorted(set(i.get("flags", [])))
    order = {"safe": 0, "care": 1, "decision": 2}
    # within a tier: toolchain first, then security / Play findings, then by key
    items.sort(key=lambda i: (order[i["tier"]], not i.get("own_confirmation"), not i["flags"], i["key"]))

    not_in_catalog = []
    if report:
        known = {member_coord(m) for u in units.values() for m in u["members"]}
        known |= {c["coord"] for c in children.values()}
        not_in_catalog = [] if report["stale"] else [
            e for e in report["outdated"]
            if e["category"] in ("library", "plugin") and e["coordinate"] not in known]
        warnings += [f"skipped configuration: {s}" for s in report.get("skipped", [])]

    for i in items:
        for k in ("members", "comments", "rich", "sibling", "fixes_16k"):
            i.pop(k, None)
    plan = {
        "catalog": str(catalog), "source": source,
        "baseline": dict(base, kotlinSource=kotlin_source, compileSdkSources=sdk_sources),
        "items": items,
        "governed": children,
        "not_in_catalog": not_in_catalog,
        "before_applying": risk.build_integrity(repo),
        "warnings": warnings,
    }
    if args.json:
        json.dump(plan, sys.stdout, indent=2, default=str)
        print()
    else:
        print(to_markdown(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
