"""Repository checks: the version is the same everywhere, and the planner runs end
to end on the committed fixture (offline, so the result is deterministic).

Run from the repo root:  python3 -m unittest discover -s tests -v
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "android-update-deps"
FIXTURE = ROOT / "evals" / "fixtures" / "android-catalog-fixture"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(main, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(argv)
    return code, out.getvalue()


class VersionTests(unittest.TestCase):
    """plugin.json, marketplace.json, SKILL.md and the CHANGELOG move together."""

    def versions(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        skill = re.search(r'^\s+version:\s*"?([^"\s]+)"?\s*$', (SKILL / "SKILL.md").read_text(encoding="utf-8"), re.M)
        changelog = re.search(r"^## \[(\d[^\]]*)\]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
        entry = next(p for p in market["plugins"] if p["name"] == plugin["name"])
        return {"plugin.json": plugin["version"], "marketplace.json": entry["version"],
                "SKILL.md metadata.version": skill and skill.group(1),
                "CHANGELOG.md latest release": changelog and changelog.group(1)}

    def test_versions_match(self):
        found = self.versions()
        self.assertEqual(len(set(found.values())), 1, found)

    def test_tag_matches_version(self):
        if os.environ.get("GITHUB_REF_TYPE") != "tag":
            self.skipTest("not a tag build")
        self.assertEqual(os.environ["GITHUB_REF_NAME"], "v" + self.versions()["plugin.json"])


class FixtureTests(unittest.TestCase):
    """The planner on evals/fixtures/android-catalog-fixture without network."""

    @classmethod
    def setUpClass(cls):
        plan = load("plan", SKILL / "scripts" / "plan.py")
        code, out = run(plan.main, [str(FIXTURE), "--offline", "--json"])
        cls.code, cls.plan = code, json.loads(out)
        cls.items = {i["key"]: i for i in cls.plan["items"]}

    def test_exit_code_and_baseline(self):
        self.assertEqual(self.code, 0)
        self.assertEqual(self.plan["baseline"]["agp"], "9.2.1")
        self.assertEqual(self.plan["baseline"]["compileSdk"], 36)

    def test_unused_entries_from_the_build_files(self):
        self.assertIn("apply false", " ".join(self.items["ksp"]["reasons"]))
        self.assertIn("not referenced", " ".join(self.items["libraries.android-pdfview"]["reasons"]))
        self.assertIn("built-in Kotlin", " ".join(self.items["kotlin"]["reasons"]))
        for key in ("ksp", "libraries.android-pdfview", "kotlin"):
            self.assertEqual(self.items[key]["tier"], "decision", key)

    def test_used_entries_are_not_flagged(self):
        for key in ("coreKtx", "composeBom", "gson", "agp"):
            self.assertNotIn(key, self.items, key)  # offline: no candidates, and not unused

    def test_offline_warnings(self):
        text = " ".join(self.plan["warnings"])
        self.assertIn("offline", text)
        self.assertIn("static scan", text)


class AggregateTests(unittest.TestCase):
    """scripts/aggregate-updates.py on a synthetic ben-manes report."""

    @classmethod
    def setUpClass(cls):
        cls.agg = load("aggregate_updates", SKILL / "scripts" / "aggregate-updates.py")

    def report(self, outdated=(), unresolved=(), gradle=None, skipped=None):
        data = {"outdated": {"dependencies": list(outdated)}, "unresolved": {"dependencies": list(unresolved)}}
        if gradle:
            data["gradle"] = gradle
        if skipped:
            data["skipped"] = {"count": len(skipped), "configurations": skipped}
        return json.dumps(data)

    def write(self, root, rel, text):
        path = root / rel / "build" / "dependencyUpdates" / "report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_collect(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, ".", self.report(
                outdated=[
                    {"group": "androidx.core", "name": "core-ktx", "version": "1.16.0",
                     "available": {"milestone": "1.19.1", "patch": "1.16.1", "minor": "1.18.0"}},
                    {"group": "org.jetbrains.kotlin", "name": "kotlin-stdlib", "version": "2.2.10",
                     "available": {"milestone": "2.4.20"}},
                    {"group": "com.android.tools.lint", "name": "lint-gradle", "version": "32.2.1",
                     "available": {"milestone": "32.4.1"}},
                    {"group": "com.example", "name": "pre", "version": "1.0", "available": {"preRelease": "2.0-rc1"}},
                ],
                unresolved=[{"group": "com.github.barteksc", "name": "AndroidPdfViewer", "version": "3.2.0-beta.1"}],
                gradle={"running": {"version": "9.6.1"}, "current": {"version": "9.8.0", "isUpdateAvailable": True}},
                skipped=[":app:lintChecks"]))
            # an older per-project report: the root's entry wins, new coordinates are added
            self.write(root, "app", self.report(outdated=[
                {"group": "androidx.core", "name": "core-ktx", "version": "1.10.0", "available": {"release": "1.17.0"}},
                {"group": "com.google.code.gson", "name": "gson", "version": "2.10", "available": {"release": "2.14.0"}}]))
            self.write(root, "build/dependencyUpdates/partials/x", self.report())  # ignored

            reports = self.agg.find_reports(root)
            self.assertEqual(len(reports), 2)
            result = self.agg.collect(reports)
            by = {e["coordinate"]: e for e in result["outdated"]}
            self.assertEqual(by["androidx.core:core-ktx"]["latest"], "1.19.1")
            self.assertEqual(by["androidx.core:core-ktx"]["minor"], "1.18.0")
            self.assertEqual(by["com.google.code.gson:gson"]["latest"], "2.14.0")
            self.assertEqual(by["org.jetbrains.kotlin:kotlin-stdlib"]["category"], "kotlin-toolchain")
            self.assertEqual(by["com.android.tools.lint:lint-gradle"]["category"], "agp-internal")
            self.assertNotIn("com.example:pre", by)  # pre-release candidates are ignored
            self.assertEqual(result["jitpack"][0]["coordinate"], "com.github.barteksc:AndroidPdfViewer")
            self.assertEqual(result["gradle"], {"current": "9.6.1", "available": "9.8.0"})
            self.assertEqual(result["skipped"], [":app:lintChecks"])

            code, out = run(self.agg.main, [str(root), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(out)["reports"]), 2)

    def test_no_report(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run(self.agg.main, [tmp])[0], 1)


if __name__ == "__main__":
    unittest.main()
