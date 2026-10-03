"""Online canary: the planner against the real repositories, on the committed fixture.

Skipped unless PLAN_ONLINE=1. Versions drift as libraries publish, so it checks the
invariants of the policy, not exact numbers. A failure means either a regression
or that the fixture no longer exercises a case (then update the fixture).

    PLAN_ONLINE=1 python3 -m unittest discover -s tests -p test_online.py -v
"""
import json
import os
import unittest

from test_repo import FIXTURE, SKILL, load, run

TOOLCHAIN = ("agp", "kotlin", "ksp", "wrapper")


@unittest.skipUnless(os.environ.get("PLAN_ONLINE") == "1", "set PLAN_ONLINE=1 to query the repositories")
class OnlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        plan = load("plan", SKILL / "scripts" / "plan.py")
        cls.vkey = staticmethod(plan.vkey)
        code, out = run(plan.main, [str(FIXTURE), "--source", "metadata", "--json"])
        cls.code, cls.plan = code, json.loads(out)
        cls.items = cls.plan["items"]

    def tier(self, key):
        return {i["tier"] for i in self.items if i["key"] == key}

    def test_runs(self):
        self.assertEqual(self.code, 0)
        self.assertTrue(self.items)

    def test_toolchain_is_never_safe(self):
        for i in self.items:
            if i["kind"] in TOOLCHAIN:
                self.assertNotEqual(i["tier"], "safe", i["key"])

    def test_safe_items_are_upgrades_without_requirements(self):
        for i in (i for i in self.items if i["tier"] == "safe"):
            self.assertFalse(i.get("needs"), i["key"])
            self.assertGreater(self.vkey(i["target"]), self.vkey(i["current"]), i["key"])

    def test_fixture_cases(self):
        self.assertEqual(self.tier("libraries.android-pdfview"), {"decision"})
        self.assertEqual(self.tier("ksp"), {"decision"})
        self.assertEqual(self.tier("kotlin"), {"decision"})
        self.assertIn("care", self.tier("agp"))
        self.assertIn("care", self.tier("gradle-wrapper"))
        # the latest core-ktx needs a higher compileSdk than the fixture's 36
        self.assertIn("decision", self.tier("compileSdk"))
        core = [i for i in self.items if i["key"] == "coreKtx"]
        self.assertEqual({i["tier"] for i in core}, {"safe", "care"})


if __name__ == "__main__":
    unittest.main()
