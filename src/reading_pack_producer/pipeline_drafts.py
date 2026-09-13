"""Declare unapproved draft successors without rewriting human review history."""
from __future__ import annotations

from reading_pack.errors import ReadingPackError
from reading_pack.hashing import semantic_hash
from reading_pack.project import load_language_data, write_json
from reading_pack_review.author_input import load_author_input_state, FIELD_MODULES
from reading_pack_review.author_review import (
    COLLECTION_MODULES, STATE_NAME, _effective_chain, _module_binding,
    load_author_review_state, validate_author_review_state, has_draft_permission,
    review_overrides_for_author_input,
)


def register_draft_revisions(project, language: str, before: dict, *, origin: str) -> int:
    state = load_author_review_state(project)
    author_input = load_author_input_state(project)
    after = load_language_data(project, language)
    revisions = list(state.get('draft_revisions', []))
    changes, data_changed = 0, False
    for collection, module in COLLECTION_MODULES.items():
        old = {record['id']: record for record in before.get(collection, [])}
        binding = _module_binding(author_input, language, module)
        rule = author_input['languages'][language]['modules'][module]
        for record in after.get(collection, []):
            previous = old.get(record['id'])
            if previous is None:
                continue
            expected_input = review_overrides_for_author_input(state, language=language, module=module,
                module_state_sha256=binding, initial_hashes=rule['provided_record_hashes'])
            permission = has_draft_permission(state, language, collection, binding, record['id'], expected_input.get(record['id']))
            # Preserve the author-supplied origin separately from the new candidate's
            # source_locations/evidence. The original module state is never edited.
            if permission:
                for field in ('provenance_source_id', 'provenance_source_hash'):
                    if field in previous and record.get(field) != previous[field]:
                        record[field] = previous[field]
                        data_changed = True
            old_hash, new_hash = semantic_hash(previous), semantic_hash(record)
            if old_hash == new_hash and previous.get('status') == record.get('status'):
                continue
            if (rule['mode'] in {'provided', 'omit'} or record['id'] in rule['provided_record_ids']) and not permission:
                raise ReadingPackError('cannot draft-revise a protected author-input record')
            if collection == 'chapters':
                for name, (field, empty) in FIELD_MODULES.items():
                    field_rule = author_input['languages'][language]['modules'][name]
                    if (field_rule['mode'] in {'provided', 'omit'} or record['id'] in field_rule['provided_record_ids']) and previous.get(field, empty) != record.get(field, empty):
                        raise ReadingPackError('cannot draft-revise a protected author-input field')
            actions = [item for review in state['reviews'] for item in review['actions']
                       if item['language'] == language and item['collection'] == collection and
                       item['module_state_sha256'] == binding and item['record_id'] == record['id']]
            if not actions and not permission:
                continue
            expected, _ = _effective_chain(state['reviews'], list_name='actions', language=language,
                module=module, module_state_sha256=binding, record_id=record['id'],
                initial_sha256=actions[0]['before_sha256'] if actions else expected_input.get(record['id']), draft_revisions=revisions)
            key = {'language': language, 'collection': collection, 'module': module,
                   'record_id': record['id'], 'module_state_sha256': binding,
                   'reviewed_sha256': expected}
            matches = lambda d: all(d[k] == v for k, v in key.items())
            if expected is None or (old_hash != expected and not any(matches(d) and d['draft_sha256'] == old_hash for d in revisions)):
                raise ReadingPackError('previous draft no longer follows the author-reviewed origin')
            if record.get('status') != 'draft':
                record['status'] = 'draft'
                data_changed = True
            if 'translation_status' in record and record['translation_status'] != 'draft':
                record['translation_status'] = 'draft'
                data_changed = True
            revisions = [d for d in revisions if not matches(d)]
            revisions.append({**key, 'draft_sha256': semantic_hash(record), 'origin': origin})
            changes += 1
    if changes:
        state['draft_revisions'] = revisions
        validate_author_review_state(state)
        # Pipeline working directories are rebuilt from frozen inputs after an
        # interruption. Old inputs and human decisions are never rewritten.
        if data_changed:
            write_json(project / 'data' / f'pack.{language}.json', after)
        write_json(project / STATE_NAME, state)
    elif data_changed:
        # An equivalent proposal may only need its original supply provenance
        # restored. Persist that restoration without inventing another revision.
        write_json(project / 'data' / f'pack.{language}.json', after)
    return changes


def authorize_draft_revisions(project, language: str, record_ids: list[str], *, origin: str) -> int:
    """Record an explicit permission for supplied records, without adopting prose.

    The caller must already have permission from the controlling user. Original
    records, module bindings and human review history are preserved for review.
    """
    from reading_pack.validation import validate_project, errors
    from .work_ledger import artifact_hash
    if not record_ids or len(record_ids) != len(set(record_ids)) or not origin:
        raise ReadingPackError('draft permission requires distinct records and an authorization origin')
    if errors(validate_project(project)[2]):
        raise ReadingPackError('cannot authorize an invalid draft project')
    state = load_author_review_state(project)
    author_input = load_author_input_state(project)
    data = load_language_data(project, language)
    permissions = list(state.get('draft_permissions', []))
    originals = []
    for rid in record_ids:
        matches = [(collection, record) for collection in COLLECTION_MODULES for record in data.get(collection, []) if record['id'] == rid]
        if len(matches) != 1 or matches[0][0] == 'chapters':
            raise ReadingPackError('draft permission requires a supplied non-chapter record')
        collection, record = matches[0]; module = COLLECTION_MODULES[collection]
        rule = author_input['languages'][language]['modules'][module]
        binding = _module_binding(author_input, language, module)
        expected = review_overrides_for_author_input(state, language=language, module=module,
            module_state_sha256=binding, initial_hashes=rule['provided_record_hashes']).get(rid)
        if rule['mode'] not in {'provided', 'augment'} or expected is None or semantic_hash(record) != expected:
            raise ReadingPackError('draft permission requires the unchanged supplied or author-reviewed record')
        permission = {'language': language, 'collection': collection, 'module': module, 'record_id': rid,
                      'module_state_sha256': binding, 'reviewed_sha256': expected, 'origin': origin}
        if not has_draft_permission(state, language, collection, binding, rid, expected):
            permissions.append(permission)
        originals.append({'permission': permission, 'record': record})
    updated = {**state, 'draft_permissions': permissions}
    validate_author_review_state(updated)
    receipt = {'authorization_origin': origin, 'originals': originals, 'author_approval': False}
    write_json(project / 'draft-permission-origins' / (artifact_hash(receipt) + '.json'), receipt)
    write_json(project / STATE_NAME, updated)
    return len(record_ids)
