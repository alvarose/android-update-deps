"""Offline tests for skills/android-update-deps/scripts/review_prs.py, with a fake `gh`.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""
import importlib.util
import json
import unittest

from test_plan import SCRIPTS, plan

spec = importlib.util.spec_from_file_location("review_prs", SCRIPTS / "review_prs.py")
review_prs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_prs)

BASE = '''[versions]
agp = "9.2.1"
okhttp = "5.1.0"
mockito = "5.23.0"
unused = "1.0"

[libraries]
okhttp = { module = "com.squareup.okhttp3:okhttp", version.ref = "okhttp" }
mockito = { module = "org.mockito:mockito-core", version.ref = "mockito" }
gson = "com.google.code.gson:gson:2.13.1"

[plugins]
android-application = { id = "com.android.application", version.ref = "agp" }
'''
BOT = {"login": "app/dependabot", "is_bot": True}
PASSING = [{"__typename": "CheckRun", "name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}]


def bump(text, old, new):
    assert old in text
    return text.replace(old, new)


class FakeGh:
    """Answers the calls GitHub makes, from a dict of {ref: {path: text}}."""

    def __init__(self, files, prs):
        self.files, self.prs = files, prs

    def __call__(self, args, cwd=None):
        if args[:2] == ["pr", "list"]:
            return json.dumps(self.prs)
        endpoint = args[-1]
        if "/compare/" in endpoint:  # base...head: the PR branched off "base"
            return json.dumps({"merge_base_commit": {"sha": "base"}})
        if "/contents/" in endpoint:
            path, ref = endpoint.split("/contents/", 1)[1].split("?ref=")
            text = self.files.get(ref, {}).get(path)
            if text is None:
                raise review_prs.GhError("404")
            return text
        raise AssertionError(f"unexpected gh call: {args}")


def pr(number, head, files, author=BOT, checks=PASSING, **extra):
    return dict({"number": number, "title": f"PR {number}", "url": f"https://x/{number}", "author": author,
                 "isDraft": False, "mergeable": "MERGEABLE", "headRefOid": head, "baseRefOid": "tip",
                 "files": [{"path": p} for p in files], "statusCheckRollup": checks}, **extra)


def offline_context():
    cat = plan.parse_catalog_text(BASE)
    units = plan.build_units(cat)
    refs = plan.scan_references({"app/build.gradle.kts": "implementation(libs.okhttp)\nimplementation(libs.mockito)\n"
                                 "implementation(libs.gson)\nplugins { alias(libs.plugins.android.application) }"}, cat)
    base = {"compileSdk": 36, "compileSdkMinor": 0, "agp": "9.2.1", "kotlin": "2.2.10"}
    ctx = {"net": plan.Net(offline=True), "base": base, "report": None, "children": {}, "refs": refs, "lint": {},
           "cooldown": 3, "offline": True}
    return {"cat": cat, "cat_path": "gradle/libs.versions.toml", "cat_text": BASE, "units": units, "ctx": ctx}


class ChangeTests(unittest.TestCase):
    def test_catalog_changes(self):
        head = bump(bump(BASE, 'okhttp = "5.1.0"', 'okhttp = "5.2.0"'), "gson:2.13.1", "gson:2.13.2")
        head = bump(head, 'unused = "1.0"\n', "")
        changes, notes = review_prs.catalog_changes(BASE, head)
        self.assertEqual(changes, [{"key": "okhttp", "from": "5.1.0", "to": "5.2.0"},
                                   {"key": "libraries.gson", "from": "2.13.1", "to": "2.13.2"}])
        self.assertEqual(notes, [])  # a removed [versions] entry isn't an alias

    def test_inline_and_wrapper(self):
        self.assertEqual(review_prs.inline_changes('classpath("a.b:c:1.0")', 'classpath("a.b:c:1.1")'),
                         [{"key": "a.b:c", "from": "1.0", "to": "1.1"}])
        self.assertEqual(review_prs.wrapper_version(
            "distributionUrl=https\\://services.gradle.org/distributions/gradle-9.8.0-bin.zip"), "9.8.0")

    def test_file_kinds(self):
        kinds = {p: review_prs.file_kind(p) for p in (
            "gradle/libs.versions.toml", "gradle/wrapper/gradle-wrapper.properties", "gradlew",
            "app/build.gradle.kts", ".github/workflows/ci.yml", ".idea/kotlinc.xml", "app/src/Main.kt")}
        self.assertEqual(list(kinds.values()), ["catalog", "wrapper", "wrapper-files", "build", "actions", "ide", "other"])

    def test_ci_and_authors(self):
        self.assertEqual(review_prs.ci_state([]), "none")
        self.assertEqual(review_prs.ci_state(PASSING + [{"name": "x", "conclusion": "CANCELLED"}]), "incomplete")
        self.assertEqual(review_prs.ci_state([{"status": "IN_PROGRESS", "conclusion": ""}]), "pending")
        failing = [{"workflowName": "E2E", "conclusion": "FAILURE"}, {"context": "lint", "state": "ERROR"}]
        self.assertEqual(review_prs.ci_state(failing), "failing")
        self.assertEqual(review_prs.failing_checks(failing), ["E2E", "lint"])
        self.assertTrue(review_prs.is_verified_bot({"login": "app/renovate", "is_bot": True}))
        self.assertFalse(review_prs.is_verified_bot({"login": "dependabot-fan", "is_bot": False}))
        self.assertTrue(review_prs.is_verified_bot({"login": "my-renovate", "is_bot": False}, ["my-renovate"]))


class ReviewTests(unittest.TestCase):
    def run_review(self, prs, heads):
        files = {"base": {"gradle/libs.versions.toml": BASE}}
        files.update({ref: {"gradle/libs.versions.toml": text} for ref, text in heads.items()})
        gh = review_prs.GitHub("o/r", runner=FakeGh(files, prs))
        return {p["number"]: p for p in review_prs.review(gh, offline_context(), prs)}

    def test_verdicts(self):
        patch = bump(BASE, 'mockito = "5.23.0"', 'mockito = "5.23.1"')
        major = bump(BASE, 'okhttp = "5.1.0"', 'okhttp = "6.0.0"')
        prs = [pr(1, "h1", ["gradle/libs.versions.toml"]),
               pr(2, "h2", ["gradle/libs.versions.toml"]),
               pr(3, "h1", ["gradle/libs.versions.toml", "app/src/Main.kt"]),
               pr(4, "h1", ["gradle/libs.versions.toml"], author={"login": "someone", "is_bot": False}),
               pr(5, "h1", ["gradle/libs.versions.toml"], checks=[{"name": "build", "conclusion": "CANCELLED"}])]
        out = self.run_review(prs, {"h1": patch, "h2": major})
        self.assertEqual(out[1]["verdict"], "merge candidate")
        self.assertEqual(out[1]["items"][0]["target"], "5.23.1")
        self.assertEqual(out[2]["verdict"], "review")  # a major needs care
        self.assertEqual(out[3]["verdict"], "hold")
        self.assertIn("app/src/Main.kt", out[3]["blockers"][0])
        self.assertEqual(out[4]["verdict"], "hold")
        self.assertIn("isn't a verified", " ".join(out[4]["blockers"]))
        self.assertEqual(out[5]["verdict"], "review")
        self.assertIn("cancelled", " ".join(out[5]["cares"]))

    def test_superseded_and_shared_failures(self):
        low = bump(BASE, 'mockito = "5.23.0"', 'mockito = "5.23.1"')
        high = bump(BASE, 'mockito = "5.23.0"', 'mockito = "5.24.0"')
        other = bump(BASE, "gson:2.13.1", "gson:2.13.2")
        flaky = [{"workflowName": "E2E", "conclusion": "FAILURE"}] + PASSING
        prs = [pr(1, "low", ["gradle/libs.versions.toml"], checks=flaky),
               pr(2, "high", ["gradle/libs.versions.toml"], checks=flaky),
               pr(3, "other", ["gradle/libs.versions.toml"], checks=flaky)]
        out = self.run_review(prs, {"low": low, "high": high, "other": other})
        self.assertIn("superseded by #2", " ".join(out[1]["blockers"]))
        self.assertEqual(out[3]["verdict"], "review")  # E2E fails everywhere: not this bump's fault
        self.assertIn("E2E fails on every bot PR", " ".join(out[3]["cares"]))


if __name__ == "__main__":
    unittest.main()
