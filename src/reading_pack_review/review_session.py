"""Shared session model for the human-editable Markdown author review."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .author_review import (
    LABEL_FIELDS,
    LIST_FIELDS,
    MAX_COMMENT_CHARACTERS,
    _author_input_state,
    _json_hash,
    _load_review_evidence,
    _record_index,
    _require_keys,
    _SAFE_LINE,
)
from reading_pack.errors import ReadingPackError
from reading_pack.hashing import semantic_hash
from reading_pack.project import load_config, load_language_data
from reading_pack.rendering import render_pack


SESSION_SCHEMA_VERSION = 1
RESPONSE_SCHEMA_VERSION = 1
MAX_CORRECTION_CHARACTERS = 100_000

COLLECTION_LABELS = {
    "chapters": "章・要約",
    "certainty": "確実性区分",
    "claims": "命題",
    "misreadings": "読解上の論点と応答",
    "policies": "本書固有方針",
    "names": "人名索引",
    "glossary": "用語索引",
    "references": "参照先",
}

QUESTION_ANSWERS = ("accept", "needs_work", "hold")

# These recommendations help a person review the form. They are not decisions;
# the edited human review remains the evidence.
ASSISTANT_QUESTION_POLICY = {
    "system-no-unavailable-text": {
        "recommended_answer": "accept",
        "requires_user_judgment": False,
        "reason": "The rendered pack explicitly denies access to text that was not supplied.",
    },
    "system-no-false-attribution": {
        "recommended_answer": "accept",
        "requires_user_judgment": False,
        "reason": "The system rules separate the book's position from outside knowledge and inference.",
    },
    "system-non-reconstruction": {
        "recommended_answer": "accept",
        "requires_user_judgment": False,
        "reason": "The pack is structured as routing and bounded explanation rather than substitute book text.",
    },
    "language-position": {
        "recommended_answer": "accept",
        "requires_user_judgment": True,
        "reason": "Only the rights holder can confirm the intended status of each language edition.",
    },
    "reference-routing": {
        "recommended_answer": "accept",
        "requires_user_judgment": True,
        "reason": "The agent can inspect recorded destinations, but official status may depend on external facts.",
    },
    "rights-and-terms": {
        "recommended_answer": "accept",
        "requires_user_judgment": True,
        "reason": "Rights, official status, and license authority cannot be established by the pack alone.",
    },
    "public-preview": {
        "recommended_answer": "accept",
        "requires_user_judgment": False,
        "reason": "The agent can inspect the complete rendered outputs and surface concrete anomalies.",
    },
    "gap-name-context": {
        "recommended_answer": "accept",
        "requires_user_judgment": True,
        "reason": "Keeping a locator-only index avoids inventing book-specific context, but the edition policy belongs to the owner.",
    },
    "gap-glossary-meaning": {
        "recommended_answer": "accept",
        "requires_user_judgment": True,
        "reason": "Keeping a locator-only index avoids inventing book-specific meanings, but the edition policy belongs to the owner.",
    },
    "gap-unbound-misreadings": {
        "recommended_answer": "accept",
        "requires_user_judgment": True,
        "reason": "Leaving an uncertain relationship unbound is safer than guessing, but the edition policy belongs to the owner.",
    },
}


def _question(
    identifier: str,
    title: str,
    prompt: str,
    detail: str,
    *,
    category: str,
    required_for_signoff: bool,
    count: int | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "question_id": identifier,
        "title": title,
        "prompt": prompt,
        "detail": detail,
        "category": category,
        "required_for_signoff": required_for_signoff,
    }
    if count is not None:
        value["count"] = count
    assistant_policy = ASSISTANT_QUESTION_POLICY[identifier]
    value["assistant_recommendation"] = assistant_policy["recommended_answer"]
    value["requires_user_judgment"] = assistant_policy["requires_user_judgment"]
    value["assistant_reason"] = assistant_policy["reason"]
    return value


def _review_questions(
    config: Mapping[str, Any], data_by_lang: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    questions = [
        _question(
            "system-no-unavailable-text",
            "未提供本文へのアクセス",
            "このReading Packは、収録されていない本文の検索・引用・再構築を約束していない。",
            "読者に、原著全文へアクセスできるという誤解を与えないかを確認します。",
            category="system",
            required_for_signoff=True,
        ),
        _question(
            "system-no-false-attribution",
            "本書への誤帰属",
            "資料外の一般知識や推測を、本書または著者の立場として提示しない。",
            "SYSと実物プレビューを見て、本書の主張と外部知識が分離されているかを確認します。",
            category="system",
            required_for_signoff=True,
        ),
        _question(
            "system-non-reconstruction",
            "原著の代替防止",
            "公開物の総体が、原著本文や章の代替にならない。",
            "要約の連結や追加説明によって、原著を再構築できる状態になっていないかを確認します。",
            category="system",
            required_for_signoff=True,
        ),
        _question(
            "reference-routing",
            "公式参照先",
            "公式ページ、OKF、補助資料への案内が正しい。",
            "未公開先や暫定URLがある場合は「要対応」を選びます。",
            category="publication",
            required_for_signoff=False,
        ),
        _question(
            "rights-and-terms",
            "権利・公式性・利用条件",
            "翻訳権、公式性、ライセンス、利用条件の説明が正しい。",
            f"現在のpack licenseは「{config['book']['pack_license']}」です。ここでの回答は権利許諾そのものではありません。",
            category="publication",
            required_for_signoff=False,
        ),
        _question(
            "public-preview",
            "読者が見る実物",
            "日英の実物プレビューを確認し、内容と見え方に重大な問題がない。",
            "個別データではなく、最終的に読者が受け取るReading Pack全体を確認します。",
            category="preview",
            required_for_signoff=True,
        ),
    ]
    if len(config["languages"]) > 1:
        questions.insert(
            3,
            _question(
                "language-position",
                "日本語版と英語版の位置づけ",
                "日本語正本と英語版の関係、英語版の公式性、翻訳上の限界が明確である。",
                "英語版を公認英訳として扱うか、著者暫定訳として扱うかを含めて確認します。",
                category="system",
                required_for_signoff=True,
            ),
        )

    missing_names = sum(
        not str(record.get("book_context", "")).strip()
        for data in data_by_lang.values()
        for record in data["names"]
    )
    missing_terms = sum(
        not str(record.get("book_meaning", "")).strip()
        for data in data_by_lang.values()
        for record in data["glossary"]
    )
    unbound_qa = sum(
        not record.get("chapter_ids") and not record.get("claim_ids")
        for data in data_by_lang.values()
        for record in data["misreadings"]
    )
    if missing_names:
        questions.append(
            _question(
                "gap-name-context",
                "人名索引の説明",
                "本書内での扱いを説明しないlocator-onlyの人名索引を、この版の方針として受け入れる。",
                "受け入れても公開検査の不足は自動的には解除されません。説明を追加するなら「要対応」を選びます。",
                category="gap",
                required_for_signoff=False,
                count=missing_names,
            )
        )
    if missing_terms:
        questions.append(
            _question(
                "gap-glossary-meaning",
                "用語索引の説明",
                "本書固有の意味を記載しないlocator-onlyの用語索引を、この版の方針として受け入れる。",
                "受け入れても公開検査の不足は自動的には解除されません。説明を追加するなら「要対応」を選びます。",
                category="gap",
                required_for_signoff=False,
                count=missing_terms,
            )
        )
    if unbound_qa:
        questions.append(
            _question(
                "gap-unbound-misreadings",
                "章・命題に未対応の読解上の論点",
                "章または命題との対応を推測で補わず、未対応のまま保持する。",
                "対応関係を著者判断で追加する場合は「要対応」を選びます。",
                category="gap",
                required_for_signoff=False,
                count=unbound_qa,
            )
        )
    by_id = {question["question_id"]: question for question in questions}
    license_text = str(config["book"].get("pack_license", "")).strip()
    if not license_text or "pending" in license_text.lower():
        rights = by_id["rights-and-terms"]
        rights["assistant_recommendation"] = "needs_work"
        rights["assistant_reason"] = (
            "The configured pack license is still a rights-holder decision "
            "pending, so the agent must surface it instead of recommending "
            "acceptance."
        )
    reference_count = sum(
        len(data["references"]) for data in data_by_lang.values()
    )
    if reference_count == 0:
        routing = by_id["reference-routing"]
        routing["assistant_recommendation"] = "needs_work"
        routing["assistant_reason"] = (
            "No reference destination is recorded, so official routing "
            "cannot be accepted."
        )
    return questions


def _record_flags(collection: str, record: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    if collection == "names" and not str(record.get("book_context", "")).strip():
        flags.append("book_contextが未記入")
    if collection == "glossary" and not str(record.get("book_meaning", "")).strip():
        flags.append("book_meaningが未記入")
    if (
        collection == "misreadings"
        and not record.get("chapter_ids")
        and not record.get("claim_ids")
    ):
        flags.append("章・命題との対応が未指定")
    return flags


def build_author_review_session(
    project: Path, review_directory: Path
) -> dict[str, Any]:
    project = Path(project).resolve()
    _, manifest = _load_review_evidence(project, review_directory)
    config = load_config(project)
    data_by_lang = {
        language: load_language_data(project, language)
        for language in config["languages"]
    }
    record_index = _record_index(data_by_lang)
    author_state = _author_input_state(project, config)
    record_scoped = manifest.get("record_ids") is not None

    records: list[dict[str, Any]] = []
    grouped: dict[tuple[Any, ...], list[str]] = {}
    group_details: dict[tuple[Any, ...], dict[str, Any]] = {}
    for unit in manifest["records"]:
        key = (unit["language"], unit["collection"], unit["record_id"])
        record = record_index[key]
        module_state = author_state["languages"][unit["language"]]["modules"][
            unit["module"]
        ]
        provided_hash = module_state.get("provided_record_hashes", {}).get(
            unit["record_id"]
        )
        exact_provided = (
            not record_scoped
            and module_state.get("mode") == "provided"
            # Translation freshness fields are added after the authority record
            # is fingerprinted. semantic_hash deliberately excludes those links.
            and provided_hash == semantic_hash(record)
            and isinstance(module_state.get("source"), Mapping)
        )
        source = module_state.get("source") or {}
        group_key = (
            unit["language"],
            unit["collection"],
            unit["module_state_sha256"],
            bool(exact_provided),
            source.get("id"),
            source.get("sha256"),
        )
        grouped.setdefault(group_key, []).append(unit["unit_id"])
        group_details[group_key] = {
            "language": unit["language"],
            "collection": unit["collection"],
            "module": unit["module"],
            "mode": module_state.get("mode"),
            "package_id": module_state.get("package_id"),
            "authority": deepcopy(module_state.get("authority")),
            "source": deepcopy(module_state.get("source")),
            "bulk_eligible": bool(exact_provided),
            "module_state_sha256": unit["module_state_sha256"],
        }
        values = {
            field: deepcopy(record.get(field, [] if field in LIST_FIELDS else ""))
            for field in unit["editable_fields"]
        }
        label_field = LABEL_FIELDS[unit["collection"]]
        label_value = record.get(label_field, unit["record_id"])
        if unit["collection"] == "misreadings":
            label_value = record.get("issue", record.get("misreading", label_value))
        records.append(
            {
                "unit_id": unit["unit_id"],
                "language": unit["language"],
                "collection": unit["collection"],
                "record_id": unit["record_id"],
                "record_sha256": unit["record_sha256"],
                "label": str(label_value),
                "editable_fields": list(unit["editable_fields"]),
                "values": values,
                "flags": _record_flags(unit["collection"], record),
                "group_id": "",
            }
        )

    groups: list[dict[str, Any]] = []
    group_id_by_unit: dict[str, str] = {}
    for group_key in sorted(grouped, key=lambda value: tuple(str(item) for item in value)):
        details = group_details[group_key]
        unit_ids = grouped[group_key]
        projection = {
            **details,
            "unit_ids": unit_ids,
        }
        group_id = f"ARG-{_json_hash(projection)[:16].upper()}"
        group = {
            "group_id": group_id,
            "title": (
                f"{details['language'].upper()}・"
                f"{COLLECTION_LABELS[details['collection']]}"
            ),
            "count": len(unit_ids),
            "unit_ids": unit_ids,
            **details,
        }
        groups.append(group)
        for unit_id in unit_ids:
            group_id_by_unit[unit_id] = group_id
    for record in records:
        record["group_id"] = group_id_by_unit[record["unit_id"]]

    scoped_modules = manifest.get("modules")
    scoped_record_ids = manifest.get("record_ids")
    # A module-scoped review is deliberately short and cannot grant whole-pack
    # final signoff. Global publication questions and full previews stay in the
    # complete review instead of overwhelming a focused policy decision.
    previews = (
        {}
        if scoped_modules is not None or scoped_record_ids is not None
        else {
            language: render_pack(project, language, config, data_by_lang[language])
            for language in config["languages"]
        }
    )
    questions = (
        []
        if scoped_modules is not None or scoped_record_ids is not None
        else _review_questions(config, data_by_lang)
    )
    session: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "review_id": manifest["review_id"],
        "manifest_sha256": _json_hash(manifest),
        "created_at": manifest["created_at"],
        "slug": config["slug"],
        "version": config["version"],
        "book": {
            "title": config["book"]["title"],
            "author": config["book"]["author"],
            "copyright_holder": config["book"]["copyright_holder"],
            "pack_license": config["book"]["pack_license"],
        },
        "primary_language": config["primary_language"],
        "languages": list(config["languages"]),
        "modules": deepcopy(scoped_modules),
        "record_ids": deepcopy(scoped_record_ids),
        "canonical_data_sha256": manifest["snapshot"]["canonical_data_sha256"],
        "workflow": deepcopy(config["workflow"]),
        "groups": groups,
        "records": records,
        "questions": questions,
        "previews": previews,
    }
    session["session_sha256"] = _json_hash(session)
    return session


def _validate_correction(
    unit: Mapping[str, Any], field: str, value: Any, label: str
) -> dict[str, Any]:
    if field not in unit["editable_fields"]:
        raise ReadingPackError(f"invalid {label}: field is not editable")
    if not isinstance(value, Mapping):
        raise ReadingPackError(f"invalid {label}: correction must be an object")
    operation = value.get("operation")
    if operation == "remove" and set(value) == {"operation"}:
        return {"operation": "remove"}
    if operation != "set" or set(value) != {"operation", "value"}:
        raise ReadingPackError(f"invalid {label}: correction operation")
    corrected = value["value"]
    if field in LIST_FIELDS:
        if (
            not isinstance(corrected, list)
            or len(corrected) > 10_000
            or any(not isinstance(item, str) for item in corrected)
        ):
            raise ReadingPackError(f"invalid {label}: list correction")
        if sum(len(item) for item in corrected) > MAX_CORRECTION_CHARACTERS:
            raise ReadingPackError(f"invalid {label}: correction is too long")
    elif not isinstance(corrected, str) or len(corrected) > MAX_CORRECTION_CHARACTERS:
        raise ReadingPackError(f"invalid {label}: text correction")
    return deepcopy(dict(value))


def validate_author_review_responses(
    value: Any, session: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid author review responses: root must be an object")
    required_fields = {
        "schema_version", "review_id", "session_sha256", "group_decisions",
        "record_decisions", "question_answers", "reviewer", "reviewed_at",
        "final_signoff",
    }
    _require_keys(value, required_fields, required_fields, "author review responses")
    if value["schema_version"] != RESPONSE_SCHEMA_VERSION:
        raise ReadingPackError("invalid author review responses: schema version")
    if value["review_id"] != session["review_id"]:
        raise ReadingPackError("author review responses belong to another review")
    if value["session_sha256"] != session["session_sha256"]:
        raise ReadingPackError("author review responses are stale or the session changed")
    if session.get("modules") and value["final_signoff"] is True:
        raise ReadingPackError(
            "module-scoped author review cannot grant whole-pack final signoff"
        )
    if session.get("record_ids") and value["final_signoff"] is True:
        raise ReadingPackError(
            "record-scoped author review cannot grant whole-pack final signoff"
        )

    groups = {group["group_id"]: group for group in session["groups"]}
    group_decisions: list[dict[str, str]] = []
    seen_groups: set[str] = set()
    if not isinstance(value["group_decisions"], list):
        raise ReadingPackError("invalid author review responses: group decisions")
    for index, item in enumerate(value["group_decisions"]):
        label = f"author review group_decisions[{index}]"
        if not isinstance(item, Mapping):
            raise ReadingPackError(f"invalid {label}")
        _require_keys(item, {"group_id", "decision"}, {"group_id", "decision"}, label)
        group_id = item["group_id"]
        if group_id not in groups or group_id in seen_groups:
            raise ReadingPackError(f"invalid {label}: unknown or duplicate group")
        if not groups[group_id]["bulk_eligible"]:
            raise ReadingPackError(f"invalid {label}: group is not authority-bound")
        if item["decision"] not in {"approve", "hold"}:
            raise ReadingPackError(f"invalid {label}: decision")
        seen_groups.add(group_id)
        group_decisions.append({"group_id": group_id, "decision": item["decision"]})

    units = {record["unit_id"]: record for record in session["records"]}
    record_decisions: list[dict[str, Any]] = []
    seen_units: set[str] = set()
    if not isinstance(value["record_decisions"], list):
        raise ReadingPackError("invalid author review responses: record decisions")
    for index, item in enumerate(value["record_decisions"]):
        label = f"author review record_decisions[{index}]"
        if not isinstance(item, Mapping):
            raise ReadingPackError(f"invalid {label}")
        fields = {"unit_id", "decision", "corrections", "comment"}
        _require_keys(item, fields, fields, label)
        unit_id = item["unit_id"]
        if unit_id not in units or unit_id in seen_units:
            raise ReadingPackError(f"invalid {label}: unknown or duplicate unit")
        decision = item["decision"]
        if decision not in {
            "approve", "revise", "revise_approve", "exclude", "hold"
        }:
            raise ReadingPackError(f"invalid {label}: decision")
        if not isinstance(item["corrections"], Mapping):
            raise ReadingPackError(f"invalid {label}: corrections")
        corrections = {
            field: _validate_correction(units[unit_id], field, correction, f"{label}.{field}")
            for field, correction in item["corrections"].items()
        }
        if corrections and decision not in {"revise", "revise_approve"}:
            raise ReadingPackError(
                f"invalid {label}: corrections require revise or revise_approve"
            )
        if decision in {"revise", "revise_approve"} and not corrections:
            raise ReadingPackError(
                f"invalid {label}: {decision} requires a correction"
            )
        comment = item["comment"]
        if not isinstance(comment, str) or len(comment) > MAX_COMMENT_CHARACTERS:
            raise ReadingPackError(f"invalid {label}: comment")
        seen_units.add(unit_id)
        record_decisions.append(
            {
                "unit_id": unit_id,
                "decision": decision,
                "corrections": corrections,
                "comment": comment,
            }
        )

    questions = {question["question_id"]: question for question in session["questions"]}
    question_answers: list[dict[str, str]] = []
    seen_questions: set[str] = set()
    if not isinstance(value["question_answers"], list):
        raise ReadingPackError("invalid author review responses: question answers")
    for index, item in enumerate(value["question_answers"]):
        label = f"author review question_answers[{index}]"
        if not isinstance(item, Mapping):
            raise ReadingPackError(f"invalid {label}")
        _require_keys(
            item,
            {"question_id", "answer"},
            {"question_id", "answer", "comment"},
            label,
        )
        question_id = item["question_id"]
        if question_id not in questions or question_id in seen_questions:
            raise ReadingPackError(f"invalid {label}: unknown or duplicate question")
        if item["answer"] not in QUESTION_ANSWERS:
            raise ReadingPackError(f"invalid {label}: answer")
        comment = item.get("comment", "")
        if not isinstance(comment, str) or len(comment) > MAX_COMMENT_CHARACTERS:
            raise ReadingPackError(f"invalid {label}: comment")
        seen_questions.add(question_id)
        answer = {"question_id": question_id, "answer": item["answer"]}
        if comment:
            answer["comment"] = comment
        question_answers.append(answer)

    reviewer = value["reviewer"]
    if not isinstance(reviewer, str) or (reviewer and not _SAFE_LINE.fullmatch(reviewer)):
        raise ReadingPackError("invalid author review responses: reviewer")
    reviewed_at = value["reviewed_at"]
    if reviewed_at:
        try:
            date.fromisoformat(reviewed_at)
        except (TypeError, ValueError) as exc:
            raise ReadingPackError("invalid author review responses: reviewed_at") from exc
    elif not isinstance(reviewed_at, str):
        raise ReadingPackError("invalid author review responses: reviewed_at")
    if not isinstance(value["final_signoff"], bool):
        raise ReadingPackError("invalid author review responses: final_signoff")
    checked = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "review_id": value["review_id"],
        "session_sha256": value["session_sha256"],
        "group_decisions": group_decisions,
        "record_decisions": record_decisions,
        "question_answers": question_answers,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "final_signoff": value["final_signoff"],
    }
    return checked


def _parsed_author_review_decisions(
    manifest: Mapping[str, Any],
    session: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    by_unit: dict[str, dict[str, Any]] = {}
    groups = {group["group_id"]: group for group in session["groups"]}
    for item in result["group_decisions"]:
        for unit_id in groups[item["group_id"]]["unit_ids"]:
            by_unit[unit_id] = {
                "decision": item["decision"],
                "corrections": {},
                "comment": "",
            }
    for item in result["record_decisions"]:
        by_unit[item["unit_id"]] = deepcopy(dict(item))
    decisions = []
    for unit in manifest["records"]:
        selected = by_unit.get(
            unit["unit_id"],
            {"decision": "pending", "corrections": {}, "comment": ""},
        )
        decisions.append({**deepcopy(dict(unit)), **selected})

    answers = {item["question_id"]: item["answer"] for item in result["question_answers"]}
    if result["final_signoff"]:
        unresolved = [
            item
            for item in decisions
            if item["decision"] not in {"approve", "revise_approve", "exclude"}
        ]
        if unresolved:
            raise ReadingPackError(
                "final signoff requires approve or exclude for every record"
            )
        required = [
            question["question_id"]
            for question in session["questions"]
            if question["required_for_signoff"]
        ]
        missing = [identifier for identifier in required if answers.get(identifier) != "accept"]
        if missing:
            raise ReadingPackError(
                "final signoff requires acceptance of: " + ", ".join(missing)
            )
    parsed = {
        "decisions": decisions,
        "reviewer": result["reviewer"],
        "reviewed_at": result["reviewed_at"],
        "final_signoff": result["final_signoff"],
        "attestations": deepcopy(result["question_answers"]),
    }
    return parsed
