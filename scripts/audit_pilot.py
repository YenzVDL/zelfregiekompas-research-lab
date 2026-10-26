"""Read saved pilot evidence without modifying historical research records."""
import json
from collections import Counter
from pathlib import Path
from backend.store import Store
from backend.research import fingerprint
from backend.methods import fixed, retrieve


def main():
    store=Store()
    identifier='job_a323517500e9460a'
    job=store.get('jobs',identifier)
    outputs=[o for o in store.all('outputs') if o['job_id']==identifier]
    scenarios=store.get('snapshots',job['dataset_snapshot_id'])['records']
    bank=store.get('snapshots',job['bank_snapshot_id'])['records']
    qwen=[o for o in outputs if (o.get('model') or '').startswith('qwen')]
    evidence={'source':'Actual saved local database records, read-only audit', 'job_id':identifier,
        'outputs':len(outputs),'statuses':dict(Counter(o['status'] for o in outputs)),
        'qwen_attempts':len(qwen),'qwen_length_count':sum(o.get('generation_runtime',{}).get('done_reason')=='length' for o in qwen),
        'qwen_token_counts':dict(Counter(o.get('generation_runtime',{}).get('eval_count') for o in qwen)),
        'model_identities':job.get('model_metadata'), 'settings':job['config']['settings'],
        'scenario_count':len(scenarios),'unique_inputs':len({fingerprint(s['inputs']) for s in scenarios}),
        'time_pairs':dict(Counter(json.dumps([s['inputs'].get('planned_time'),s['inputs'].get('reported_time')],sort_keys=True) for s in scenarios)),
        'old_fixed_selections':dict(Counter(o.get('trace',{}).get('selected_version_id') for o in outputs if o['method']=='fixed')),
        'old_rag_retrievals':dict(Counter(row['version_id'] for o in outputs if o['method']=='rag' for row in o.get('trace',{}).get('retrieved',[]))),
        'bank_condition_structures':len({json.dumps(q['conditions'],sort_keys=True) for q in bank}),
        'review_coverage':dict(Counter(label['source'] for label in store.all('labels') if label.get('job_id')==identifier)),
        'new_logic_counterfactual_only_not_rewritten':{'notice':'Diagnostic calculation against frozen old inputs; not a new research experiment or retroactive correction',
          'fixed_selections':dict(Counter(fixed(s['inputs'],bank)['selected_version_id'] for s in scenarios)),
          'rag_first_three_cases':[{'scenario_version_id':s['id'],'retrieved':retrieve(s['inputs'],bank,3)} for s in scenarios[:3]]}}
    Path('docs/pilot-findings-audit.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps({k:evidence[k] for k in ['source','outputs','statuses','qwen_attempts','qwen_length_count','qwen_token_counts','unique_inputs','bank_condition_structures','review_coverage']},indent=2))


if __name__=='__main__': main()
