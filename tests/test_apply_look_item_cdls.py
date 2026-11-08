"""timeline.apply_look_to_items with item_cdls: a different CDL per item in one call."""
import unittest

import src.server as compound


class _NodeGraphStub:
    def __init__(self, num_nodes):
        self._num_nodes = num_nodes

    def GetNumNodes(self):
        return self._num_nodes


class _ItemStub:
    def __init__(self, uid, num_nodes=1, set_cdl_result=True):
        self._uid = uid
        self._num_nodes = num_nodes
        self._set_cdl_result = set_cdl_result
        self.set_cdl_calls = []

    def GetUniqueId(self):
        return self._uid

    def GetName(self):
        return f"clip-{self._uid}"

    def GetNodeGraph(self):
        return _NodeGraphStub(self._num_nodes)

    def SetCDL(self, cdl):
        if isinstance(self._set_cdl_result, Exception):
            raise self._set_cdl_result
        self.set_cdl_calls.append(cdl)
        return self._set_cdl_result


class _TimelineStub:
    def __init__(self, items):
        self._items = items

    def GetTrackCount(self, track_type):
        return 1 if track_type == "video" else 0

    def GetItemListInTrack(self, track_type, idx):
        return list(self._items) if track_type == "video" and idx == 1 else []


def _cdl(slope, node_index=1):
    return {"NodeIndex": node_index, "Slope": [slope, slope, slope],
            "Offset": [0.0, 0.0, 0.0], "Power": [1.0, 1.0, 1.0], "Saturation": 1.0}


def _apply(items, params):
    return compound._timeline_apply_look_to_items(_TimelineStub(items), params)


class ItemCdlsTests(unittest.TestCase):
    def test_item_cdls_applies_each_items_own_cdl(self):
        a, b = _ItemStub("A"), _ItemStub("B")
        cdl_a, cdl_b = _cdl(1.1), _cdl(0.9)
        out = _apply([a, b], {"item_cdls": [
            {"timeline_item_id": "A", "cdl": cdl_a},
            {"timeline_item_id": "B", "cdl": cdl_b},
        ]})
        self.assertTrue(out["success"], out)
        self.assertEqual(a.set_cdl_calls, [compound._normalize_cdl(cdl_a)])
        self.assertEqual(b.set_cdl_calls, [compound._normalize_cdl(cdl_b)])
        self.assertNotEqual(a.set_cdl_calls, b.set_cdl_calls)
        self.assertEqual([row["timeline_item_id"] for row in out["cdl_results"]], ["A", "B"])
        self.assertNotIn("partial", out)

    def test_item_cdls_invalid_entry_writes_nothing(self):
        a, b = _ItemStub("A"), _ItemStub("B")
        bad = _cdl(1.0)
        bad["Slope"] = [1.0, 1.0]
        out = _apply([a, b], {"item_cdls": [
            {"timeline_item_id": "A", "cdl": _cdl(1.1)},
            {"timeline_item_id": "B", "cdl": bad},
        ]})
        self.assertIs(out["success"], False)
        self.assertEqual(a.set_cdl_calls, [])
        self.assertEqual(b.set_cdl_calls, [])
        invalid = [row for row in out["validation"] if not row["valid"]]
        self.assertEqual([row["timeline_item_id"] for row in invalid], ["B"])

    def test_item_cdls_non_object_cdl_reported_per_entry(self):
        a, b = _ItemStub("A"), _ItemStub("B")
        out = _apply([a, b], {"item_cdls": [
            {"timeline_item_id": "A", "cdl": _cdl(1.1)},
            {"timeline_item_id": "B", "cdl": "not-a-cdl"},
        ]})
        self.assertIs(out["success"], False)
        self.assertEqual(a.set_cdl_calls, [])
        invalid = [row for row in out["validation"] if not row["valid"]]
        self.assertEqual([row["timeline_item_id"] for row in invalid], ["B"])

    def test_item_cdls_missing_id_writes_nothing(self):
        a = _ItemStub("A")
        out = _apply([a], {"item_cdls": [
            {"timeline_item_id": "A", "cdl": _cdl(1.1)},
            {"timeline_item_id": "GHOST", "cdl": _cdl(0.9)},
        ]})
        self.assertIs(out["success"], False)
        self.assertIn("GHOST", out["missing"])
        self.assertEqual(a.set_cdl_calls, [])

    def test_item_cdls_bad_node_on_second_item_writes_nothing(self):
        a, b = _ItemStub("A", num_nodes=3), _ItemStub("B", num_nodes=1)
        out = _apply([a, b], {"item_cdls": [
            {"timeline_item_id": "A", "cdl": _cdl(1.1, node_index=2)},
            {"timeline_item_id": "B", "cdl": _cdl(0.9, node_index=2)},
        ]})
        self.assertIs(out["success"], False)
        self.assertEqual(a.set_cdl_calls, [])
        self.assertEqual(b.set_cdl_calls, [])
        failed = [row for row in out["node_preflight"] if row.get("reason")]
        self.assertEqual([row["timeline_item_id"] for row in failed], ["B"])

    def test_item_cdls_runtime_failure_reports_partial(self):
        for label, middle_result in (("returns_false", False),
                                     ("raises", RuntimeError("bridge dropped"))):
            with self.subTest(label):
                a = _ItemStub("A")
                b = _ItemStub("B", set_cdl_result=middle_result)
                c = _ItemStub("C")
                out = _apply([a, b, c], {"item_cdls": [
                    {"timeline_item_id": "A", "cdl": _cdl(1.1)},
                    {"timeline_item_id": "B", "cdl": _cdl(1.0)},
                    {"timeline_item_id": "C", "cdl": _cdl(0.9)},
                ]})
                self.assertEqual(len(a.set_cdl_calls), 1)
                self.assertEqual(len(c.set_cdl_calls), 1)
                self.assertIs(out["partial"], True)
                self.assertIs(out["success"], False)
                rows = {row["timeline_item_id"]: row for row in out["cdl_results"]}
                self.assertEqual(set(rows), {"A", "B", "C"})
                self.assertIs(rows["B"]["set_cdl"], False)
                if label == "raises":
                    self.assertIn("bridge dropped", rows["B"]["error"])
                else:
                    self.assertIn("diagnosis", rows["B"])

    def test_item_cdls_rejects_mixed_modes(self):
        a = _ItemStub("A")
        entries = [{"timeline_item_id": "A", "cdl": _cdl(1.1)}]
        for extra in ({"cdl": _cdl(1.0)}, {"source_item_id": "A"}):
            with self.subTest(extra=sorted(extra)):
                out = _apply([a], {"item_cdls": entries, **extra})
                self.assertFalse(out.get("success"))
                self.assertIn("item_cdls", out["error"]["message"])
                self.assertIn(next(iter(extra)), out["error"]["message"])
        self.assertEqual(a.set_cdl_calls, [])

    def test_item_cdls_rejects_empty_and_duplicates(self):
        a = _ItemStub("A")
        out = _apply([a], {"item_cdls": []})
        self.assertFalse(out.get("success"))
        self.assertIn("item_cdls", out["error"]["message"])
        out = _apply([a], {"item_cdls": [
            {"timeline_item_id": "A", "cdl": _cdl(1.1)},
            {"timeline_item_id": "A", "cdl": _cdl(0.9)},
        ]})
        self.assertFalse(out.get("success"))
        self.assertIn("duplicate", out["error"]["message"])
        self.assertIn("A", out["error"]["message"])
        self.assertEqual(a.set_cdl_calls, [])

    def test_item_cdls_dry_run_preflights_each_item(self):
        a, b = _ItemStub("A"), _ItemStub("B")
        out = _apply([a, b], {"dry_run": True, "item_cdls": [
            {"timeline_item_id": "A", "cdl": _cdl(1.1)},
            {"timeline_item_id": "B", "cdl": _cdl(0.9)},
        ]})
        self.assertTrue(out["success"], out)
        self.assertIs(out["dry_run"], True)
        self.assertEqual([row["timeline_item_id"] for row in out["node_preflight"]], ["A", "B"])
        self.assertNotIn("cdl_results", out)
        self.assertEqual(a.set_cdl_calls, [])
        self.assertEqual(b.set_cdl_calls, [])

    def test_target_ids_path_unchanged(self):
        a, b = _ItemStub("A"), _ItemStub("B")
        cdl = _cdl(1.05)
        out = _apply([a, b], {"target_ids": ["A", "B"], "cdl": cdl})
        self.assertTrue(out["success"], out)
        self.assertEqual(a.set_cdl_calls, [compound._normalize_cdl(cdl)])
        self.assertEqual(b.set_cdl_calls, [compound._normalize_cdl(cdl)])


if __name__ == "__main__":
    unittest.main()
