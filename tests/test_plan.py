"""Offline tests for skills/android-update-deps/scripts/plan.py.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""
import importlib.util
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "android-update-deps" / "scripts"
spec = importlib.util.spec_from_file_location("plan", SCRIPTS / "plan.py")
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)

CATALOG = '''\
[versions]
agp = "9.2.1"
kotlin = "2.2.10"
ksp = "2.2.10-2.0.2"
# held: 1.17 breaks our insets handling (#123)
coreKtx = "1.16.0"
composeBom = "2026.06.00"
gson = "2.10"  # trailing note with a "#" inside quotes is fine
retrofit = "3.0.0"
strict = { strictly = "1.0" }

[libraries]
androidx-core-ktx = { module = "androidx.core:core-ktx", version.ref = "coreKtx" }
compose-bom = { group = "androidx.compose", name = "compose-bom", version.ref = "composeBom" }
compose-ui = { module = "androidx.compose.ui:ui" }
gson = "com.google.code.gson:gson:2.10"
retrofit = { module = "com.squareup.retrofit2:retrofit", version.ref = "retrofit" }
retrofit-gson = { module = "com.squareup.retrofit2:converter-gson", version.ref = "retrofit" }
strict-lib = { module = "org.example:strict", version.ref = "strict" }

[bundles]
net = [
    "retrofit",
    "retrofit-gson",
]

[plugins]
android-application = { id = "com.android.application", version.ref = "agp" }
kotlin-android = { id = "org.jetbrains.kotlin.android", version.ref = "kotlin" }
ksp = "com.google.devtools.ksp:2.2.10-2.0.2"
'''


def metadata_xml(*versions):
    body = "".join(f"<version>{v}</version>" for v in versions)
    return f"<metadata><versioning><versions>{body}</versions></versioning></metadata>".encode()


def aar(min_sdk, min_agp="8.0.0"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/com/android/build/gradle/aar-metadata.properties",
                   f"minCompileSdk={min_sdk}\nminAndroidGradlePluginVersion={min_agp}\n")
    return buf.getvalue()


class FakeNet(plan.Net):
    def __init__(self, responses, age=30):
        super().__init__(offline=False)
        self.responses, self.age = responses, age

    def get(self, url):
        return self.responses.get(url)

    def age_days(self, url):
        return self.age


def md(base, group, name):
    return f"{base}/{group.replace('.', '/')}/{name}/maven-metadata.xml"


def art(base, group, name, version, ext):
    return f"{base}/{group.replace('.', '/')}/{name}/{version}/{name}-{version}.{ext}"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "libs.versions.toml"
        self.path.write_text(CATALOG, encoding="utf-8")
        self.cat = plan.parse_catalog(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sections_and_notations(self):
        libs, plugins, versions = self.cat["libraries"], self.cat["plugins"], self.cat["versions"]
        self.assertEqual(libs["compose-bom"]["group"], "androidx.compose")
        self.assertEqual(libs["gson"]["value"], "2.10")  # string notation
        self.assertIsNone(libs["compose-ui"]["value"])
        self.assertIsNone(libs["compose-ui"]["ref"])
        self.assertEqual(plugins["ksp"]["value"], "2.2.10-2.0.2")
        self.assertTrue(versions["strict"]["rich"])
        self.assertNotIn("net", libs)
        self.assertEqual(self.cat["bundles"]["net"], ["retrofit", "retrofit-gson"])  # multi-line array

    def test_comments_attach_to_entries(self):
        self.assertIn("held: 1.17 breaks our insets handling (#123)", self.cat["versions"]["coreKtx"]["comments"])
        self.assertTrue(self.cat["versions"]["gson"]["comments"][0].startswith("trailing note"))

    def test_units_kinds_and_governed(self):
        units = plan.build_units(self.cat)
        self.assertEqual(units["agp"]["kind"], "agp")
        self.assertEqual(units["kotlin"]["kind"], "kotlin")
        self.assertEqual(units["plugins.ksp"]["kind"], "ksp")
        self.assertEqual(units["composeBom"]["kind"], "bom")
        self.assertEqual(len(units["retrofit"]["members"]), 2)  # coupled block
        governed = plan.governed_children(self.cat)
        self.assertEqual(governed["compose-ui"]["bom"], "compose-bom")

    def test_reference_scan(self):
        texts = {
            "build.gradle.kts": "plugins {\n    alias(libs.plugins.android.application) apply false\n"
                                "    alias(libs.plugins.ksp) apply false\n}\n",
            "app/build.gradle.kts": "plugins { alias(libs.plugins.android.application) }\n"
                                    "dependencies {\n    implementation(libs.bundles.net)\n"
                                    "    // implementation(libs.gson)\n}\n",
            "build-logic/src/main/kotlin/Conv.kt": 'add("implementation", libs.findLibrary("androidx-core-ktx").get())\n'
                                                   'pluginManager.apply("org.jetbrains.kotlin.android")\n',
        }
        refs = plan.scan_references(texts, self.cat)
        self.assertEqual(refs["libraries"], {"retrofit", "retrofit-gson", "androidx-core-ktx"})
        self.assertEqual(refs["applied"], {"android-application", "kotlin-android"})
        self.assertEqual(refs["declared"], {"ksp"})  # only behind apply false
        units = plan.build_units(self.cat)
        self.assertEqual(plan.usage(units["plugins.ksp"], refs), "declared")
        self.assertEqual(plan.usage(units["libraries.gson"], refs), "unused")  # commented out
        self.assertEqual(plan.usage(units["retrofit"], refs), "used")

    def test_reference_scan_needs_full_accessor(self):
        refs = plan.scan_references({"a.gradle.kts": "implementation(libs.androidx.core)\n"}, self.cat)
        self.assertNotIn("androidx-core-ktx", refs["libraries"])
        refs = plan.scan_references({"a.gradle.kts": "implementation(libs.androidx.core.ktx.get())\n"}, self.cat)
        self.assertIn("androidx-core-ktx", refs["libraries"])

    def test_dynamic_lookup_disables_the_scan(self):
        self.assertIsNone(plan.scan_references({"Conv.kt": "libs.findLibrary(alias).get()"}, self.cat))

    def test_compile_sdk_from_constant_and_minor(self):
        texts = {"build-logic/Sdk.kt": "const val APP_COMPILE_SDK = 36\n",
                 "app/build.gradle.kts": "android { compileSdk = APP_COMPILE_SDK }\n",
                 "lib/build.gradle.kts": "android { compileSdk { version = release(37) { minorApiLevel = 1 } } }\n"}
        sdk, minor, sources = plan.detect_compile_sdk(texts, self.cat)
        self.assertEqual((sdk, minor), (36, 1))
        self.assertIn("app/build.gradle.kts (APP_COMPILE_SDK)", sources)


class VersionTests(unittest.TestCase):
    def test_stability(self):
        for v in ("2.5.0-Beta1", "9.5.0-alpha08", "1.0-rc1", "2.0.0-M2", "1.0-SNAPSHOT"):
            self.assertFalse(plan.is_stable(v), v)
        for v in ("2.4.20", "2.2.10-2.0.2", "33.0.0-jre", "2026.09.00"):
            self.assertTrue(plan.is_stable(v), v)

    def test_delta_and_ordering(self):
        self.assertEqual(plan.delta("2.10", "2.14.0"), ("minor", 4))
        self.assertEqual(plan.delta("5.3.0", "6.0.2"), ("major", 1))
        self.assertGreater(plan.vkey("1.10.0"), plan.vkey("1.9.9"))

    def test_cautious_step(self):
        versions = ["2.10", "2.10.1", "2.11.0", "2.12.0", "2.14.0"]
        self.assertEqual(plan.cautious_step(versions, "2.10", "2.14.0"), "2.10.1")
        self.assertEqual(plan.cautious_step(["1.0.0", "1.1.0", "1.4.0"], "1.0.0", "1.4.0"), "1.1.0")

    def test_requirement_gaps(self):
        base = {"compileSdk": 36, "agp": "9.0.0", "kotlin": "2.2.10"}
        gaps = plan.requirement_gaps({"minCompileSdk": 37, "minAgp": "9.1.0"}, "2.4.0", base)
        self.assertEqual([g["kind"] for g in gaps], ["compileSdk", "agp", "kotlin"])
        self.assertEqual(plan.requirement_gaps({"minCompileSdk": 36, "minAgp": "8.9"}, "2.3.0", base), [])
        minor = plan.requirement_gaps({"minCompileSdk": 36, "minCompileMinorSdk": 1}, None, base)
        self.assertEqual(minor[0]["value"], "36.1")
        self.assertEqual(plan.requirement_gaps({"minCompileSdk": 36, "minCompileMinorSdk": 1}, None,
                                               dict(base, compileSdkMinor=1)), [])

    def test_gradle_age(self):
        self.assertIsNone(plan.gradle_age("not a date"))
        self.assertGreater(plan.gradle_age("20200101000000+0000"), 365)

    def test_hold_detection(self):
        for c in ("held at 1.16: breaks insets", "@pin", "do not upgrade until #12", "no actualizar"):
            self.assertTrue(plan.HOLD.search(c), c)
        for c in ("coupled with ksp", "keeps tests green", "placeholder"):
            self.assertFalse(plan.HOLD.search(c), c)


class AnalyseTests(unittest.TestCase):
    def unit(self, key="coreKtx", cur="1.16.0", comments=(), kind="library"):
        member = {"alias": "androidx-core-ktx", "section": "libraries", "group": "androidx.core",
                  "name": "core-ktx", "coord": "androidx.core:core-ktx"}
        return {"key": key, "current": cur, "rich": False, "comments": list(comments),
                "members": [member], "kind": kind}

    def ctx(self, net, report=None, refs=None):
        return {"net": net, "base": {"compileSdk": 36, "agp": "9.2.1", "kotlin": "2.2.10"},
                "report": report, "children": {}, "cooldown": 3, "offline": False, "refs": refs}

    def net(self, **extra):
        g = plan.GOOGLE
        responses = {
            md(g, "androidx.core", "core-ktx"): metadata_xml("1.16.0", "1.17.0", "1.18.0", "1.19.0-rc01", "1.19.1"),
            art(g, "androidx.core", "core-ktx", "1.19.1", "aar"): aar(37),
            art(g, "androidx.core", "core-ktx", "1.18.0", "aar"): aar(36),
            art(g, "androidx.core", "core-ktx", "1.17.0", "aar"): aar(35),
        }
        responses.update(extra)
        return FakeNet(responses)

    def test_latest_needs_higher_compile_sdk(self):
        u = plan.analyse(self.unit(), self.ctx(self.net()))
        self.assertEqual(u["target"], "1.19.1")
        self.assertEqual(u["tier"], "care")
        self.assertEqual(u["alternative"], "1.18.0")
        self.assertTrue(any("compileSdk 37" in r for r in u["reasons"]))

    def test_stale_report_entry_is_ignored(self):
        report = {"stale": False, "seen": {"androidx.core:core-ktx"},
                  "outdated": [{"coordinate": "androidx.core:core-ktx", "current": "1.10.0", "latest": "1.17.0"}]}
        u = plan.analyse(self.unit(), self.ctx(self.net(), report))
        self.assertEqual(u["target"], "1.19.1")  # from metadata, not the stale 1.17.0

    def test_unused_entry_needs_a_decision(self):
        report = {"stale": False, "seen": set(), "outdated": []}
        u = plan.analyse(self.unit(), self.ctx(self.net(), report))
        self.assertEqual(u["tier"], "decision")

    def test_hold_comment_makes_it_care(self):
        g = plan.GOOGLE
        net = self.net(**{art(g, "androidx.core", "core-ktx", "1.19.1", "aar"): aar(35)})
        u = plan.analyse(self.unit(comments=["held: breaks insets"]), self.ctx(net))
        self.assertEqual(u["tier"], "care")
        self.assertTrue(any(r.startswith("held") for r in u["reasons"]))

    def test_current_not_published(self):
        u = plan.analyse(self.unit(cur="1.16.5"), self.ctx(self.net()))
        self.assertEqual(u["tier"], "decision")

    def test_old_ksp_scheme(self):
        member = {"alias": "ksp", "section": "plugins", "id": "com.google.devtools.ksp"}
        unit = {"key": "ksp", "current": "2.2.10-2.0.2", "rich": False, "comments": [],
                "members": [member], "kind": "ksp"}
        name = "com.google.devtools.ksp.gradle.plugin"
        net = FakeNet({md(plan.PORTAL, "com.google.devtools.ksp", name): metadata_xml("2.2.10-2.0.2", "2.3.12")})
        u = plan.analyse(unit, self.ctx(net))
        self.assertEqual(u["target"], "2.3.12")
        self.assertEqual(u["tier"], "care")
        self.assertTrue(any("old <kotlin>-<ksp> scheme" in r for r in u["reasons"]))

        declared = {"libraries": set(), "applied": set(), "declared": {"ksp"}}
        u = plan.analyse(unit, self.ctx(net, refs=declared))
        self.assertEqual(u["tier"], "decision")  # apply false only: nothing to verify
        self.assertTrue(any("apply false" in r for r in u["reasons"]))
        self.assertTrue(any("old <kotlin>-<ksp> scheme" in r for r in u["reasons"]))
        self.assertFalse(any("codegen" in r for r in u["reasons"]))

    def test_compatible_alternative_gets_a_safe_row(self):
        u = plan.analyse(self.unit(), self.ctx(self.net()))
        self.assertEqual(u["needs"][0]["kind"], "compileSdk")
        self.assertEqual(u["sibling"]["target"], "1.18.0")
        self.assertEqual(u["sibling"]["tier"], "safe")

    def test_wrapped_artifact_requirements(self):
        g = plan.GOOGLE
        pom = ("<project><dependencies><dependency><groupId>androidx.core</groupId><artifactId>core</artifactId>"
               "<version>1.19.1</version><scope>compile</scope></dependency></dependencies></project>").encode()
        net = self.net(**{art(g, "androidx.core", "core-ktx", "1.19.1", "aar"): aar(35),
                          art(g, "androidx.core", "core-ktx", "1.19.1", "pom"): pom,
                          art(g, "androidx.core", "core", "1.19.1", "aar"): aar(37)})
        u = plan.analyse(self.unit(), self.ctx(net))
        self.assertTrue(any(r.startswith("core 1.19.1 needs compileSdk 37") for r in u["reasons"]))

    def test_unused_jitpack_entry_is_still_checked(self):
        member = {"alias": "pdf", "section": "libraries", "group": "com.github.barteksc",
                  "name": "AndroidPdfViewer", "coord": "com.github.barteksc:AndroidPdfViewer"}
        unit = {"key": "libraries.pdf", "current": "3.2.0-beta.1", "rich": False, "comments": [],
                "members": [member], "kind": "library"}
        net = FakeNet({md(plan.JITPACK, "com.github.barteksc", "AndroidPdfViewer"): metadata_xml("2.0.2", "3.1.0-beta.1")})
        refs = {"libraries": set(), "applied": set(), "declared": set()}
        u = plan.analyse(unit, self.ctx(net, refs=refs))
        self.assertEqual(u["tier"], "decision")
        text = " ".join(u["reasons"])
        self.assertIn("not referenced", text)
        self.assertIn("not published", text)
        self.assertIn("latest stable (2.0.2)", text)

    def test_compile_sdk_needs_become_one_decision(self):
        items = [{"key": k, "kind": "library", "tier": "care", "target": "x", "reasons": [],
                  "needs": [{"kind": "compileSdk", "value": "37", "msg": ""}]} for k in ("a", "b")]
        extra = plan.requirement_items(items, {"compileSdk": 36}, {"app/build.gradle.kts": 36}, [])
        self.assertEqual(len(extra), 1)
        self.assertEqual((extra[0]["target"], extra[0]["tier"]), ("37", "decision"))
        self.assertIn("a, b", extra[0]["reasons"][0])


if __name__ == "__main__":
    unittest.main()
