"""Separate mechanical counts, ordinal model judgments and execution evidence."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import dataclasses
import json
import re
import time

from reading_pack.validation import validate_project
from .delivery_contract import VERSION


def build_report(root, plan, state, data, generated, evaluations, global_result, *, own_ids=None, completeness=None):
    text = (root / 'source.txt').read_text(encoding='utf-8')
    # Mechanical checks cover records this delivery produced; seed-provided records are counted in completeness.
    records = [r for r in data['chapters'] + data['claims'] if own_ids is None or r['id'] in own_ids]
    expected_records = len(plan['units']) + sum(len(u['sections']) for u in plan['units'])
    quotes = []
    for unit in plan['units']:
        if unit['id'] in generated:
            for s in unit['sections']:
                quote = generated[unit['id']]['sections'][s['id']]['evidence_quote']
                quotes.append({'record_id': 'CP-' + s['id'], 'exact_match': bool(quote) and quote in text[s['start']:s['end']]})
    locators = []
    for record in records:
        for locator in record.get('source_locations') or []:
            m = re.fullmatch(r'source\.txt#normalized-text:(\d+)-(\d+)', locator)
            valid = bool(m and 0 <= int(m[1]) < int(m[2]) <= len(text))
            locators.append({'record_id': record['id'], 'locator': locator, 'resolved': valid})
    _, _, issues = validate_project(root / 'project')
    model_counts = Counter()
    coverage_counts = Counter()
    evaluator_quotes = []
    for unit in plan['units']:
        result = evaluations.get(unit['id'])
        if result is None:
            continue
        model_counts.update(r['verdict'] for r in result['records'].values())
        coverage_counts.update(r['rating'] for r in result['coverage'].values())
        for rid, item in result['records'].items():
            quote = item['source_quote']
            evaluator_quotes.append({'record_id': rid, 'exact_match': bool(quote) and quote in text[unit['start']:unit['end']]})
    jobs = state['jobs']
    own = {k: j for k, j in jobs.items() if not j.get('carried')}
    known = sum((Decimal(str(j['receipt']['actual_cost_usd'])) for j in own.values()
                 if j.get('receipt') and j['receipt'].get('actual_cost_usd') is not None), Decimal(0))
    started = [j for j in own.values() if j.get('started')]
    unknown = sum(not j.get('receipt') or j['receipt'].get('actual_cost_usd') is None for j in started)
    salvaged = sorted(k for k, j in own.items() if (j.get('receipt') or {}).get('salvaged_additional_properties'))
    lang = plan['recipe']['language']
    pack = root / f'reading-pack.{lang}.md'
    return {'contract_version': VERSION, 'quality_gate': False, 'rubric': plan['rubric'],
        'scope': plan['recipe']['scope'], 'title': plan['title'],
        'models': {role: worker['model'] for role, worker in plan['recipe']['workers'].items()},
        'user_adoption': 'not_decided', 'publication': False,
        'generation': {'completed_chapters': len(generated), 'total_chapters': len(plan['units'])},
        'mechanical': {'chapter_structure': {'matched': len(data['chapters']), 'total': len(plan['units'])},
            'section_structure': {'matched': sum(len(c['sections']) for c in data['chapters']),
                                  'total': sum(len(u['sections']) for u in plan['units'])},
            'nonempty_content': {'present': sum(bool(c.get('summary', c.get('statement'))) for c in records), 'expected': expected_records},
            'source_locators': locators, 'generator_evidence_quotes': quotes,
            'project_issues': [dataclasses.asdict(i) for i in issues],
            'delivery_bytes_match': pack.read_bytes() == (root / 'project' / 'dist' / pack.name).read_bytes(),
            'length': {'characters': len(pack.read_text(encoding='utf-8')), 'limit': plan['recipe']['max_pack_characters'],
                       'within_limit': len(pack.read_text(encoding='utf-8')) <= plan['recipe']['max_pack_characters']}},
        'completeness': completeness,
        'evaluation': {'completed_chapters': len(evaluations), 'total_chapters': len(plan['units']),
            'record_counts': dict(model_counts), 'records_evaluated': sum(model_counts.values()), 'records_expected': expected_records,
            'coverage_counts': dict(coverage_counts), 'sections_evaluated': sum(coverage_counts.values()),
            'sections_expected': sum(len(u['sections']) for u in plan['units']),
            'chapters': evaluations, 'global': global_result,
            'evaluator_evidence_quotes': evaluator_quotes},
        'resources': {'model_calls_started': len(started), 'maximum_calls': plan['maximum_calls'],
            'reserved_usd': plan['reserved_usd'], 'known_reported_usd': str(known), 'unknown_cost_calls': unknown,
            'prior_cost_usd': plan['recipe']['prior_cost_usd'],
            'known_cumulative_usd': str(Decimal(str(plan['recipe']['prior_cost_usd'])) + known),
            'cumulative_cost_limit_usd': plan['recipe']['cumulative_cost_limit_usd'],
            'cost_kind': 'CLI/provider report, not an invoice; unknown costs are not zero',
            'elapsed_seconds': max(0, time.time() - state['started_at']), 'repairs': 0, 'automatic_retries': 0,
            'carried_jobs': sorted(k for k, j in jobs.items() if j.get('carried')),
            'predecessor': plan.get('predecessor'), 'salvaged_additional_properties_jobs': salvaged},
        'jobs': jobs,
        'limitations': ['Ordinal LLM scores are not correctness probabilities.',
                       'Source fidelity does not establish scientific truth.',
                       'Global evaluation has the Pack, not the full source.',
                       'Missing/invalid evaluations remain unevaluated.',
                       'Only this declared manuscript is included; no implicit author supplements or seed adoption.']}


def render_report(report, lang):
    ja = lang == 'ja'
    lines = ['# ' + ('品質評価報告' if ja else 'Quality report'), '',
             report['title'], '', report['scope'], '',
             'Models: ' + ', '.join(f'{role}={model}' for role, model in report['models'].items()), '',
             ('生成物と評価を提示する。採否はユーザーが判断する。LLM評点は正確さの確率ではない。'
              if ja else 'The Pack and evaluation are delivered for user judgment. LLM scores are not correctness probabilities.'), '',
             '## ' + ('検査範囲' if ja else 'Coverage'), '']
    m, e = report['mechanical'], report['evaluation']
    lines += ['| ' + ('項目 | 件数 |' if ja else 'Measure | Count |'), '|---|---:|',
              f"| {'章構造' if ja else 'Chapter structure'} | {m['chapter_structure']['matched']}/{m['chapter_structure']['total']} |",
              f"| {'節構造' if ja else 'Section structure'} | {m['section_structure']['matched']}/{m['section_structure']['total']} |",
              f"| {'非空の内容欄' if ja else 'Nonempty content'} | {m['nonempty_content']['present']}/{m['nonempty_content']['expected']} |",
              f"| {'解決可能な所在' if ja else 'Resolved locators'} | {sum(x['resolved'] for x in m['source_locators'])}/{len(m['source_locators'])} |",
              f"| {'LLM照合済み項目' if ja else 'LLM-inspected records'} | {e['records_evaluated']}/{e['records_expected']} |",
              f"| {'LLM評価済み節' if ja else 'LLM-inspected sections'} | {e['sections_evaluated']}/{e['sections_expected']} |", '',
              '## ' + ('LLMの評点と根拠' if ja else 'LLM scores and evidence'), '']
    for chapter_id, result in e['chapters'].items():
        lines += ['### ' + chapter_id, '']
        for name, d in result['dimensions'].items():
            lines += [f"- {name}: **{d['score']}/4**. {d['reason']}"]
        lines += ['']
        for rid, finding in result['records'].items():
            lines += [f"- **{rid}**: {finding['verdict']} ({finding['severity']}). {finding['reason']}",
                      '  ' + ('根拠: ' if ja else 'Evidence: ') + finding['source_quote']]
        lines += ['']
        for sid, finding in result['coverage'].items():
            lines += [f"- {sid}: {finding['rating']}. {finding['reason']}"]
        lines += [''] + ['- ' + note for note in result['limitations']] + ['']
    c = report.get('completeness')
    if c is not None:
        lines += ['## ' + ('収録範囲の完全性' if ja else 'Module completeness'), '']
        if c['seed'] is None:
            lines += [('seedなし。' if ja else 'No seed. ') + c.get('note', ''), '']
        else:
            s = c['seed']
            lines += [(f"seed: {s['path']} (policy={s['policy']}, files={s['files']}, "
                       f"source_matches_delivery={s['source_matches_delivery']}, workflow reset to pending)"), '']
        lines += ['| ' + ('モジュール | seed | 出力 | 欠落 | 追加 |' if ja else 'Module | Seed | Output | Lost | Added |'), '|---|---:|---:|---:|---:|']
        for name, m in c['modules'].items():
            if 'lost_ids' in m:
                lines += [f"| {name} | {m['seed']} | {m['output']} | {len(m['lost_ids'])} | {len(m['added_ids'])} |"]
            else:
                lines += [f"| {name} | {m['seed']} | {m['output']} | preserved={m['preserved']} generated={m['generated']} | empty={m['empty']} |"]
        lines += ['']
        for x in c.get('author_input_conflicts', []):
            lines += [('著者資料の宣言との衝突（著者判断が必要）: ' if ja else 'Author-input declaration conflict (author decision needed): ')
                      + f"{x['module']} seed_mode={x['seed_mode']} added={x['added']}. {x['note']}", '']
        if c['lost_modules']:
            lines += [('**seedの記録が失われたモジュール（退行）**: ' if ja else '**Modules that lost seed records (regression)**: ')
                      + ', '.join(f"{k} {c['modules'][k]['lost_ids']}" for k in c['lost_modules']), '']
        lines += [('章ID対応（seed ID / 原稿見出し / 節数 / 併合した下位見出し）:' if ja else 'Chapter map (seed id / manuscript heading / sections / merged subheadings):')]
        lines += [f"- {u['id']} | {u['manuscript_title']} | {u['sections']} | {u['merged_subheadings']}" for u in c['chapter_map']] + ['']
    if e['global'] is not None:
        lines += ['### ' + ('全体・指示' if ja else 'Whole Pack and instructions'), '']
        for name, d in e['global']['dimensions'].items():
            lines += [f"- {name}: **{d['score']}/4**. {d['reason']}"]
        lines += [''] + [f"- {i['target']} ({i['severity']}): {i['reason']}" for i in e['global']['issues']]
        lines += [''] + ['- ' + x for x in e['global']['limitations']] + ['']
    else:
        lines += [('全体・指示のLLM評価: 未評価。' if ja else 'Whole-Pack/instruction LLM evaluation: unevaluated.'), '']
    lines += ['## ' + ('評点基準' if ja else 'Score rubric'), '']
    lines += [f'- {k}: {v}' for k, v in report['rubric']['scale'].items()]
    lines += ['', '## ' + ('未完了・実行情報' if ja else 'Incomplete work and execution'), '']
    if report['resources'].get('predecessor'):
        pre = report['resources']['predecessor']
        lines += [(f"後継run: 先行run {pre['run']}（{pre['state']}）の完了済みjob {len(report['resources']['carried_jobs'])}件を再送せず引き継ぎ、未完了分だけ実行した。"
                   if ja else f"Successor run: carried {len(report['resources']['carried_jobs'])} completed jobs from {pre['run']} ({pre['state']}) without resending; only incomplete work was executed."), '']
    if report['resources'].get('salvaged_additional_properties_jobs'):
        lines += [('許可外プロパティを剪定して受理した応答: ' if ja else 'Responses accepted after pruning undeclared keys: ')
                  + ', '.join(report['resources']['salvaged_additional_properties_jobs']), '']
    for key, job in report['jobs'].items():
        if job['status'] != 'completed':
            lines += [f"- {key}: {job['status']}. {job.get('error', '')}"]
    lines += ['', '```json', json.dumps(report['resources'], ensure_ascii=False, indent=2), '```', '',
              ('完全な機械検査、評価原データ、費用根拠は同じフォルダーのquality-report.jsonに保存。'
               if ja else 'Full mechanical checks, evaluation data and cost evidence are in quality-report.json in this folder.'), '']
    lines += ['- ' + x for x in report['limitations']]
    return '\n'.join(lines) + '\n'
