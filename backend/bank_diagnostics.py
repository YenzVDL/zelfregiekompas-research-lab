"""Conservative deterministic diagnostics, never a promise of uniform use."""
import json
from collections import Counter
from difflib import SequenceMatcher
from .bank_questions import normalize, fallback
from .methods import fixed, matches, RULE_VERSION
from .schema import prompt_inputs


def inspect(bank, scenarios):
    excluded_rejected = [q['id'] for q in bank if q.get('review_status') == 'rejected']
    bank = [q for q in bank if q.get('review_status') != 'rejected']
    exact, near, unreachable, shadowed = [], [], [], []
    for i, question in enumerate(bank):
        c = question['conditions']
        if any(p == m or p.startswith(m + '.') for p in c.get('present', []) for m in c.get('missing', [])):
            unreachable.append({'version_id': question['id'], 'reason': 'Requires a path present inside a required missing parent'})
        for other in bank[i+1:]:
            ratio = SequenceMatcher(None, normalize(question['text']), normalize(other['text'])).ratio()
            if normalize(question['text']) == normalize(other['text']):
                exact.append([question['id'], other['id']])
            elif ratio >= .82:
                near.append({'versions':[question['id'],other['id']], 'lexical_similarity':ratio, 'notice':'Likely wording overlap; semantic review required'})
            if json.dumps(question['conditions'], sort_keys=True) == json.dumps(other['conditions'], sort_keys=True):
                winner = sorted([question,other], key=lambda q:(-q['priority'], q['question_id']))[0]
                loser = other if winner['id'] == question['id'] else question
                shadowed.append({'version_id':loser['id'], 'by_version_id':winner['id'], 'reason':'Identical predicates; priority/stable ID wins whenever these match'})
    selections, errors, counts, eligible_counts = [], [], Counter(), Counter()
    for scenario in scenarios:
        inputs = prompt_inputs(scenario['inputs'])
        for question in bank:
            if matches(question['conditions'], inputs):
                eligible_counts[question['id']] += 1
        try:
            trace = fixed(inputs, bank)
            counts[trace['selected_version_id']] += 1
            selections.append({'scenario_version_id':scenario['id'], 'selected_version_id':trace['selected_version_id'], 'winning_reason':trace['winning_reason'], 'fallback':trace['fallback']})
        except ValueError as exc:
            errors.append({'scenario_version_id':scenario['id'],'error':str(exc)})
    return {'rule_version':RULE_VERSION, 'question_count':len(bank), 'excluded_rejected_versions':excluded_rejected, 'exact_duplicates':exact, 'likely_near_duplicates':near,
            'unconditional_versions':[q['id'] for q in bank if fallback(q)], 'provably_unreachable':unreachable, 'provably_shadowed_pairs':shadowed,
            'selection_frequencies':dict(counts), 'selections':selections, 'selection_errors':errors, 'development_cases':len(scenarios),
            'unused':[{'version_id':q['id'], 'eligible_cases':eligible_counts[q['id']], 'reason':'No applicable selected development cases' if not eligible_counts[q['id']] else 'Applicable but another priority/specificity/stable ID selection won'} for q in bank if not counts[q['id']]],
            'limitations':'No arbitrary semantic contradiction detection or proof of educational suitability. Frequencies depend on this development snapshot; unused questions are not automatically bad.'}
