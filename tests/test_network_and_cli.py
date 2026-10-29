"""本地 HTTP 服务模拟官网：链接发现、下载归档、HTML 冒充 PDF 识别；CLI 与技能规范校验。"""
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from _util import FIXTURES, HAS_PDF, ROOT, SCRIPTS, SKILL_DIR, TempWorkspaceCase


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipUnless(HAS_PDF, "需要任一 PDF 解析库")
class NetworkTest(TempWorkspaceCase):
    @classmethod
    def setUpClass(cls):
        cls.site = Path(tempfile.mkdtemp(prefix="medins_site_"))
        (cls.site / "files").mkdir()
        shutil.copy(FIXTURES / "product_page.html", cls.site / "product.html")
        shutil.copy(FIXTURES / "示例B款条款.pdf", cls.site / "files" / "clause_b.pdf")
        shutil.copy(FIXTURES / "示例B款费率表.pdf", cls.site / "files" / "rates_b.pdf")
        (cls.site / "files" / "fake.pdf").write_text("<html><body>请先登录</body></html>", encoding="utf-8")
        handler = functools.partial(_Quiet, directory=str(cls.site))
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.site, ignore_errors=True)

    def test_discover_and_fetch(self):
        from medins_lib.fetch import fetch_url, load_manifest
        from medins_lib.search import discover_links
        links = discover_links(f"{self.base}/product.html", "示例·免健告百万医疗（2026）")
        by_name = {Path(x["url"]).name: x for x in links}
        self.assertEqual(by_name["clause_b.pdf"]["doc_type_guess"], "01")
        self.assertEqual(by_name["rates_b.pdf"]["doc_type_guess"], "02")
        self.assertEqual(by_name["serious_list.pdf"]["doc_type_guess"], "04")  # 来自页面脚本里的 JSON
        self.assertNotIn("about", by_name)

        p = self.ws.register({"company": "示例健康", "product_name": "示例·免健告百万医疗（2026）",
                              "clause_names": ["示例互联网个人住院B款医疗保险"]})
        d = fetch_url(self.ws, p["pid"], by_name["clause_b.pdf"]["url"], anchor=by_name["clause_b.pdf"]["anchor"])
        self.assertEqual(d["doc_type"], "01")
        self.assertEqual(d["pages"], 2)
        self.assertEqual(d["version_check"]["status"], "matched")
        self.assertEqual(d["sources"][0]["url"], by_name["clause_b.pdf"]["url"])
        self.assertEqual(d["evidence_level"], "C")  # 127.0.0.1 不是登记的官方域名
        fake = fetch_url(self.ws, p["pid"], f"{self.base}/files/fake.pdf", anchor="投保须知")
        self.assertEqual(fake["ext"], ".html")  # 服务器返回 HTML 冒充 PDF
        self.assertEqual(len(load_manifest(self.ws, p["pid"])["documents"]), 2)

    def test_download_error_is_reported(self):
        from medins_lib.common import MedinsError
        from medins_lib.fetch import fetch_url
        p = self.ws.register({"company": "示例健康", "product_name": "示例产品"})
        with self.assertRaises(MedinsError):
            fetch_url(self.ws, p["pid"], f"{self.base}/files/missing.pdf")


class CliAndSpecTest(unittest.TestCase):
    def run_cli(self, *args, cwd=None):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, str(SCRIPTS / "medins.py"), *args], capture_output=True, text=True,
                           encoding="utf-8", env=env, cwd=cwd)
        return p.returncode, p.stdout, p.stderr

    def test_doctor_and_resolve_json(self):
        rc, out, err = self.run_cli("doctor", "--json")
        self.assertIn(rc, (0, 2), err)
        self.assertIn("pdf_backends", json.loads(out))
        rc, out, _ = self.run_cli("resolve", "蓝医保2027", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["candidates"][0]["product_name"], "蓝医保·免健告百万医疗（2027）")

    def test_cli_errors_are_friendly(self):
        tmp = tempfile.mkdtemp(prefix="medins_cli_")
        try:
            rc, _, err = self.run_cli("list", "-w", str(Path(tmp) / "nope"))
            self.assertEqual(rc, 2)
            self.assertIn("工作区未初始化", err)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_skill_spec(self):
        from skill_spec import parse_frontmatter, validate_skill
        self.assertEqual(validate_skill(SKILL_DIR), [])
        fm, body = parse_frontmatter((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(fm["name"], SKILL_DIR.name)
        self.assertLessEqual(len(fm["description"]), 1024)
        self.assertLess(len(body.splitlines()), 500)

    def test_install_dry_run(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        tmp = tempfile.mkdtemp(prefix="medins_proj_")
        try:
            p = subprocess.run([sys.executable, str(ROOT / "install.py"), "--project", tmp], capture_output=True, text=True,
                               encoding="utf-8", env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
            for d in (".agents/skills", ".claude/skills", ".codebuddy/skills"):
                self.assertTrue((Path(tmp) / d / SKILL_DIR.name / "SKILL.md").exists(), d)
                self.assertFalse(list((Path(tmp) / d / SKILL_DIR.name).rglob("__pycache__")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
