"""引导模式测试：next 逐阶段推进、任务卡、补丁骨架可直接 apply、细节检查。"""
import json
import unittest

from _util import FIXTURES, HAS_PDF, TempWorkspaceCase


class GuideTest(TempWorkspaceCase):
    def test_lint(self):
        from medins_lib.guide import lint_field
        self.assertTrue(lint_field("deductible", "有免赔"))
        self.assertFalse(lint_field("deductible", "年免赔额1万元"))
        self.assertTrue(lint_field("coinsurance", "按计划赔付"))

    def test_next_without_products(self):
        from medins_lib.guide import next_steps
        r = next_steps(self.ws)
        self.assertEqual(r["stage"], "登记产品")
        self.assertIn("resolve", r["steps"][0]["cmd"])

    def test_comparison_judgment_deliverables_and_deliver(self):
        import medins
        from medins_lib.common import write_json
        from medins_lib.guide import comparison_judged, deliverables_built, narrative_filled, write_analysis_template
        self.ws.register({"company": "示例健康", "product_name": "示例医疗（2026）"})
        write_json(self.ws.outputs_dir / "对比结果.json", {"versions": [
            {"old": {"pid": "a", "product_name": "A"}, "new": {"pid": "b", "product_name": "B"}, "changes": []}]})
        write_json(self.ws.outputs_dir / "创新点候选.json", {"products": {}})
        self.assertFalse(comparison_judged(self.ws))
        path = write_analysis_template(self.ws)
        a = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("findings", a)
        a["version_notes"]["a->b"] = "主险换款。"
        path.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(comparison_judged(self.ws))
        self.assertFalse(narrative_filled(self.ws))
        write_analysis_template(self.ws)  # 再次生成只补缺，不覆盖已写判断
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version_notes"]["a->b"], "主险换款。")
        self.assertEqual(deliverables_built(self.ws)["excel"], "")
        (self.ws.outputs_dir / "示例_对比表_20260101.xlsx").write_bytes(b"x")
        self.assertTrue(deliverables_built(self.ws)["excel"].endswith(".xlsx"))
        self.assertEqual(medins.main(["deliver", "-w", str(self.ws.root), "--report", "yes", "--ppt", "no"]), 0)
        self.assertEqual({k: v for k, v in self.ws.load()["deliverables"].items() if k != "asked_at"}, {"report": True, "ppt": False})

    @unittest.skipUnless(HAS_PDF, "需要任一 PDF 解析库")
    def test_stage_progression_task_and_template(self):
        from medins_lib.extract import run_extract
        from medins_lib.fetch import archive_file, mark_gap
        from medins_lib.guide import next_steps, task_card, write_template
        from medins_lib.records import apply_patch, load_patch, load_record
        p = self.ws.register({"company": "示例健康", "product_name": "示例·免健告百万医疗（2026）"})
        pid = p["pid"]
        self.assertEqual(next_steps(self.ws)["stage"], "收集资料")
        archive_file(self.ws, pid, FIXTURES / "示例B款条款.pdf", title="示例互联网个人住院B款医疗保险条款")
        r = next_steps(self.ws)
        self.assertEqual(r["stage"], "核对版本")
        self.assertIn("示例互联网个人住院B款医疗保险", r["steps"][0]["cmd"])  # 已预填识别出的备案条款名
        self.ws.register({**self.ws.get(pid), "clause_names": ["示例互联网个人住院B款医疗保险"]})
        self.assertEqual(next_steps(self.ws)["stage"], "补齐资料")
        for code in ("02", "03", "04", "05"):
            mark_gap(self.ws, pid, code, "未找到可确认文件", note="测试")
        self.assertEqual(next_steps(self.ws)["stage"], "抽取")
        run_extract(self.ws, pid, apply_safe=True)
        r = next_steps(self.ws)
        self.assertEqual(r["stage"], "逐字段整理")
        card = task_card(self.ws, pid)
        self.assertTrue(card["required"])
        self.assertIn("text", card["search_cmd"])
        self.assertEqual(card["patch_template"][0]["field"], card["field"])
        card = task_card(self.ws, pid, "deductible")
        self.assertTrue(card["candidates"])  # 有候选原文
        path = write_template(self.ws, pid)
        items = json.loads(path.read_text(encoding="utf-8"))
        ded = next(i for i in items if i["field"] == "deductible")
        self.assertTrue(ded["evidence"])
        ded["value"] = "一般医疗年免赔额1.5万元"
        ded["evidence"] = [{"doc": "01_", "quote": "本合同一般医疗保险金的年免赔额为1.5万元。"}]
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        cleaned = load_patch(path)  # 未填项与说明键被忽略
        self.assertTrue(all(not k.startswith("_") for it in cleaned for k in it))
        res = apply_patch(self.ws, pid, cleaned)
        self.assertTrue(all(r["applied"] for r in res), res)
        self.assertEqual(load_record(self.ws, pid)["fields"]["deductible"]["status"], "原文核验")

        # 其余字段处理完 → 校验 → 对比与创新点 → 对照不足 → 补充同业对照（或说明原因跳过）→ 撰写对比判断
        import medins
        from medins_lib.compare import run_compare
        from medins_lib.innovation import run_innovation
        from medins_lib.validate import validate_all
        rest = [{"field": f, "status": "不适用", "note": "测试"} for f in load_record(self.ws, pid)["fields"]
                if load_record(self.ws, pid)["fields"][f]["status"] in ("未填", "规则抽取") and f != "preexisting_general"]
        rest.append({"field": "preexisting_general", "status": "推定", "value": "一般既往症可赔，首年按约定比例×50%", "note": "测试"})
        self.assertTrue(all(r["applied"] for r in apply_patch(self.ws, pid, rest)))
        self.assertEqual(next_steps(self.ws)["stage"], "校验")
        self.assertEqual(validate_all(self.ws)["totals"]["errors"], 0)
        self.assertEqual(next_steps(self.ws)["stage"], "对比与创新点")
        run_compare(self.ws)
        run_innovation(self.ws)
        r = next_steps(self.ws)
        self.assertEqual(r["stage"], "全网核验创新点")
        self.assertIn("general_pre_payable", r["steps"][0]["do"])
        self.assertIn("market add", r["steps"][1]["cmd"])
        from medins_lib.market import pending_features
        for f in pending_features(self.ws):
            self.assertEqual(medins.main(["market", "none", "-w", str(self.ws.root), "--feature", f["key"],
                                          "--query", f"{f['label']} 医疗险", "--query", f"{f['label']} 条款", "--note", "官网与行业协会产品库"]), 0)
        self.assertEqual(next_steps(self.ws)["stage"], "重新识别创新点")
        run_innovation(self.ws)
        self.assertEqual(next_steps(self.ws)["stage"], "撰写对比判断")
        # 登记同业对照后：先整理同业产品（只需条款与对照字段），整理完再要求重新识别创新点
        self.ws.register({"company": "另一家健康", "product_name": "另一款免健告医疗", "role": "peer"})
        r = next_steps(self.ws)
        self.assertEqual(r["stage"], "收集资料")
        self.assertIn("同业对照", r["steps"][0]["do"])


if __name__ == "__main__":
    unittest.main()
