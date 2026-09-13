"""Bounded audit recovery without discarding findings or changing acceptance."""
from __future__ import annotations
import copy
import re

from reading_pack.errors import ReadingPackError
from .pipeline_generation import compact_canonical, author_contract, protection_reason
from .work_ledger import artifact_hash

SELECTION_CONTRACT = {
    'purpose': 'A compact reading aid: locate material, explain central claims and their conditions, distinguish source roles, and avoid unsupported answers.',
    'rules': [
        'Select only information needed for that purpose and the declared profile; a detail is not mandatory merely because it occurs in this source chunk.',
        'Do not test exhaustive citation lists, incidental names, isolated dates, or paragraph recall unless indispensable to a central claim.',
        'For notes, references or supplements, prefer source navigation, qualification and attribution over reproducing their contents.',
        'Reader questions do not create mandatory Pack coverage obligations. Reject otherwise entailed questions that require incidental detail recall rather than reader understanding or specific source navigation.',
        'Make this selection before any reader answers. Never relax or replace an already frozen suite after observing results.',
    ],
}


def consolidate_findings(findings):
    """Group repair targets, preserving every observation and its exact evidence."""
    groups = {}
    for finding in findings:
        f = copy.deepcopy(finding)
        ids = sorted(set(f.get('record_ids', [])))
        requirements = sorted(set(f.get('requirement_ids', [])))
        identity = {'record_ids': ids, 'requirement_ids': requirements,
                    'criterion': f.get('criterion', f['category']),
                    'classification': f.get('classification', 'blocking'),
                    'repair_scope': f.get('repair_scope', 'pack')}
        # Unlocated defects cannot be equated by category alone.
        if not ids and not requirements:
            identity['reason'] = f['reason']
        key = artifact_hash(identity)
        if key not in groups:
            groups[key] = f
            continue
        merged = groups[key]
        if 'observations' not in merged:
            merged['observations'] = [copy.deepcopy(merged)]
        merged['observations'].append(f)
        merged['reason'] = '\n'.join(dict.fromkeys(o['reason'] for o in merged['observations']))
        merged['evidence'] = list({artifact_hash(e): e for o in merged['observations'] for e in o.get('evidence', [])}.values())
        merged.pop('source_scope', None)  # Evidence now spans all original observations.
    return list(groups.values())


def actionable_failures(project, language, canonical, findings):
    contract = author_contract(project, language)
    records = {r['id']: (c, r) for c, rows in compact_canonical(canonical).items() for r in rows}
    result = []
    for f in findings:
        if f.get('classification') != 'unresolved':
            result.append(f)
            continue
        # Uncertainty permits only a positively sourced clarification of an
        # editable existing record, never guessing the disputed fact.
        if f.get('evidence') and any(rid in records and not protection_reason(
                {'collection': records[rid][0], 'record': records[rid][1]}, canonical, contract)
                for rid in f.get('record_ids', [])):
            result.append(f)
    return result


def recheck_known_findings(runner, project, canonical, failures, known, number, report):
    """A fresh audit's silence must not erase a previously confirmed defect."""
    from .pipeline_audit import adjudicate_audit
    def identity(f):
        requirements = sorted(f.get('requirement_ids', []))
        return artifact_hash({'criterion':f.get('criterion',f.get('category')),
            'requirements':requirements,
            'records':[] if requirements else sorted(f.get('record_ids', []))})
    covered = {identity(f) for f in failures + report['advisories'] + report['dismissed']}
    for finding in consolidate_findings(known):
        if finding.get('classification') not in {'blocking','unresolved'} or not finding.get('evidence'):
            continue
        key = identity(finding)
        if key in covered:
            continue
        checked = adjudicate_audit(runner, project, canonical,
            {'findings':[{k:v for k,v in finding.items() if k not in {'observations','finding_id'}}],
             'source_attribution_errors':[], 'invented_record_ids':[]},
            {'id':'known-'+key[:24]}, number)
        failures.extend(checked['blocking'])
        report['advisories'].extend(checked['advisories']);report['dismissed'].extend(checked['dismissed'])
        report['decisions'].extend(checked['decisions'])
        report.setdefault('known_findings_rechecked',[]).append({'finding':finding,'decisions':checked['decisions']})
        covered.add(key)
    return failures


def lookup_context(runner, canonical, finding, requests):
    from .pipeline_audit import evidence_context, _slice
    maximum = runner.recipe.get('audit_context_characters', 32000)
    base = evidence_context(runner, canonical, [finding], maximum=maximum)
    sources = {s['id']: s for s in runner.manifest['sources']}
    lengths = {sid: max(c['end'] for c in runner.chunks if c['source_id'] == sid) for sid in sources}
    samples = list(base['samples']); used = sum(len(s['text']) for s in samples)
    queries = []; omitted = []
    for request in requests:
        sid, query = request['source_id'], request['query']
        if sid not in sources or not isinstance(query, str) or not 1 <= len(query) <= 160:
            raise ReadingPackError('audit lookup requested an invalid frozen source or query')
        text = _slice(runner.chunks, sid, 0, lengths[sid])['text']
        matches = list(re.finditer(re.escape(query), text))
        queries.append({'source_id': sid, 'source_sha256': sources[sid]['sha256'],
                        'query': query, 'match_count': len(matches), 'searched_range': [0, lengths[sid]]})
        for match in matches:
            intervals = [(max(0, match.start()-600), min(lengths[sid], match.end()+1200))]
            for s in samples:
                if s['source_id'] != sid: continue
                intervals = [part for a,b in intervals for part in ((a,min(b,s['start'])),(max(a,s['end']),b)) if part[0]<part[1]]
            need = sum(b-a for a,b in intervals)
            if used+need > maximum:
                omitted.append(f'context limit: {sid}:{match.start()}'); continue
            for a,b in intervals:
                samples.append(_slice(runner.chunks,sid,a,b));used+=b-a
    retrieval = {**base['retrieval'], 'characters': used,
                 'complete': base['retrieval']['complete'] and not omitted,
                 'unresolved': base['retrieval']['unresolved'] + omitted,
                 'followup_searches': queries}
    return {'samples': samples, 'retrieval': retrieval,
            'resolution_guidance': 'This is one bounded follow-up to an unresolved suspicion. Exact query matches are not semantic completeness. A zero count does not prove absence. Keep all existing criteria. If verified positive evidence supports a narrower attribution or qualification, identify that precise repair rather than demanding proof of source silence. Do not fabricate certainty.'}


def resolve_unsettled(runner, project, canonical, findings, number, report):
    """One requested lookup and rejudgment per unresolved repair group per round."""
    from .pipeline_audit import adjudicate_audit
    sources = [{k:s[k] for k in ('id','name','role','sha256')} for s in runner.manifest['sources']]
    records = {r['id']: r for rows in compact_canonical(canonical).values() for r in rows}
    output = []
    for f in consolidate_findings(findings):
        if f.get('classification') != 'unresolved':
            output.append(f); continue
        identity = artifact_hash(f)[:24]
        plan = runner.call('audit_lookup', {'finding': f, 'sources': sources,
            'records': [records[rid] for rid in f.get('record_ids', []) if rid in records]},
            f'{number}/audit-lookup/{identity}')
        if not plan['requests']:
            output.append(f)
            report.setdefault('resolution_attempts', []).append({'finding':f,'lookup':plan,'rejudged':False})
            continue
        context = lookup_context(runner, canonical, f, plan['requests'])
        chunk = {'id':'resolution-'+identity}
        # Keep a single explicit suspicion; aggregate messages were already
        # expanded by the first audit and must not be added for a second time.
        request_finding = {k:v for k,v in f.items() if k not in {'observations','finding_id'}}
        audit = {'findings':[request_finding], 'source_attribution_errors':[], 'invented_record_ids':[]}
        result = adjudicate_audit(runner, project, canonical, audit, chunk, number, resolution_context=context)
        output.extend(result['blocking'])
        report['advisories'].extend(result['advisories']); report['dismissed'].extend(result['dismissed'])
        report.setdefault('resolution_attempts', []).append({'finding':f,'lookup':plan,
                                                           'rejudged':True,'decisions':result['decisions']})
    return consolidate_findings(output)
