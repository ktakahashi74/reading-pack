from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reading_pack.artifact_transaction import (
    ArtifactChange,
    apply_artifact_transaction,
    artifact_hash,
    recover_artifact_transaction,
)
from reading_pack.errors import ReadingPackError
from reading_pack.project import write_json
from tests.support import read_json


def _policy(path: str, kind: str) -> bool:
    return (path == "data.json" and kind == "json") or (
        path == "state.json" and kind == "json"
    )


class ArtifactTransactionTests(unittest.TestCase):
    def test_apply_replaces_all_artifacts_and_clears_prepared_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            write_json(project / "data.json", {"value": 1})
            write_json(project / "state.json", {"state": "before"})
            apply_artifact_transaction(
                project,
                prepared_name="test-prepared.json",
                changes=[
                    ArtifactChange("data.json", "json", {"value": 1}, {"value": 2}),
                    ArtifactChange(
                        "state.json",
                        "json",
                        {"state": "before"},
                        {"state": "after"},
                    ),
                ],
                path_policy=_policy,
                label="test",
            )
            self.assertEqual(read_json(project / "data.json"), {"value": 2})
            self.assertEqual(read_json(project / "state.json"), {"state": "after"})
            self.assertFalse((project / ".reading-pack/test-prepared.json").exists())

    def test_validation_failure_restores_existing_and_missing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            write_json(project / "data.json", {"value": 1})

            def reject() -> None:
                raise ReadingPackError("invalid result")

            with self.assertRaisesRegex(ReadingPackError, "invalid result"):
                apply_artifact_transaction(
                    project,
                    prepared_name="test-prepared.json",
                    changes=[
                        ArtifactChange(
                            "data.json", "json", {"value": 1}, {"value": 2}
                        ),
                        ArtifactChange(
                            "state.json",
                            "json",
                            {},
                            {"state": "after"},
                            before_exists=False,
                        ),
                    ],
                    path_policy=_policy,
                    label="test",
                    validate_after=reject,
                )
            self.assertEqual(read_json(project / "data.json"), {"value": 1})
            self.assertFalse((project / "state.json").exists())
            self.assertFalse((project / ".reading-pack/test-prepared.json").exists())

    def test_recovery_accepts_only_exact_before_or_after_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            write_json(project / "data.json", {"value": 2})
            write_json(
                project / ".reading-pack/test-prepared.json",
                {
                    "schema_version": 1,
                    "artifacts": [
                        {
                            "path": "data.json",
                            "kind": "json",
                            "before": {"value": 1},
                            "before_exists": True,
                            "after_sha256": artifact_hash("json", {"value": 2}),
                        }
                    ],
                },
            )
            self.assertTrue(
                recover_artifact_transaction(
                    project,
                    prepared_name="test-prepared.json",
                    path_policy=_policy,
                    label="test",
                )
            )
            self.assertEqual(read_json(project / "data.json"), {"value": 1})

    def test_recovery_rejects_unknown_edit_without_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            write_json(project / "data.json", {"value": 3})
            write_json(
                project / ".reading-pack/test-prepared.json",
                {
                    "schema_version": 1,
                    "artifacts": [
                        {
                            "path": "data.json",
                            "kind": "json",
                            "before": {"value": 1},
                            "before_exists": True,
                            "after_sha256": artifact_hash("json", {"value": 2}),
                        }
                    ],
                },
            )
            with self.assertRaisesRegex(ReadingPackError, "unknown edit"):
                recover_artifact_transaction(
                    project,
                    prepared_name="test-prepared.json",
                    path_policy=_policy,
                    label="test",
                )
            self.assertEqual(read_json(project / "data.json"), {"value": 3})


if __name__ == "__main__":
    unittest.main()
