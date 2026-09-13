from __future__ import annotations

import copy
import unittest

from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline_acceptance_ledger import (
    apply_results,
    freeze_inventory,
    new_ledger,
    summary,
    validate_inventory,
)


SHA = "a" * 64


def inventory(*, deferred: bool = False) -> dict:
    checks = [{
        "id": "CHECK-1", "target_id": "PACK", "criterion": "All entries are present.",
        "method": "mechanical", "source_ranges": [{
            "source_id": "SRC-1", "source_sha256": SHA, "start": 0, "end": 9,
        }],
    }, {
        "id": "CHECK-2", "target_id": "DELIVERY", "criterion": "Reader route is usable.",
        "method": "deferred" if deferred else "semantic", "source_ranges": [{
            "source_id": "SRC-2", "source_sha256": SHA, "start": 3, "end": 12,
        }], "metadata": {"audience": "reader"},
    }]
    return freeze_inventory(
        [{"id": "PACK", "kind": "candidate", "files": ["pack.md"]},
         {"id": "DELIVERY", "kind": "delivery", "route": "web"}],
        checks,
        {"candidate_manifest": SHA, "source_manifest": SHA},
    )


def evidence_ref(path: str = "reports/attempt-1.json") -> dict:
    return {"path": path, "sha256": "b" * 64}


def result(check_id: str, *, status: str = "complete", outcome: str = "pass") -> dict:
    return {
        "check_id": check_id, "status": status, "outcome": outcome,
        "reason": "Synthetic direct inspection result.",
        "evidence": [{"source_id": "SRC-1", "detail": "observed"}],
        "reader_impact": "A reader would receive an incomplete Pack." if outcome in {"defect", "unresolved"} else "",
    }


class AcceptanceLedgerTests(unittest.TestCase):
    def test_omissions_remain_pending_and_keep_coverage_incomplete(self):
        frozen = inventory()
        ledger = apply_results(frozen, new_ledger(frozen), [result("CHECK-1")], evidence_ref())

        observed = summary(frozen, ledger)

        self.assertEqual(observed["status"], "inconclusive")
        self.assertEqual(observed["coverage"], "incomplete")
        self.assertEqual(observed["completed_check_ids"], ["CHECK-1"])
        self.assertEqual(observed["pending_check_ids"], ["CHECK-2"])

    def test_partial_attempt_with_confirmed_defect_fails(self):
        frozen = inventory()
        defect = result("CHECK-1", status="incomplete", outcome="defect")
        ledger = apply_results(frozen, new_ledger(frozen), [defect], evidence_ref())

        observed = summary(frozen, ledger)

        self.assertEqual(observed["status"], "fail")
        self.assertEqual(observed["coverage"], "incomplete")
        self.assertEqual(observed["confirmed_defects"][0]["check_id"], "CHECK-1")
        self.assertEqual(observed["confirmed_defects"][0]["evidence_ref"], evidence_ref())

    def test_incomplete_and_unresolved_context_cannot_pass(self):
        frozen = inventory()
        unfinished = result("CHECK-1", status="incomplete", outcome="unresolved")
        ledger = apply_results(frozen, new_ledger(frozen), [unfinished], evidence_ref())

        observed = summary(frozen, ledger)

        self.assertEqual(observed["status"], "inconclusive")
        self.assertEqual(observed["coverage"], "incomplete")
        self.assertEqual(len(observed["unresolved_suspicions"]), 1)

    def test_attempts_append_in_order_without_mutating_input(self):
        frozen = inventory()
        original = new_ledger(frozen)
        first = apply_results(frozen, original, [result("CHECK-1")], evidence_ref("reports/one.json"))
        second = apply_results(frozen, first, [result("CHECK-2")], evidence_ref("reports/two.json"))

        self.assertEqual(original["attempts"], [])
        self.assertEqual(len(first["attempts"]), 1)
        self.assertEqual([a["evidence_ref"]["path"] for a in second["attempts"]],
                         ["reports/one.json", "reports/two.json"])
        self.assertEqual(summary(frozen, second)["status"], "pass")

    def test_changed_inventory_hash_and_forged_ledger_are_rejected(self):
        frozen = inventory()
        changed = copy.deepcopy(frozen)
        changed["bindings"]["candidate_manifest"] = "c" * 64
        with self.assertRaises(ReadingPackError):
            validate_inventory(changed)
        forged = new_ledger(frozen)
        forged["attempts"].append({"evidence_ref": evidence_ref(), "results": [result("MISSING")]})
        with self.assertRaises(ReadingPackError):
            summary(frozen, forged)

    def test_invalid_evidence_path_and_deferred_result_are_rejected(self):
        frozen = inventory()
        with self.assertRaises(ReadingPackError):
            apply_results(frozen, new_ledger(frozen), [result("CHECK-1")], evidence_ref("../outside.json"))

        deferred = inventory(deferred=True)
        with self.assertRaises(ReadingPackError):
            apply_results(deferred, new_ledger(deferred), [result("CHECK-2")], evidence_ref())
        observed = summary(deferred, new_ledger(deferred))
        self.assertEqual((observed["status"], observed["coverage"]), ("not_run", "not_run"))
        self.assertEqual(observed["pending_check_ids"], ["CHECK-1", "CHECK-2"])

    def test_empty_and_dangling_inventories_are_rejected(self):
        with self.assertRaises(ReadingPackError):
            freeze_inventory([], [], {})
        with self.assertRaises(ReadingPackError):
            freeze_inventory([{"id": "PACK", "kind": "candidate"}], [], {})
        with self.assertRaises(ReadingPackError):
            freeze_inventory(
                [{"id": "PACK", "kind": "candidate"}],
                [{"id": "BAD", "target_id": "MISSING", "criterion": "x", "method": "mechanical",
                  "source_ranges": [{"source_id": "SRC", "source_sha256": SHA, "start": 0, "end": 1}]}],
                {},
            )

    def test_duplicate_check_results_in_one_attempt_are_rejected(self):
        frozen = inventory()
        with self.assertRaises(ReadingPackError):
            apply_results(frozen, new_ledger(frozen), [result("CHECK-1"), result("CHECK-1")], evidence_ref())

    def test_confirmed_defect_cannot_be_erased_by_later_pass(self):
        frozen = inventory()
        ledger = apply_results(frozen, new_ledger(frozen),
                               [result("CHECK-1", outcome="defect")], evidence_ref("reports/defect.json"))
        ledger = apply_results(frozen, ledger, [result("CHECK-1")], evidence_ref("reports/recheck.json"))
        observed = summary(frozen, ledger)

        self.assertEqual(observed["status"], "fail")
        self.assertEqual(len(observed["confirmed_defects"]), 1)
        self.assertEqual(observed["pending_check_ids"], ["CHECK-2"])

    def test_completed_advisory_needs_evidence(self):
        frozen = inventory()
        advisory = result("CHECK-1", outcome="advisory")
        advisory["evidence"] = []
        with self.assertRaises(ReadingPackError):
            apply_results(frozen, new_ledger(frozen), [advisory], evidence_ref())

    def test_later_incomplete_result_returns_a_check_to_pending(self):
        frozen = inventory()
        ledger = apply_results(frozen, new_ledger(frozen), [result("CHECK-1")], evidence_ref("reports/complete.json"))
        ledger = apply_results(frozen, ledger,
                               [result("CHECK-1", status="incomplete", outcome="unresolved")],
                               evidence_ref("reports/incomplete.json"))

        observed = summary(frozen, ledger)

        self.assertEqual(observed["pending_check_ids"], ["CHECK-1", "CHECK-2"])
        self.assertEqual(observed["coverage"], "incomplete")


if __name__ == "__main__":
    unittest.main()
