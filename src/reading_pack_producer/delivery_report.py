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
from .delivery_fresh import module_records
from .delivery_seed import AUXILIARY
from .delivery_mechanical import quote_check, name_diagnostics
from .delivery_costs import history as cost_history, totals as cost_totals


def build_report(root, plan, state, data, generated, evaluations, global_result, *, own_ids=None, completeness=None):
    text = (root / 'source.txt').read_text(encoding='utf-8')
    # Mechanical checks cover records this delivery produced; seed-provided records are counted in completeness.
    fresh = plan.get('content_mode') == 'fresh'
    modules = ('chapters',) + AUXILIARY if fresh else ('chapters', 'claims')
    records = [r for name in modules for r in data[name] if own_ids is None or r['id'] in own_ids]
    expected_records = len(plan['units']) + sum(len(u['sections']) for u in plan['units'])
    if fresh:
        expected_records += sum(len(items) for u in plan['units']
                                for items in module_records(u, generated.get(u['id'])).values())
    quotes = []
    for unit in plan['units']:
        if unit['id'] in generated:
            for s in unit['sections']:
                quote = generated[unit['id']]['sections'][s['id']]['evidence_quote']
                quotes.append({'record_id': 'CP-' + s['id'], **quote_check(text, quote, s['start'], s['end'])})
    if fresh:
        for unit in plan['units']:
            if unit['id'] not in generated:
                continue
            sections = {unit['id']: unit, **{s['id']: s for s in unit['sections']}}
            canonical = module_records(unit, generated[unit['id']])
            for name, module in generated[unit['id']]['modules'].items():
                for raw, record in zip(module['items'], canonical[name]):
                    section = sections[raw['section_id']]
                    quotes.append({'record_id': record['id'],
                        **quote_check(text, raw['evidence_quote'], section['start'], section['end'])})
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
            evaluator_quotes.append({'record_id': rid, **quote_check(text, quote, unit['start'], unit['end'])})
    jobs = state['jobs']
    own = {k: j for k, j in jobs.items() if not j.get('carried')}
    known = sum((Decimal(str(j['receipt']['actual_cost_usd'])) for j in own.values()
                 if j.get('receipt') and j['receipt'].get('actual_cost_usd') is not None), Decimal(0))
    started = [j for j in own.values() if j.get('started')]
    unknown = sum(not j.get('receipt') or j['receipt'].get('actual_cost_usd') is None for j in started)
    salvaged = sorted(k for k, j in own.items() if (j.get('receipt') or {}).get('salvaged_additional_properties'))
    rows, base = cost_history(root, plan, state)
    chain = cost_totals(rows, base)
    # recipe.prior_cost_usd is a budget carry-in, possibly including provisional reserves.
    prior_rows = rows[:-1]
    minimum_prior = Decimal(cost_totals(prior_rows, base)['base_plus_chain_and_reserve_usd'])
    extra_prior = max(Decimal(0), Decimal(str(plan['recipe']['prior_cost_usd'])) - minimum_prior)
    chain['additional_prior_budget_usd'] = str(extra_prior)
    chain['booked_cumulative_usd'] = str(Decimal(chain['base_plus_chain_and_reserve_usd']) + extra_prior)
    lang = plan['recipe']['language']
    pack = root / f'reading-pack.{lang}.md'
    return {'contract_version': VERSION, 'quality_gate': False, 'rubric': plan['rubric'],
        'scope': plan['recipe']['scope'], 'title': plan['title'], 'content_mode': plan.get('content_mode', 'legacy'),
        'models': {role: worker['model'] for role, worker in plan['recipe']['workers'].items()},
        'user_adoption': 'not_decided', 'publication': False,
        'generation': {'completed_chapters': len(generated), 'total_chapters': len(plan['units'])},
        'mechanical': {'chapter_structure': {'matched': len(data['chapters']), 'total': len(plan['units'])},
            'section_structure': {'matched': sum(len(c['sections']) for c in data['chapters']),
                                  'total': sum(len(u['sections']) for u in plan['units'])},
            'nonempty_content': {'present': sum(any(bool(c.get(k)) for k in ('summary', 'statement', 'label', 'issue', 'misreading', 'name', 'term')) for c in records), 'expected': expected_records},
            'source_locators': locators, 'generator_evidence_quotes': quotes,
            'name_normalization': name_diagnostics(data, plan, generated),
            'section_pages': {'mapped': len(data.get('section_pages', [])),
                              'total': sum(len(u['sections']) for u in plan['units']), 'kind': 'section_start_only'},
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
            'known_cumulative_usd': chain['base_plus_chain_known_usd'],
            'cost_accounting': chain,
            'normalized_array_wrapper_jobs': {k: j['receipt']['normalized_array_wrappers'] for k,j in jobs.items() if (j.get('receipt') or {}).get('normalized_array_wrappers')},
            'cumulative_cost_limit_usd': plan['recipe']['cumulative_cost_limit_usd'],
            'cost_kind': 'CLI/provider report, not an invoice; unknown costs are not zero',
            'elapsed_seconds': max(0, time.time() - state['started_at']), 'repairs': sum(k.startswith('repair/') and bool(j.get('started')) for k, j in own.items()), 'automatic_retries': 0,
            'carried_jobs': sorted(k for k, j in jobs.items() if j.get('carried')),
            'predecessor': plan.get('predecessor'), 'salvaged_additional_properties_jobs': salvaged,
            'recovered_output_jobs': sorted(k for k, j in jobs.items() if j.get('recovery') or (j.get('receipt') or {}).get('recovery'))},
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
              ]
    # Keep deterministic corrections visible without altering LLM findings.
    pages = m.get('section_pages', {})
    names = m.get('name_normalization', {})
    restored = sum(x.get('restored', False) for x in m['generator_evidence_quotes'])
    lines += [f"- {'原文抜粋の空白差補正' if ja else 'Whitespace-restored generator quotes'}: {restored}",
              f"- {'節開始ページ' if ja else 'Section start pages'}: {pages.get('mapped', 0)}/{pages.get('total', 0)}",
              f"- {'辞書による人名補正' if ja else 'Confirmed name normalizations'}: {len(names.get('applied', []))}",
              f"- {'未確認の類似名候補（自動統合なし）' if ja else 'Unconfirmed name candidates (not merged)'}: {len(names.get('unconfirmed_candidates', []))}",
              ('補正前の引用・表記と補正結果はquality-report.jsonに記録。曖昧な引用や意味の差は自動補正しない。' if ja else
               'Original quotes/names and derived corrections are retained in quality-report.json. Ambiguous or semantic differences are not automatically repaired.'),
              '', '## ' + ('LLMの評点と根拠' if ja else 'LLM scores and evidence'), '']
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
        for name, review in result.get('module_review', {}).items():
            lines += [f"- module {name}: {review['rating']}. {review['reason']}"]
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
    if c and c.get('mode') == 'fresh':
        lines += ['## ' + ('モジュールの生成・空欄理由' if ja else 'Module generation and absence reasons'), '']
        for cid, modules in c['module_decisions'].items():
            for name, decision in modules.items():
                lines += [f"- {cid}/{name}: {decision['items']} items. {decision['omission_reason']}"]
        if c['unprocessed_chapters']:
            lines += ['Unprocessed modules: ' + ', '.join(c['unprocessed_chapters'])]
        lines += ['']
    if e['global'] is not None:
        lines += ['### ' + ('全体・指示' if ja else 'Whole Pack and instructions'), '']
        for name, d in e['global']['dimensions'].items():
            lines += [f"- {name}: **{d['score']}/4**. {d['reason']}"]
        lines += [''] + [f"- {i['target']} ({i['severity']}): {i['reason']}" for i in e['global']['issues']]
        lines += [''] + ['- ' + x for x in e['global']['limitations']] + ['']
    else:
        lines += [('全体・指示のLLM評価: 未評価。' if ja else 'Whole-Pack/instruction LLM evaluation: unevaluated.'), '']
    repair = report.get('repair')
    if repair:
        lines += ['## ' + ('修正1ラウンドと前後比較' if ja else 'One repair round and comparison'), '',
                  f"Complete: {repair['complete']}; changed chapters: {', '.join(repair['changed_chapters']) or '-'}", '']
        if repair.get('reason'): lines += [repair['reason'], '']
        if repair.get('deferred_chapters'):
            lines += [('修正対象外（生成または初回評価が未完了）: ' if ja else 'Deferred (generation or initial evaluation incomplete): ') + ', '.join(repair['deferred_chapters']), '']
        for label, result in [('initial', repair.get('initial_global')), ('final', repair.get('final_global'))]:
            lines += [f"- {label}: " + (', '.join(f"{k}={v['score']}/4" for k,v in result['dimensions'].items()) if result else 'unevaluated')]
        for cid, details in repair['chapters'].items():
            lines += ['', f"### {cid}: {details['status']}", '']
            if details.get('error'): lines += [details['error']]
            for identifier, disposition in details.get('response', {}).get('dispositions', {}).items():
                lines += [f"- {identifier}: {disposition['action']}. {disposition['reason']}"]
        lines += ['', ('初回Packと評価、修正差分と理由を別保存。変更章と変更後Packは再評価し、初回評点を流用しない。採用・公開は未判断。' if ja else
                       'Initial Pack/evaluations and repair changes/reasons are preserved separately. Changed chapters and the changed Pack are reevaluated. Adoption/publication remain undecided.'), '']
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
    if report['resources'].get('normalized_array_wrapper_jobs'):
        lines += [('内容を変えず配列の外包だけを復元した応答: ' if ja else 'Array wrappers normalized without changing element values: ') + ', '.join(report['resources']['normalized_array_wrapper_jobs']), '']
    if report['resources'].get('recovered_output_jobs'):
        lines += [('中断したCLIの保存出力を無変更で回収（ネイティブ正常完了とは区別）: ' if ja else 'Unchanged output recovered from a stopped CLI (not native success): ')
                  + ', '.join(report['resources']['recovered_output_jobs']), '']
    for key, job in report['jobs'].items():
        if job['status'] != 'completed':
            lines += [f"- {key}: {job['status']}. {job.get('error', '')}"]
    costs = report['resources'].get('cost_accounting')
    if costs:
        lines += ['', '## ' + ('全runの費用集計' if ja else 'Cost accounting across runs'), '',
            f"- {'既知報告額（この連続run内）' if ja else 'Known reported cost in this chain'}: USD {costs['chain_known_usd']}",
            f"- {'費用不明件数・暫定予備' if ja else 'Unknown calls and provisional reserve'}: {costs['chain_unknown_cost_calls']} / USD {costs['chain_unknown_reserve_usd']}",
            f"- {'run開始前の申告額' if ja else 'Declared amount before this chain'}: USD {costs['base_prior_declared_usd']}",
            f"- {'追加の持越し予算' if ja else 'Additional budget carry-in'}: USD {costs['additional_prior_budget_usd']}",
            f"- {'予備込み計上額／累計上限' if ja else 'Booked amount including reserve / cumulative cap'}: USD {costs['booked_cumulative_usd']} / {report['resources']['cumulative_cost_limit_usd']}",
            ('失敗分も含むCLI/provider報告額。実請求額ではなく、不明分の予備も確定実費ではない。' if ja else
             'CLI/provider reports include failed calls, not invoices. Unknown-cost reserves are not confirmed charges.')]
    lines += ['', '```json', json.dumps(report['resources'], ensure_ascii=False, indent=2), '```', '',
              ('完全な機械検査、評価原データ、費用根拠は同じフォルダーのquality-report.jsonに保存。'
               if ja else 'Full mechanical checks, evaluation data and cost evidence are in quality-report.json in this folder.'), '']
    lines += ['- ' + x for x in report['limitations']]
    return '\n'.join(lines) + '\n'
