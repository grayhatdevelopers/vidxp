import unittest

from utils.ci_scope import Scope, classify


class CiScopeTests(unittest.TestCase):
    def test_documentation_only_changes_skip_code_validation(self):
        self.assertEqual(
            classify(["README.md", "docs/releasing.md"]),
            Scope(run_suite=False, run_container=False),
        )

    def test_tests_and_desktop_changes_skip_container_builds(self):
        self.assertEqual(
            classify(["tests/test_new_feature.py", "desktop/src/App.tsx"]),
            Scope(run_suite=True, run_container=False),
        )

    def test_product_and_workflow_changes_validate_containers(self):
        for path in (
            "src/vidxp/new_feature.py",
            "web/upload-page/src/app.js",
            "utils/build_package.sh",
            ".github/workflows/future.yml",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    classify([path]),
                    Scope(run_suite=True, run_container=True),
                )

    def test_unknown_new_roots_default_to_full_validation(self):
        self.assertEqual(
            classify(["future-product/component.rs"]),
            Scope(run_suite=True, run_container=True),
        )


if __name__ == "__main__":
    unittest.main()
