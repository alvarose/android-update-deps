"""Offline tests for skills/android-update-deps/scripts/risk.py.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""
import tempfile
import unittest
from pathlib import Path

from test_plan import aar, elf64, osv_answer, plan

risk = plan.risk


class Net:
    def __init__(self, answers=None):
        self.answers = answers or {}

    def post_json(self, url, payload):
        return self.answers.get((payload["package"]["name"], payload["version"]), {})


class VulnerabilityTests(unittest.TestCase):
    def test_osv_parsing_and_union(self):
        net = Net({("androidx.core:core-ktx", "1.0"): osv_answer("GHSA-a", "1.1"),
                   ("androidx.core:core", "1.0"): osv_answer("GHSA-b", "1.2")})
        vulns = risk.vulnerabilities(net, ["androidx.core:core-ktx", "androidx.core:core"], "1.0")
        self.assertEqual([v["id"] for v in vulns], ["GHSA-a", "GHSA-b"])
        self.assertEqual(vulns[0]["cve"], ["CVE-2022-0001"])
        self.assertEqual(vulns[0]["fixed"], ["1.1"])
        self.assertEqual(risk.describe(vulns), "GHSA-a (HIGH; CVE-2022-0001), GHSA-b (HIGH; CVE-2022-0001)")
        self.assertEqual(risk.vulnerabilities(net, ["androidx.core:core-ktx"], "1.1"), [])

    def test_fix_version(self):
        vulns = [{"fixed": ["2.8.9"]}, {"fixed": ["2.6.0", "2.9.1"]}]
        versions = ["2.8.8", "2.8.9", "2.9.0", "2.9.1", "2.10-rc1", "2.10"]
        self.assertEqual(plan.fix_version(versions, "2.8.8", vulns), "2.9.1")
        self.assertIsNone(plan.fix_version(versions, "2.8.8", [{"fixed": []}]))  # no fix at all


class AlignmentTests(unittest.TestCase):
    def test_elf_load_alignment(self):
        self.assertEqual(risk._load_alignment(elf64(16384)), 16384)
        self.assertEqual(risk._load_alignment(elf64(4096)), 4096)
        self.assertIsNone(risk._load_alignment(b"not an elf"))

    def test_only_64_bit_abis_count(self):
        self.assertEqual(risk.misaligned_16k(aar(35, native=4096)), ["jni/arm64-v8a/libnative.so"])
        self.assertEqual(risk.misaligned_16k(aar(35, native=16384)), [])  # the 4 KB armeabi-v7a lib is ignored
        self.assertIsNone(risk.misaligned_16k(aar(35)))  # no native code: nothing to check
        self.assertIsNone(risk.misaligned_16k(None))


class LintTests(unittest.TestCase):
    def test_lint_findings(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<issues format="6" by="lint 9.4.1">
  <issue id="PlaySdkIndexNonCompliant" severity="Error" message="com.example:sdk version 1.0 has policy issues">
    <location file="C:\\proj\\gradle\\libs.versions.toml" line="12" column="1"/>
  </issue>
  <issue id="Aligned16KB" severity="Warning" message="The native library arm64-v8a/libfoo.so is not 16 KB aligned">
    <location file="C:\\proj\\app\\build.gradle.kts" line="40"/>
  </issue>
  <issue id="ObsoleteSdkInt" severity="Warning" message="unrelated"/>
</issues>"""
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "app" / "build" / "reports" / "lint-results-debug.xml"
            report.parent.mkdir(parents=True)
            report.write_text(xml, encoding="utf-8")
            findings, newest = risk.lint_findings(Path(tmp))
        self.assertEqual([f["id"] for f in findings], ["PlaySdkIndexNonCompliant", "Aligned16KB"])
        self.assertEqual(findings[0]["line"], 12)
        self.assertEqual(findings[0]["report"], "app/build/reports/lint-results-debug.xml")
        self.assertIsNotNone(newest)
        self.assertEqual(risk.lint_coords(findings[0]["message"]), {"com.example:sdk"})


class ReleaseNotesTests(unittest.TestCase):
    def test_known_pages(self):
        self.assertEqual(risk.release_notes("library", "androidx.compose.material3", "material3"),
                         "https://developer.android.com/jetpack/androidx/releases/compose-material3")
        self.assertEqual(risk.release_notes("bom", "androidx.compose", "compose-bom"),
                         "https://developer.android.com/develop/ui/compose/bom/bom-mapping")
        self.assertTrue(risk.release_notes("ksp", "com.google.devtools.ksp", None).endswith("google/ksp/releases"))

    def test_from_the_pom(self):
        pom = b"""<project xmlns="http://maven.apache.org/POM/4.0.0"><url>https://square.github.io/okhttp/</url>
            <scm><url>scm:git:git@github.com:square/okhttp.git</url></scm></project>"""
        self.assertEqual(risk.release_notes("library", "com.squareup.okhttp3", "okhttp", pom),
                         "https://github.com/square/okhttp/releases")
        site = b"<project><url>https://example.org/lib</url></project>"
        self.assertEqual(risk.release_notes("library", "org.example", "lib", site), "https://example.org/lib")
        self.assertIsNone(risk.release_notes("library", "org.example", "lib", None))

    def test_parent_pom(self):
        pom = b"""<project><parent><groupId>com.google.code.gson</groupId><artifactId>gson-parent</artifactId>
            <version>2.8.9</version></parent></project>"""
        self.assertIsNone(risk.pom_url(pom))
        self.assertEqual(risk.pom_parent(pom), ("com.google.code.gson", "gson-parent", "2.8.9"))


class RepositoryTests(unittest.TestCase):
    def test_build_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.assertEqual(risk.build_integrity(repo), [])
            (repo / "gradle").mkdir()
            (repo / "gradle" / "verification-metadata.xml").write_text("<x/>", encoding="utf-8")
            (repo / "app").mkdir()
            (repo / "app" / "gradle.lockfile").write_text("", encoding="utf-8")
            (repo / "renovate.json").write_text("{}", encoding="utf-8")
            (repo / ".github").mkdir()
            (repo / ".github" / "dependabot.yml").write_text(
                "version: 2\nupdates:\n  - package-ecosystem: gradle\n", encoding="utf-8")
            notes = " | ".join(risk.build_integrity(repo))
        self.assertIn("--write-verification-metadata sha256", notes)
        self.assertIn("1 lockfile(s)", notes)
        self.assertIn("renovate.json, .github/dependabot.yml", notes)

    def test_handoffs(self):
        self.assertIn("agp-9-upgrade", risk.handoff("agp", None, 8, 9))
        self.assertIsNone(risk.handoff("agp", None, 9, 9))
        self.assertIn("play-billing", risk.handoff("library", "com.android.billingclient:billing-ktx", 7, 8))
        self.assertIsNone(risk.handoff("library", "com.android.billingclient:billing-ktx", 8, 8))


if __name__ == "__main__":
    unittest.main()
