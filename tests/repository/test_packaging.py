"""Installed imports do not depend on the process cwd."""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "bin" / "python"
IMPORTS = (
    "import ppap_api, ppap_application, ppap_core, ppap_corpus_contracts, ppap_deep, "
    "ppap_docops, ppap_excel_audit_engine, ppap_ocr_adapter, ppap_product_store, "
    "ppap_pdf_to_excel, ppap_vision, ppap_worker"
)


class PackagingTests(unittest.TestCase):
    def test_imports_and_owner_collection_ignore_cwd(self):
        self.assertTrue(PYTHON.is_file(), PYTHON)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        for cwd in ("/tmp", str(ROOT / "tests"), str(ROOT)):
            completed = subprocess.run(
                [str(PYTHON), "-c", IMPORTS],
                cwd=cwd,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        collected = subprocess.run(
            [
                str(PYTHON),
                "-m",
                "pytest",
                "--rootdir",
                str(ROOT),
                "-c",
                str(ROOT / "pyproject.toml"),
                "--collect-only",
                "-q",
                str(ROOT / "packages/ppap_ocr_adapter/tests"),
            ],
            cwd="/tmp",
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(collected.returncode, 0, collected.stderr)
        self.assertIn("test_", collected.stdout)
