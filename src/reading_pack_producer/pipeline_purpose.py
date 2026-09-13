"""Reader utility, fixed before generation, distinct from exhaustive recall."""

import copy

from .pipeline_contracts import ARTIFACT_CONTRACT_VERSION, recipe_contract

READER_UTILITY_CONTRACT = {
    'version': 'reader-utility-1',
    'purpose': 'Help a reader locate and understand the book without replacing it.',
    'essential_outcomes': [
        'Locate relevant chapters, sections, concepts and supplied supplementary sources.',
        'Explain central claims with their material conditions and epistemic status.',
        'Preserve attribution and distinguish manuscript, author supplements and other source roles.',
        'Avoid unsupported assertions, invented quotations and unsupported claims of absence.',
    ],
    'coverage_rule': 'Reader questions are probes, not authority to add mandatory Pack records or demand exhaustive source recall.',
    'detail_rule': 'Incidental experimental percentages, dates, examples and citation lists need not be copied into the Pack. '
        'They become essential only when their absence changes a central claim, a material qualification or an explicitly declared profile requirement.',
    'routing_rule': 'For an incidental detail beyond the Pack, an honest limitation and a specific relevant location already present in the Pack '
        'can be a successful reader outcome. A generic instruction to read the book, a fabricated locator, or routing instead of explaining '
        'a central claim is insufficient. Do not claim the referenced source was retrieved when it was not.',
    'fidelity_rule': 'Every assertion actually included must remain source-faithful. Omit an optional detail if appropriate, '
        'but never excuse a false number, unsupported comparison, altered condition or incorrect attribution as optional.',
    'evaluation_rule': 'Grade requirements under this fixed reader-utility scope. For an incidental detail, a supported specific route '
        'may satisfy the requirement without reproducing its value. Essential meanings and conditions must be answered correctly. '
        'Explain the reason in the grade rationale and retain exact answer evidence. Do not treat an unexecuted test as a quality failure.',
}

ARTIFACT_READER_UTILITY_CONTRACT = {
    **READER_UTILITY_CONTRACT,
    'version': 'reader-utility-artifact-1',
    'evaluation_rule': 'Determine Pack acceptance from direct checks of its instructions, content, '
        'material qualifications, attribution, references and selected delivery artifacts. '
        'Standard production performs no reader questions, answers, grading or holdout gate. '
        'Optional reader evaluations are diagnostics: retain observed errors and uncertainty without '
        'turning them into Pack defects. Require independent direct-check evidence of a concrete Pack '
        'defect before routing a correction. Correct answers cannot excuse a defective Pack.',
}


def purpose_for_recipe(recipe: dict) -> dict:
    selected = (ARTIFACT_READER_UTILITY_CONTRACT
                if recipe_contract(recipe) == ARTIFACT_CONTRACT_VERSION else READER_UTILITY_CONTRACT)
    return copy.deepcopy(selected)

PURPOSE_STAGES = {'benchmark', 'benchmark_review', 'generate', 'repair', 'audit', 'audit_adjudicate', 'grade', 'grade_batch'}

PURPOSE_PROMPT = (
    ' Apply the frozen reader_utility_contract when deciding scope and acceptance. '
    'For benchmark preparation/review, reject questions that make incidental detail recall essential; '
    'write requirements for reader understanding, material conditions, source navigation and honest boundaries. '
    'For grading, judge a supported specific route to an incidental detail under the contract, not as missing Pack content. '
    'For all stages, retain source-fidelity checks on every assertion actually made. '
    'This contract does not authorize changing questions after answers, ignoring important qualifications or approving unknown results.'
)
