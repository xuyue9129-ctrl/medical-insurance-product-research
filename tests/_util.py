"""测试公共设置：把技能脚本目录加入 sys.path，并提供临时工作区。"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "medical-insurance-product-research"
SCRIPTS = SKILL_DIR / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tools"))

from medins_lib.textextract import available_pdf_backends  # noqa: E402

HAS_PDF = bool(available_pdf_backends())


def has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


class TempWorkspaceCase(unittest.TestCase):
    """每个测试用独立的临时工作区与用户目录（不污染真实 ~/.medins-research）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="medins_test_"))
        self._old_home = os.environ.get("MEDINS_HOME")
        os.environ["MEDINS_HOME"] = str(self.tmp / "home")
        from medins_lib.workspace import Workspace
        self.ws = Workspace(self.tmp / "ws")
        self.ws.init("测试研究")

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("MEDINS_HOME", None)
        else:
            os.environ["MEDINS_HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)
