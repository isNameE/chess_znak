import unittest
from domain import initial_state, apply, RuleError


class RulesTest(unittest.TestCase):
    def test_generator_skips_manually_entered_identifiers(self):
        s = apply(initial_state(), "scan", {"code": "DEMO-1L-000001"})
        s = apply(s, "fill", {})
        self.assertEqual(len(set(s["draft"])), 15)

    def test_batch_makes_progress_when_one_child_needs_attention(self):
        s = apply(initial_state(), "settings", {"one": 1, "five": 1, "pallet": 2})
        s = apply(s, "fill-pallet", {})
        child_id = s["documents"][0]["id"]
        s = apply(s, "send", {"id": child_id, "outcome": "unknown"})
        s = apply(s, "close-pallet", {})
        s = apply(s, "send-all", {})
        self.assertEqual(sorted(d["status"] for d in s["documents"]), ["accepted", "queued", "unknown"])

    def test_duplicate_and_wrong_product_are_atomic(self):
        s = apply(initial_state(), "scan", {"code": "DEMO-1L-FIRST"})
        for code in ("DEMO-1L-FIRST", "DEMO-5L-OTHER", "invalid"):
            with self.assertRaises(RuleError):
                apply(s, "scan", {"code": code})
            self.assertEqual(s["draft"], ["DEMO-1L-FIRST"])

    def test_complete_flow_and_parent_dependency(self):
        s = apply(initial_state(), "settings", {"one": 2, "five": 1, "pallet": 2})
        s = apply(s, "fill-pallet", {})
        self.assertEqual(len(s["boxes"]), 2)
        self.assertEqual(len({c for b in s["boxes"] for c in b["codes"]}), 4)
        with self.assertRaises(RuleError):
            apply(s, "add-box", {"id": s["boxes"][0]["id"]})
        s = apply(s, "close-pallet", {})
        with self.assertRaises(RuleError):
            apply(s, "send", {"id": s["documents"][0]["id"]})
        s = apply(s, "send-all", {})
        self.assertTrue(all(d["status"] == "accepted" for d in s["documents"]))
        self.assertTrue(all(b["pallet"] == s["pallets"][0]["id"] for b in s["boxes"]))

    def test_timeout_blocks_resend_and_print_keeps_id(self):
        s = apply(apply(initial_state(), "fill", {}), "close-box", {})
        doc_id, box_id = s["documents"][0]["id"], s["boxes"][0]["id"]
        s = apply(s, "send", {"id": doc_id, "outcome": "unknown"})
        with self.assertRaises(RuleError):
            apply(s, "send", {"id": doc_id})
        s = apply(s, "reconcile", {"id": doc_id})
        self.assertEqual(s["documents"][0]["attempts"], 1)
        for _ in range(2):
            s = apply(s, "print", {"id": box_id})
        self.assertEqual(s["boxes"][0]["id"], box_id)
        self.assertEqual(s["boxes"][0]["prints"], 2)
        self.assertEqual(len(s["documents"]), 1)

    def test_settings_and_partial_box_guards(self):
        s = apply(initial_state(), "scan", {"code": "DEMO-1L-FIRST"})
        for action, data in [("close-box", {}), ("product", {"product": "5L"}),
                             ("settings", {"one": 1, "five": 1, "pallet": 1}), ("fill-pallet", {})]:
            with self.assertRaises(RuleError):
                apply(s, action, data)

    def test_offline_and_rejected_retry(self):
        s = apply(apply(initial_state(), "fill", {}), "close-box", {})
        doc_id = s["documents"][0]["id"]
        s = apply(s, "online", {"online": False})
        with self.assertRaises(RuleError):
            apply(s, "send", {"id": doc_id})
        self.assertEqual(s["documents"][0]["attempts"], 0)
        s = apply(s, "online", {"online": True})
        s = apply(s, "send", {"id": doc_id, "outcome": "rejected"})
        s = apply(s, "send", {"id": doc_id})
        self.assertEqual(s["documents"][0]["attempts"], 2)

    def test_existing_code_cannot_enter_second_box(self):
        s = apply(apply(initial_state(), "fill", {}), "close-box", {})
        with self.assertRaises(RuleError):
            apply(s, "scan", {"code": s["boxes"][0]["codes"][0]})

    def test_print_failure_and_retry_keep_composition_and_identifier(self):
        s = apply(apply(initial_state(), "fill", {}), "close-box", {})
        box_id = s["boxes"][0]["id"]
        codes = list(s["boxes"][0]["codes"])
        s = apply(s, "print-error", {"id":box_id})
        self.assertEqual(s["printJobs"][0]["status"], "failed")
        s = apply(s, "print", {"id":box_id})
        self.assertEqual(s["printJobs"][0]["status"], "preview_opened")
        self.assertEqual(s["boxes"][0]["codes"], codes)
        self.assertEqual(s["boxes"][0]["id"], box_id)


if __name__ == "__main__":
    unittest.main()
