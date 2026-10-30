"""创新点识别：对照样本不足时不判首见/少见、行业先验、同业对照产品（role=peer）作为原文核验的对照样本。"""
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

    def test_small_sample_prior_peers_and_skip(self):
        import medins
        from medins_lib.innovation import (INSUFFICIENT, MIN_PEERS, insufficient_features, peer_fields, run_innovation,
                                           suggest_peers)
        from medins_lib.validate import validate_product
        pid = self._product("示例·免健告百万医疗（2026）", {
            "advanced_therapy": "质子重离子医疗保险金100万元", "preexisting_general": "一般既往症可赔，首年按约定比例×50%",
            "launch_date": "2026-06"})
        inn = run_innovation(self.ws)
        feats = {c["feature"]: c for c in inn["products"][pid]["candidates"]}
        self.assertEqual(feats["proton"]["verdict"], "行业常见/跟进（先验）")  # 行业普遍配置，不会被判成首见
        self.assertEqual(feats["general_pre_payable"]["verdict"], INSUFFICIENT)  # 没有对照样本，不判首见/少见
        self.assertTrue(any("对照样本仅 0 款" in x for x in feats["general_pre_payable"]["flags"]))
        self.assertEqual([f["key"] for f in insufficient_features(self.ws)][:1], ["general_pre_payable"])
        self.assertIn("preexisting_general", peer_fields(self.ws))
        self.assertTrue(suggest_peers(self.ws, 5))

        # 补充 MIN_PEERS 款同业对照：只填对照字段
        peers = [self._product(f"同业对照产品{i}", {"preexisting_general": "一般既往症不赔" if i else "一般既往症可赔",
                                                  "launch_date": f"2025-0{i + 1}"}, role="peer") for i in range(MIN_PEERS)]
        self.assertEqual(self.ws.research_pids(), [pid])
        self.assertEqual(sorted(self.ws.peer_pids()), sorted(peers))
        inn = run_innovation(self.ws)
        self.assertEqual(inn["baseline"]["peer_products"], MIN_PEERS)
        c = {c["feature"]: c for c in inn["products"][pid]["candidates"]}["general_pre_payable"]
        self.assertEqual((c["peers_with"], c["peers_total"], c["verdict"]), (1, MIN_PEERS, "少见差异化"))
        self.assertEqual(c["peer_examples"][0]["source"], "同业对照（原文核验）")
        self.assertEqual(set(inn["products"]), {pid})  # 同业对照不作为研究对象

        # 同业对照只校验条款原件与对照字段
        v = validate_product(self.ws, peers[0])
        self.assertTrue(any("01 " in e for e in v["errors"]))
        self.assertFalse(any(x in e for e in v["errors"] for x in ("02 ", "03 ", "04 ", "05 ")))
        self.assertFalse(any("保险期间" in e for e in v["errors"]))

        if has_module("openpyxl"):
            import openpyxl
            from medins_lib.excel import build_workbook
            wb = openpyxl.load_workbook(build_workbook(self.ws))
            heads = [str(x.value or "") for x in wb[wb.sheetnames[1]][4]]
            self.assertFalse(any("同业对照产品" in h for h in heads))  # 对比表不含同业对照产品

        self.assertEqual(medins.main(["peers", "-w", str(self.ws.root), "--skip", "--reason", "测试：无联网"]), 0)
        self.assertEqual(self.ws.load()["peer_skip"]["reason"], "测试：无联网")


if __name__ == "__main__":
    unittest.main()
