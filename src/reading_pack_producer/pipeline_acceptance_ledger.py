"""Frozen direct-inspection inventory and append-only acceptance ledger."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from reading_pack.errors import ReadingPackError

from .work_ledger import artifact_hash


_SHA256 = re.compile(r"[a-f0-9]{64}")
_METHODS = {"mechanical", "semantic", "deferred"}
_RESULT_STATUSES = {"complete", "incomplete"}
_OUTCOMES = {"pass", "defect", "unresolved", "advisory"}
_INVENTORY_KEYS = {"version", "bindings", "targets", "checks", "sha256"}
_LEDGER_KEYS = {"inventory_sha256", "attempts"}
_ATTEMPT_KEYS = {"evidence_ref", "results"}
_RESULT_KEYS = {"check_id", "status", "outcome", "reason", "evidence", "reader_impact"}
_EVIDENCE_REF_KEYS = {"path", "sha256"}


def _fail(message: str) -> None:
    raise ReadingPackError(message)


def _json_value(value: Any, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        _fail(f"{label} must be JSON data: {exc}")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a nonempty string")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(f"{label} must be a lowercase SHA-256")
    return value


def _exact_keys(value: Any, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        _fail(f"{label} has an invalid shape")
    return value


def _validate_targets(targets: Any) -> dict[str, dict]:
    if not isinstance(targets, list) or not targets:
        _fail("inventory targets must be a nonempty list")
    indexed: dict[str, dict] = {}
    for target in targets:
        if not isinstance(target, dict):
            _fail("inventory target must be an object")
        target_id = _nonempty_string(target.get("id"), "target id")
        _nonempty_string(target.get("kind"), "target kind")
        _json_value(target, "target")
        if target_id in indexed:
            _fail("duplicate target id")
        indexed[target_id] = target
    return indexed


def _validate_source_ranges(value: Any) -> None:
    if not isinstance(value, list):
        _fail("check source_ranges must be a list")
    for source_range in value:
        _exact_keys(source_range, {"source_id", "source_sha256", "start", "end"}, "source range")
        _nonempty_string(source_range["source_id"], "source range source_id")
        _sha256(source_range["source_sha256"], "source range source_sha256")
        start, end = source_range["start"], source_range["end"]
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            _fail("source range positions must be integers")
        if start < 0 or end <= start:
            _fail("source range positions are invalid")


def _validate_checks(checks: Any, targets: dict[str, dict]) -> dict[str, dict]:
    if not isinstance(checks, list) or not checks:
        _fail("inventory checks must be a nonempty list")
    indexed: dict[str, dict] = {}
    checked_targets: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            _fail("inventory check must be an object")
        allowed = {"id", "target_id", "criterion", "method", "source_ranges", "metadata"}
        if not {"id", "target_id", "criterion", "method", "source_ranges"} <= set(check) or set(check) - allowed:
            _fail("inventory check has an invalid shape")
        check_id = _nonempty_string(check["id"], "check id")
        target_id = _nonempty_string(check["target_id"], "check target_id")
        _nonempty_string(check["criterion"], "check criterion")
        if check["method"] not in _METHODS:
            _fail("check method is invalid")
        if target_id not in targets:
            _fail("check has a dangling target")
        _validate_source_ranges(check["source_ranges"])
        if "metadata" in check:
            _json_value(check["metadata"], "check metadata")
        if check_id in indexed:
            _fail("duplicate check id")
        indexed[check_id] = check
        checked_targets.add(target_id)
    if set(targets) != checked_targets:
        _fail("every inventory target needs a check")
    return indexed


def _inventory_projection(inventory: dict) -> dict:
    return {key: inventory[key] for key in ("version", "bindings", "targets", "checks")}


def freeze_inventory(targets: list[dict], checks: list[dict], bindings: dict) -> dict:
    """Validate and hash the exact inventory required for one candidate."""
    if not isinstance(bindings, dict):
        _fail("inventory bindings must be an object")
    _json_value(bindings, "inventory bindings")
    copied_targets, copied_checks, copied_bindings = copy.deepcopy(targets), copy.deepcopy(checks), copy.deepcopy(bindings)
    target_index = _validate_targets(copied_targets)
    _validate_checks(copied_checks, target_index)
    projection = {"version": 1, "bindings": copied_bindings, "targets": copied_targets, "checks": copied_checks}
    return {**projection, "sha256": artifact_hash(projection)}


def validate_inventory(inventory: dict) -> None:
    _exact_keys(inventory, _INVENTORY_KEYS, "inventory")
    if type(inventory["version"]) is not int or inventory["version"] != 1:
        _fail("inventory version is invalid")
    if not isinstance(inventory["bindings"], dict):
        _fail("inventory bindings must be an object")
    _json_value(inventory["bindings"], "inventory bindings")
    targets = _validate_targets(inventory["targets"])
    _validate_checks(inventory["checks"], targets)
    _sha256(inventory["sha256"], "inventory sha256")
    if artifact_hash(_inventory_projection(inventory)) != inventory["sha256"]:
        _fail("inventory sha256 does not match its contents")


def new_ledger(inventory: dict) -> dict:
    validate_inventory(inventory)
    return {"inventory_sha256": inventory["sha256"], "attempts": []}


def _validate_evidence_ref(evidence_ref: Any) -> None:
    _exact_keys(evidence_ref, _EVIDENCE_REF_KEYS, "evidence_ref")
    path = _nonempty_string(evidence_ref["path"], "evidence_ref path")
    if "\\" in path or ":" in path or path.startswith("/") or path.endswith("/"):
        _fail("evidence_ref path must be a relative POSIX path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail("evidence_ref path must not traverse directories")
    _sha256(evidence_ref["sha256"], "evidence_ref sha256")


def _validate_result(result: Any, check_index: dict[str, dict]) -> str:
    _exact_keys(result, _RESULT_KEYS, "result")
    check_id = _nonempty_string(result["check_id"], "result check_id")
    check = check_index.get(check_id)
    if check is None:
        _fail("result references an unknown check")
    if check["method"] == "deferred":
        _fail("deferred checks cannot receive results")
    if result["status"] not in _RESULT_STATUSES or result["outcome"] not in _OUTCOMES:
        _fail("result status or outcome is invalid")
    _nonempty_string(result["reason"], "result reason")
    if not isinstance(result["evidence"], list):
        _fail("result evidence must be a list")
    _json_value(result["evidence"], "result evidence")
    if result["status"] == "complete" or result["outcome"] == "defect":
        if not result["evidence"]:
            _fail("completed passes and defects need evidence")
    if result["outcome"] in {"defect", "unresolved"}:
        _nonempty_string(result["reader_impact"], "result reader_impact")
    elif not isinstance(result["reader_impact"], str):
        _fail("result reader_impact must be a string")
    _json_value(result, "result")
    return check_id


def _validate_attempt(attempt: Any, check_index: dict[str, dict]) -> None:
    _exact_keys(attempt, _ATTEMPT_KEYS, "ledger attempt")
    _validate_evidence_ref(attempt["evidence_ref"])
    if not isinstance(attempt["results"], list):
        _fail("ledger attempt results must be a list")
    seen: set[str] = set()
    for result in attempt["results"]:
        check_id = _validate_result(result, check_index)
        if check_id in seen:
            _fail("duplicate check result in one attempt")
        seen.add(check_id)


def _validate_ledger(inventory: dict, ledger: Any) -> None:
    _exact_keys(ledger, _LEDGER_KEYS, "ledger")
    if ledger["inventory_sha256"] != inventory["sha256"]:
        _fail("ledger is bound to a different inventory")
    if not isinstance(ledger["attempts"], list):
        _fail("ledger attempts must be a list")
    check_index = {check["id"]: check for check in inventory["checks"]}
    for attempt in ledger["attempts"]:
        _validate_attempt(attempt, check_index)


def apply_results(inventory: dict, ledger: dict, results: list[dict], evidence_ref: dict) -> dict:
    """Append one verified attempt without mutating either supplied object."""
    validate_inventory(inventory)
    _validate_ledger(inventory, ledger)
    _validate_evidence_ref(evidence_ref)
    if not isinstance(results, list):
        _fail("results must be a list")
    check_index = {check["id"]: check for check in inventory["checks"]}
    attempt = {"evidence_ref": copy.deepcopy(evidence_ref), "results": copy.deepcopy(results)}
    _validate_attempt(attempt, check_index)
    output = copy.deepcopy(ledger)
    output["attempts"].append(attempt)
    return output


def summary(inventory: dict, ledger: dict) -> dict:
    """Replay the append-only ledger against its frozen inventory."""
    validate_inventory(inventory)
    _validate_ledger(inventory, ledger)
    completed: set[str] = set()
    defects, unresolved, advisories = [], {}, []
    for attempt in ledger["attempts"]:
        for result in attempt["results"]:
            if result["status"] == "complete":
                completed.add(result["check_id"])
            else:
                completed.discard(result["check_id"])
            tagged = {**copy.deepcopy(result), "evidence_ref": copy.deepcopy(attempt["evidence_ref"])}
            if result["status"] == "complete" and result["outcome"] != "unresolved":
                unresolved.pop(result["check_id"], None)
            if result["outcome"] == "defect":
                defects.append(tagged)
            elif result["outcome"] == "unresolved":
                unresolved[result["check_id"]] = tagged
            elif result["outcome"] == "advisory":
                advisories.append(tagged)
    check_ids = [check["id"] for check in inventory["checks"]]
    completed_ids = [check_id for check_id in check_ids if check_id in completed]
    pending_ids = [check_id for check_id in check_ids if check_id not in completed]
    coverage = "not_run" if not ledger["attempts"] else ("complete" if not pending_ids else "incomplete")
    if defects:
        status = "fail"
    elif coverage == "not_run":
        status = "not_run"
    elif coverage == "incomplete" or unresolved:
        status = "inconclusive"
    else:
        status = "pass"
    return {
        "status": status,
        "coverage": coverage,
        "completed_check_ids": completed_ids,
        "pending_check_ids": pending_ids,
        "confirmed_defects": defects,
        "unresolved_suspicions": list(unresolved.values()),
        "advisories": advisories,
    }
