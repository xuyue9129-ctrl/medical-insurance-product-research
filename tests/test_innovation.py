"""创新点识别：对照样本不足时不判首见/少见、行业先验、全网核验（market）、市场样本特征表、同业对照产品（role=peer）。"""
import os
import unittest

from _util import TempWorkspaceCase, has_module


class InnovationBaselineTest(TempWorkspaceCase):
    def _product(self, name, fields, role=None):
        from medins_lib.records import apply_patch
        meta = {"company": "示例健康", "product_name": name}
        if role:
            meta["role"] = role
        p = self.ws.register(meta)
        res = apply_patch(self.ws, p["pid"], [{"field": k, "status": "推定", "value": v, "note": "测试数据"} for k, v in fields.items()])
        self.assertTrue(all(r["applied"] for r in res), res)
        return p["pid"]

    def _target(self):
        return self._product("示例·免健告百万医疗（2026）", {
            "advanced_therapy": "质子重离子医疗保险金100万元", "preexisting_general": "一般既往症可赔，首年按约定比例×50%",
            "launch_date": "2026-06"})

    def test_small_sample_prior_and_market_verification(self):
        import medins
        from medins_lib.innovation import INSUFFICIENT, NOT_FOUND, run_innovation
        from medins_lib.market import load_scan, pending_features
        pid = self._target()
        feats = {c["feature"]: c for c in run_innovation(self.ws)["products"][pid]["candidates"]}
        self.assertEqual(feats["proton"]["verdict"], "行业常见/跟进（先验）")  # 行业普遍配置，不会被判成首见
        c = feats["general_pre_payable"]
        self.assertEqual(c["verdict"], INSUFFICIENT)
        self.assertTrue(any("尚未全网核验" in x for x in c["flags"]))
        self.assertIn("general_pre_payable", [f["key"] for f in pending_features(self.ws)])
        self.assertNotIn("proton", [f["key"] for f in pending_features(self.ws)])

        # 检索未见：至少两组检索词并写明来源
        w = ["-w", str(self.ws.root)]
        self.assertEqual(medins.main(["market", "none", *w, "--feature", "general_pre_payable", "--query", "一般既往症 可赔"]), 2)
        self.assertEqual(medins.main(["market", "none", *w, "--feature", "general_pre_payable", "--query", "一般既往症 可赔",
                                      "--query", "既往症 也能赔 百万医疗", "--note", "保险公司官网、行业协会产品库"]), 0)
        c = {c["feature"]: c for c in run_innovation(self.ws)["products"][pid]["candidates"]}["general_pre_payable"]
        self.assertEqual(c["verdict"], NOT_FOUND)
        self.assertEqual(c["market"]["status"], "none")

        # 全网命中：页面原文核验（用本地保存的页面模拟），计入对照
        page = self.tmp / "peer_page.html"
        page.write_text("<html><body><p>某健康险公司 某某医疗保险条款：被保险人的一般既往症，我们按照约定比例给付保险金。</p></body></html>",
                        encoding="utf-8")
        self.assertEqual(medins.main(["market", "add", *w, "--feature", "general_pre_payable", "--product", "某健康·某某医疗",
                                      "--file", str(page), "--quote", "这句话不在页面里"]), 2)  # 引文核验不过 → 拒收
        # 之前“检索未见”，后来找到了同类产品：命中覆盖未见记录
        self.assertEqual(medins.main(["market", "add", *w, "--feature", "general_pre_payable", "--product", "某健康·某某医疗",
                                      "--file", str(page), "--quote", "被保险人的一般既往症，我们按照约定比例给付保险金", "--launch", "2025-03"]), 0)
        f = load_scan(self.ws)["features"]["general_pre_payable"]
        self.assertEqual((f["status"], f["hits"][0]["match"]), ("found", "exact"))
        self.assertTrue((self.ws.root / f["hits"][0]["file"]).exists())
        self.assertEqual(medins.main(["market", "none", *w, "--feature", "general_pre_payable", "--query", "a", "--query", "b",
                                      "--note", "x"]), 2)  # 有命中后不能再标未见
        c = {c["feature"]: c for c in run_innovation(self.ws)["products"][pid]["candidates"]}["general_pre_payable"]
        self.assertEqual(c["verdict"], "少见差异化（全网核验已有 1 款）")

    def test_market_hit_counts_as_peer(self):
        import medins
        from medins_lib.innovation import run_innovation
        pid = self._target()
        run_innovation(self.ws)
        page = self.tmp / "p.txt"
        page.write_text("本合同对一般既往症的赔付比例为80%。", encoding="utf-8")
        w = ["-w", str(self.ws.root)]
        self.assertEqual(medins.main(["market", "add", *w, "--feature", "general_pre_payable", "--product", "甲公司·甲医疗",
                                      "--file", str(page), "--quote", "本合同对一般既往症的赔付比例为80%", "--launch", "2024-05"]), 0)
        # 把研究对象自己（带公司前缀）误记为命中：不计入
        self.assertEqual(medins.main(["market", "add", *w, "--feature", "general_pre_payable", "--product", "示例健康·示例·免健告百万医疗（2026）",
                                      "--file", str(page), "--quote", "本合同对一般既往症的赔付比例为80%"]), 0)
        c = {c["feature"]: c for c in run_innovation(self.ws)["products"][pid]["candidates"]}["general_pre_payable"]
        self.assertEqual(c["verdict"], "少见差异化（全网核验已有 1 款）")
        self.assertTrue(any(x["source"].startswith("全网核验") for x in c["peer_examples"]))

    def test_public_market_samples(self):
        from medins_lib.innovation import run_innovation
        os.environ["MEDINS_MARKET_SAMPLES"] = "1"
        pid = self._target()
        inn = run_innovation(self.ws)
        self.assertGreaterEqual(inn["baseline"]["builtin_samples"], 30)
        c = {c["feature"]: c for c in inn["products"][pid]["candidates"]}["proton"]
        self.assertEqual(c["verdict"], "行业常见/跟进")  # 样本充足时按样本判定
        self.assertGreaterEqual(c["peers_total"], 30)

    def test_peer_products_and_scope(self):
        import medins
        from medins_lib.innovation import MIN_PEERS, peer_fields, run_innovation, suggest_peers
        from medins_lib.validate import validate_product
        pid = self._target()
        run_innovation(self.ws)
        self.assertIn("preexisting_general", peer_fields(self.ws))
        self.assertTrue(suggest_peers(self.ws, 5))
        peers = [self._product(f"同业对照产品{i}", {"preexisting_general": "一般既往症不赔" if i else "一般既往症可赔",
                                                  "launch_date": f"2025-0{i + 1}"}, role="peer") for i in range(MIN_PEERS)]
        self.assertEqual(self.ws.research_pids(), [pid])
        inn = run_innovation(self.ws)
        self.assertEqual(inn["baseline"]["peer_products"], MIN_PEERS)
        self.assertEqual(set(inn["products"]), {pid})  # 同业对照不作为研究对象
        c = {c["feature"]: c for c in inn["products"][pid]["candidates"]}["general_pre_payable"]
        self.assertEqual((c["peers_with"], c["peers_total"]), (1, MIN_PEERS))
        self.assertTrue(c["verdict"].startswith("少见差异化"))
        v = validate_product(self.ws, peers[0])  # 同业对照只校验条款原件与对照字段
        self.assertTrue(any("01 " in e for e in v["errors"]))
        self.assertFalse(any(x in e for e in v["errors"] for x in ("02 ", "03 ", "04 ", "05 ", "保险期间")))
        if has_module("openpyxl"):
            import openpyxl
            from medins_lib.excel import build_workbook
            wb = openpyxl.load_workbook(build_workbook(self.ws))
            heads = [str(x.value or "") for x in wb[wb.sheetnames[1]][4]]
            self.assertFalse(any("同业对照产品" in h for h in heads))
        self.assertEqual(medins.main(["peers", "-w", str(self.ws.root), "--skip", "--reason", "测试"]), 0)


if __name__ == "__main__":
    unittest.main()
