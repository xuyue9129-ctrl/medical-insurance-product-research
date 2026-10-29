"""纯函数单元测试：规范化、模糊检索、资料分类、引文定位、费率表解析。"""
import unittest

import _util  # noqa: F401  (设置 sys.path)
from medins_lib.catalog import load_catalog, resolve
from medins_lib.common import safe_filename
from medins_lib.doctypes import classify, classify_name
from medins_lib.evidence import locate
from medins_lib.normalize import cn_to_int, match_key, name_key, numbers_in, unmatched_numbers, versions_in, years_in
from medins_lib.rates import parse_rate_text, pick_age


class NormalizeTest(unittest.TestCase):
    def test_match_key_ignores_whitespace_and_width(self):
        self.assertEqual(match_key("住院 H 款\n医疗保险（含）"), match_key("住院H款医疗保险(含)"))

    def test_cn_numbers(self):
        self.assertEqual(cn_to_int("三十"), 30)
        self.assertEqual(cn_to_int("一万二千"), 12000)
        self.assertEqual(cn_to_int("二〇二六"), 2026)

    def test_numbers_and_units(self):
        self.assertIn(15000.0, numbers_in("年免赔额为1.5万元"))
        self.assertIn(60.0, numbers_in("赔付比例为百分之六十"))
        self.assertIn(30.0, numbers_in("等待期为三十日"))
        self.assertNotIn(1000000.0, numbers_in("百万医疗险"))  # “百万医疗”不是数额

    def test_unmatched_numbers(self):
        self.assertEqual(unmatched_numbers("免赔额1万元", "年免赔额为10,000元"), [])
        self.assertEqual(unmatched_numbers("1.5万", "免赔额为15000元"), [])
        self.assertEqual(unmatched_numbers("免赔额2万元", "年免赔额为1万元"), ["2"])

    def test_name_key_versions(self):
        self.assertEqual(name_key("众民保·中高端26版"), name_key("众民保中高端2026版"))
        self.assertIn("2026", years_in("众民保中高端26"))
        self.assertIn("B款", versions_in("太保家安芯3.0（H2025B）"))
        self.assertIn("3号", versions_in("长相安三号"))

    def test_safe_filename(self):
        self.assertEqual(safe_filename('a<b>:c"/d|e?.pdf'), "a_b__c__d_e_.pdf")
        self.assertTrue(safe_filename("CON.txt").startswith("_"))
        self.assertLessEqual(len(safe_filename("长" * 300 + ".pdf", 80)), 80)


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalog()

    def top(self, q):
        r = resolve(q, self.cat)
        return r, r["candidates"][0]["product_name"] if r["candidates"] else ""

    def test_catalog_loaded(self):
        self.assertGreater(len(self.cat), 150)

    def test_fuzzy_hits(self):
        cases = {
            "蓝医保2027": "蓝医保·免健告百万医疗（2027）",
            "长相安5号": "长相安5号中高端医疗保险（免健告版）",
            "平安全家保": "平安e生保·全家保2026（易保版）",
            "家安芯3.0 B款": "家安芯3.0（H2025B）",
            "人保福安康": "AI福安康百万医疗保险",
            "欣享易生B款": "欣享易生2026版·B款",
            "蓝医保有福": "蓝医保·免健告长期医疗险（有福版）",
            "蓝医保 免健告 百万 2025": "蓝医保·百万医疗险（免健告版）",
        }
        for q, want in cases.items():
            r, got = self.top(q)
            self.assertEqual(got, want, f"{q} → {got}")

    def test_ambiguous_detection(self):
        r, _ = self.top("太保免健告百万")  # 太享保与蓝医保都符合
        self.assertFalse(r["confident"])
        r, _ = self.top("暖医保")  # 君龙与德华同名系列
        self.assertTrue(r["ambiguous"])

    def test_year_conflict_penalized(self):
        r = resolve("蓝医保2027", self.cat)
        names = [c["product_name"] for c in r["candidates"]]
        self.assertEqual(names[0], "蓝医保·免健告百万医疗（2027）")
        self.assertTrue(any("年份不同" in "".join(c["reasons"]) for c in r["candidates"][1:]))

    def test_same_series_listed(self):
        r = resolve("蓝医保2027", self.cat)
        self.assertGreaterEqual(len(r["same_series"]), 3)


class DoctypeTest(unittest.TestCase):
    def test_suffix_wins(self):
        cases = {
            "蓝医保免健告百万医疗2027费率表": "02", "蓝医保免健告百万医疗2027投保须知": "05",
            "太保互联网个人一年期住院H款医疗保险责任免除": "04", "蓝医保免健告版免责条款": "04",
            "蓝医保免健告版严重既往症清单": "04", "私立医院清单": "06", "院外重疾紧急用药目录": "06",
            "健康管理服务手册": "07", "太保互联网个人一年期住院F款医疗保险条款": "01",
            "蓝医保中高端简易健康告知": "03", "销售平台客户告知书": "05", "人身保险互联网投保提示书": "05",
        }
        for name, code in cases.items():
            self.assertEqual(classify_name(name), code, name)

    def test_content_fallback(self):
        code, basis = classify(url="https://x.com/232701035_00033362.pdf",
                               first_pages_text="基准费率表\n投保年龄（周岁）\n年交 月交\n0-5 353 624")
        self.assertEqual((code, basis), ("02", "正文关键词"))


class EvidenceTest(unittest.TestCase):
    pages = ("本合同的保险期间为1 年，\n不保证续保。等待期为30 日。",
             "被保险人于本合同生效日前365 天内在中华人民共和国境内1累计居住至少183 天；",
             "跨页的句子开头部分",
             "继续在这一页结束。")
    keys = tuple(match_key(p) for p in pages)

    def test_exact_with_linebreaks(self):
        r = locate("本合同的保险期间为1年，不保证续保。", self.pages, self.keys)
        self.assertEqual((r["match"], r["page"]), ("exact", 1))

    def test_fuzzy_footnote(self):
        r = locate("被保险人于本合同生效日前365天内在中华人民共和国境内累计居住至少183天", self.pages, self.keys)
        self.assertEqual(r["match"], "fuzzy")
        self.assertEqual(r["page"], 2)

    def test_ellipsis_and_cross_page(self):
        self.assertEqual(locate("保险期间为1年……等待期为30日", self.pages, self.keys)["match"], "exact")
        self.assertEqual(locate("跨页的句子开头部分继续在这一页结束", self.pages, self.keys)["match"], "exact")

    def test_not_found(self):
        self.assertEqual(locate("本合同保证续保20年", self.pages, self.keys)["match"], "not_found")


class RatesTest(unittest.TestCase):
    def test_blocks_and_continuation(self):
        pages = ["有基本医疗保险\n计划一 计划二\n0-5 353 624\n41-45 505 875",
                 "46-50 542 886\n51-55 679 1,110\n无基本医疗保险\n计划一 计划二\n0-5 827 1,560\n46-50 1,496 2,293"]
        blocks = parse_rate_text(pages)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(len(blocks[0]["rows"]), 4)  # 跨页续表已合并
        picks = pick_age(blocks, 50)
        self.assertEqual(picks[0]["values"], ["542", "886"])
        self.assertEqual(picks[0]["page"], 2)
        self.assertEqual(picks[1]["values"], ["1,496", "2,293"])


if __name__ == "__main__":
    unittest.main()
