"""Focused profile and fail-closed checks for PropertySubject source metadata."""
import copy
import unittest

from petrel_native_binary import Node
from petrel_property_history import ROOT_VERSION, XMLNS, property_history_evidence


PID = "3a2f3a24-89aa-4794-938a-aa235cb0643b"


def scalar(name, value):
    return Node(name, content=[value])


def entry(arguments="a + b"):
    raw = {"nbfx_type": 150, "raw_hex": "2e3642c5d86ade48"}
    return Node("entry", {"Version": 3}, [
        scalar("user_name", "user"), scalar("operation", "Run calculation"),
        scalar("arguments", arguments), scalar("begin_time", copy.deepcopy(raw)),
        scalar("end_time", copy.deepcopy(raw)),
        Node("app_version", children=[scalar("version", 6149), scalar("flags", 0)]),
        scalar("build_date", "Oct 17 2024"), scalar("is_editable", False)])


def root(entries=None):
    entries = [entry()] if entries is None else entries
    return Node("PropertySubject", {"xmlns": XMLNS, "Version": list(ROOT_VERSION)}, [
        scalar("min", 0.0), scalar("max", 42.0), scalar("min_gt0", 1.0),
        Node("history", {"Size": len(entries)}, entries)])


class PropertyHistoryEvidenceTests(unittest.TestCase):
    def test_recorded_preserves_repeated_entries_order_and_opaque_values(self):
        node = root([entry("x"), entry("x")])
        before = copy.deepcopy(node)
        result = property_history_evidence(node, PID)
        self.assertEqual(node, before)
        self.assertTrue(result["source_metadata_only"])
        self.assertFalse(result["qualified_generic_property_semantics"])
        self.assertFalse(result["qualified_null_semantics"])
        self.assertEqual(result["cached_statistics"]["status"], "recorded_cached_statistics")
        history = result["history"]
        self.assertEqual((history["status"], history["entry_count"]), ("recorded_native_history", 2))
        self.assertEqual([x["entry_ordinal"] for x in history["entries"]], [0, 1])
        self.assertEqual(history["entries"][0]["arguments"], "x")
        self.assertEqual(history["entries"][0]["begin_time"],
                         {"nbfx_type": 150, "raw_hex": "2e3642c5d86ade48"})
        self.assertEqual(history["entries"][1]["source_locator"],
                         f"Model.ptd/PropertySubject[{PID}]/history/entry[1]")

    def test_missing_optional_evidence_is_independent(self):
        node = root()
        node.children = [x for x in node.children if x.name != "history"]
        result = property_history_evidence(node, PID)
        self.assertEqual(result["history"]["status"], "not_recorded")
        self.assertEqual(result["cached_statistics"]["status"], "recorded_cached_statistics")
        node = root()
        node.children = [x for x in node.children if x.name != "max"]
        result = property_history_evidence(node, PID)
        self.assertEqual(result["cached_statistics"]["status"], "not_recorded")
        self.assertEqual(result["history"]["status"], "recorded_native_history")

    def test_history_size_bool_mismatch_duplicate_and_limit_rejected(self):
        for size in (True, 0, 4097):
            with self.subTest(size=size):
                node = root()
                node.children[-1].attrs["Size"] = size
                self.assertEqual(property_history_evidence(node, PID)["history"]["status"], "unresolved")
        node = root()
        node.children.append(copy.deepcopy(node.children[-1]))
        self.assertEqual(property_history_evidence(node, PID)["history"]["reason"], "duplicate_history")

    def test_unsupported_root_and_entry_profile(self):
        node = root()
        node.attrs["Version"] = [1]
        self.assertEqual(property_history_evidence(node, PID)["history"]["status"], "unresolved")
        node = root()
        node.children[-1].children[0].attrs["Version"] = 4
        self.assertEqual(property_history_evidence(node, PID)["history"]["status"], "unresolved")

    def test_statistics_nonfinite_bool_and_duplicate_rejected_without_losing_history(self):
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value):
                node = root()
                node.children[0].content = [value]
                result = property_history_evidence(node, PID)
                self.assertEqual(result["cached_statistics"]["status"], "unresolved")
                self.assertEqual(result["history"]["status"], "recorded_native_history")
        node = root()
        node.children.append(scalar("min", 2))
        self.assertEqual(property_history_evidence(node, PID)["cached_statistics"]["reason"], "duplicate_min")

    def test_oversize_strings_and_invalid_raw_time_reject_entire_history(self):
        node = root([entry(), entry("é" * 32769)])
        result = property_history_evidence(node, PID)["history"]
        self.assertEqual(result["status"], "unresolved")
        self.assertNotIn("entries", result)
        node = root()
        node.children[-1].children[0].children[3].content = [
            {"nbfx_type": 150, "raw_hex": "00"}]
        self.assertEqual(property_history_evidence(node, PID)["history"]["status"], "unresolved")

    def test_aggregate_history_bound_rejects_complete_record(self):
        node = root([entry("x" * 65536) for _ in range(16)])
        evidence = property_history_evidence(node, PID)["history"]
        self.assertEqual(evidence["status"], "unresolved")
        self.assertEqual(evidence["reason"], "history_total_exceeds_limit")
        self.assertNotIn("entries", evidence)

    def test_expression_is_retained_as_literal(self):
        expression = "__import__('os').system('should-never-run')"
        result = property_history_evidence(root([entry(expression)]), PID)
        self.assertEqual(result["history"]["entries"][0]["arguments"], expression)


if __name__ == "__main__":
    unittest.main()
