from __future__ import annotations

import ast
from pathlib import Path
import unittest

try:
    import tomllib
except ImportError:  # Python 3.9/3.10 use the pinned development backport.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "palmprint_workbench"
FLAT_MODULES = {
    "api_server",
    "camera_capture",
    "candidate_manifest",
    "config",
    "model_registry",
    "palm_backends",
    "palm_datasets",
    "palm_metrics",
    "palm_preprocessor",
    "palm_store",
    "palmprint_benchmark",
    "palmprint_service",
}


class ProductionPackageBoundaryTests(unittest.TestCase):
    def test_package_has_no_absolute_flat_module_imports(self) -> None:
        failures: list[str] = []
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    if node.module.split(".", 1)[0] in FLAT_MODULES:
                        failures.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in FLAT_MODULES:
                            failures.append(
                                f"{path.relative_to(ROOT)}:{node.lineno} {alias.name}"
                            )
        self.assertEqual(failures, [])

    def test_build_metadata_discovers_only_formal_packages(self) -> None:
        payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        setuptools = payload["tool"]["setuptools"]
        self.assertNotIn("py-modules", setuptools)
        include = setuptools["packages"]["find"]["include"]
        self.assertEqual(include, ["palmprint_workbench*", "tools*"])

    def test_app_is_a_thin_package_entry(self) -> None:
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("palmprint_workbench.api", source)
        self.assertNotIn("from api_server", source)
        self.assertNotIn("import *", source)

    def test_cpu_and_edcc_adapters_are_offline_only(self) -> None:
        runtime = (PACKAGE / "runtime" / "adapters.py").read_text(encoding="utf-8")
        offline = (ROOT / "tools" / "offline" / "adapters.py").read_text(encoding="utf-8")
        self.assertNotIn("class OnnxEmbeddingAdapter", runtime)
        self.assertNotIn("class EdccAdapter", runtime)
        self.assertIn("class OnnxEmbeddingAdapter", offline)
        self.assertIn("class EdccAdapter", offline)

    def test_production_evaluation_uses_npu_facade(self) -> None:
        server = (PACKAGE / "api" / "server.py").read_text(encoding="utf-8")
        facade = (PACKAGE / "services" / "evaluation.py").read_text(encoding="utf-8")
        self.assertIn("from ..services.evaluation import", server)
        self.assertNotIn("from tools.offline.benchmark import", server)
        self.assertIn("_require_npu", facade)
        self.assertNotIn("tools.offline", facade)
        self.assertNotIn("OnnxEmbeddingAdapter", facade)
        self.assertNotIn("EdccAdapter", facade)
        self.assertNotIn("numerical_consistency", facade)
        self.assertIn("resolve_runtime_model", facade)
        for forbidden in (
            "from tools",
            "import tools",
            "OnnxEmbeddingAdapter",
            "EdccAdapter",
            "numerical_consistency",
            "compare_embeddings",
        ):
            self.assertNotIn(forbidden, facade)

    def test_production_package_has_no_research_backend_imports(self) -> None:
        failures: list[str] = []
        forbidden_modules = {"tools", "onnxruntime"}
        forbidden_names = {
            "OnnxEmbeddingAdapter",
            "EdccAdapter",
            "numerical_consistency",
            "compare_embeddings",
        }
        # ``palmprint_workbench.tools.verify_assets`` is an explicit command
        # bridge to the board verifier; this gate covers the serving import
        # graph only.
        serving_dirs = (PACKAGE / "services", PACKAGE / "api")
        for directory in serving_dirs:
            for path in directory.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        if node.level == 0 and node.module:
                            if node.module.split(".", 1)[0] in forbidden_modules:
                                failures.append(
                                    f"{path.relative_to(ROOT)}:{node.lineno} {node.module}"
                                )
                        if any(alias.name in forbidden_names for alias in node.names):
                            failures.append(
                                f"{path.relative_to(ROOT)}:{node.lineno} forbidden symbol"
                            )
                    elif isinstance(node, ast.Import):
                        if any(
                            alias.name.split(".", 1)[0] in forbidden_modules
                            for alias in node.names
                        ):
                            failures.append(
                                f"{path.relative_to(ROOT)}:{node.lineno} forbidden module"
                            )
        self.assertEqual(failures, [])

    def test_offline_cli_help_is_lazy(self) -> None:
        source = (ROOT / "tools" / "offline" / "__main__.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="tools/offline/__main__.py")
        top_level_benchmark_imports = [
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module == "benchmark"
        ]
        self.assertEqual(top_level_benchmark_imports, [])
        self.assertIn("from .benchmark import main as benchmark_main", source)


if __name__ == "__main__":
    unittest.main()
