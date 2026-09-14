import unittest

from utils.ci_scope import Scope, classify, select_scope


class CiScopeTests(unittest.TestCase):
    def test_documentation_only_changes_skip_code_validation(self):
        self.assertEqual(
            classify(["README.md", "docs/releasing.md"]),
            Scope(
                run_suite=False,
                run_container=False,
                run_desktop=False,
                run_providers=False,
            ),
        )

    def test_tests_and_desktop_changes_skip_container_builds(self):
        self.assertEqual(
            classify(["tests/test_new_feature.py", "desktop/src/App.tsx"]),
            Scope(run_suite=True, run_container=False, run_desktop=True, run_providers=True),
        )

    def test_benchmark_changes_skip_product_artifact_builds(self):
        self.assertEqual(
            classify(["benchmarks/codex-mcp/scripts/setup.mjs"]),
            Scope(run_suite=True, run_container=False, run_desktop=False, run_providers=True),
        )

    def test_product_and_workflow_changes_validate_containers(self):
        for path in (
            "src/vidxp/new_feature.py",
            "web/upload-page/src/app.js",
            "utils/build_package.sh",
            ".github/workflows/future.yml",
        ):
            with self.subTest(path=path):
                scope = classify([path])
                self.assertTrue(scope.run_suite)
                self.assertTrue(scope.run_container)

    def test_desktop_uses_stable_product_and_packaging_boundaries(self):
        for path in (
            "desktop/src/App.tsx",
            "src/vidxp/settings.py",
            "tests/test_packaging.py",
            "utils/build_package.sh",
            "pyproject.toml",
            "uv.lock",
            ".github/workflows/desktop.yml",
            "plugins/vidxp/skills/vidxp-ingest-video/SKILL.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(classify([path]).run_desktop)

        for path in (
            "README.md",
            "docs/releasing.md",
            ".agents/plugins/marketplace.json",
            "web/upload-page/src/app.js",
        ):
            with self.subTest(path=path):
                self.assertFalse(classify([path]).run_desktop)

    def test_unknown_new_roots_default_to_full_validation(self):
        self.assertEqual(
            classify(["future-product/component.rs"]),
            Scope(run_suite=True, run_container=True, run_desktop=True, run_providers=True),
        )

    def test_release_candidates_defer_to_the_candidate_build(self):
        self.assertEqual(
            select_scope(
                ["pyproject.toml", "uv.lock"],
                base_ref="main",
                head_ref="release-please--branches--main",
            ),
            Scope(
                run_suite=False,
                run_container=False,
                run_desktop=False,
                run_providers=False,
            ),
        )

    def test_main_to_release_sync_does_not_rebuild_validated_main(self):
        self.assertEqual(
            select_scope(
                ["src/vidxp/app.py"],
                base_ref="release",
                head_ref="main",
            ),
            Scope(
                run_suite=False,
                run_container=False,
                run_desktop=False,
                run_providers=False,
            ),
        )

    def test_forced_candidate_validation_ignores_pr_scope(self):
        self.assertEqual(
            select_scope(
                [],
                force_validation=True,
                run_containers=True,
            ),
            Scope(run_suite=True, run_container=True, run_desktop=False, run_providers=True),
        )

    def test_benchmark_python_keeps_suite_without_product_builds(self):
        for paths in (
            ["src/vidxp/benchmarks/latency.py", "tests/test_benchmark_latency.py"],
            ["./src/vidxp/benchmarks/cli.py", "docs/benchmarking/performance.md"],
            ["tests/test_benchmark_cli.py"],
        ):
            with self.subTest(paths=paths):
                self.assertEqual(classify(paths), Scope(True, False, False, False))

    def test_benchmark_changes_do_not_hide_shared_or_unknown_changes(self):
        for path in (
            "src/vidxp/core/runner.py",
            "src/vidxp/cli.py",
            "src/vidxp/benchmarks/requirements.txt",
            "pyproject.toml",
            "uv.lock",
            "MANIFEST.in",
            "utils/ci_scope.py",
            ".github/workflows/future.yml",
            "future-product/component.rs",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    classify(["src/vidxp/benchmarks/latency.py", path]),
                    Scope(True, True, True, True),
                )

    def test_unrecognized_tests_keep_existing_validation(self):
        self.assertEqual(
            classify(["tests/test_new_benchmark_runtime.py"]),
            Scope(True, False, True, True),
        )

    def test_forced_validation_overrides_benchmark_exemption(self):
        self.assertEqual(
            select_scope(
                ["src/vidxp/benchmarks/latency.py"],
                force_validation=True,
                run_containers=True,
            ),
            Scope(True, True, False, True),
        )


if __name__ == "__main__":
    unittest.main()
