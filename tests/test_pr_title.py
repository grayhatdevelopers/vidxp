import unittest
from pathlib import Path

from utils.pr_title import EXAMPLES, title_problem

ROOT = Path(__file__).resolve().parents[1]


class PrTitleTests(unittest.TestCase):
    def test_conventional_titles_pass(self):
        for title in (
            *EXAMPLES,
            "build(deps): bump the actions group with 3 updates",
            "chore(main): release 1.0.0-b",
            "feat(search)!: drop the legacy query endpoint",
            "refactor!: rename the index command",
            "fix(fusion): bound moment components to shared overlapping interval (#87)",
        ):
            with self.subTest(title=title):
                self.assertIsNone(title_problem(title))

    def test_titles_release_please_would_skip_fail(self):
        for title, problem in (
            ("Show media state in list command (#112)", '": "'),
            ("feat:add clips", '": "'),
            ("Fix: keep search form submittable", "type must be one of"),
            ("feature(mcp): add clips", "type must be one of"),
            ("fix (mcp): add clips", "type must be one of"),
            ("fix(): add clips", "scope in parentheses is empty"),
            ("fix(mcp):  ", "subject"),
        ):
            with self.subTest(title=title):
                self.assertIn(problem, title_problem(title) or "")

    def test_failure_examples_are_the_contributing_guide_examples(self):
        guide = (ROOT / "docs" / "CONTRIBUTING.md").read_text(encoding="utf-8")
        for example in EXAMPLES:
            with self.subTest(example=example):
                self.assertIn(f"\n{example}\n", guide)


if __name__ == "__main__":
    unittest.main()
