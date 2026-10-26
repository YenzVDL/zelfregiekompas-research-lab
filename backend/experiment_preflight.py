"""Explicit small research-path checks, stored outside outputs and metrics."""
import hashlib
import json
from .store import now, uid
from .schema import prompt_inputs
from .methods import prepare, output_errors, PROMPT_VERSION, RULE_VERSION, RETRIEVAL_VERSION, EVALUATOR_VERSION
from . import model_policy
from .provider import ProviderError


def identity(config, metadata, address=None):
    return hashlib.sha256(json.dumps({'config': {k:v for k,v in config.items() if k != 'preflight_id'},
        'models': {name: model['digest'] for name, model in metadata.items()}, 'ollama_address':address,
        'versions': [PROMPT_VERSION, RULE_VERSION, RETRIEVAL_VERSION, EVALUATOR_VERSION]}, sort_keys=True).encode()).hexdigest()


def run(jobs, raw):
    from .jobs import Experiment
    cfg = Experiment.model_validate(raw).model_dump()
    cfg['methods'] = list(dict.fromkeys(cfg['methods']))
    cfg['models'] = list(dict.fromkeys(cfg['models']))
    if not cfg['ai_control']:
        cfg['evaluator_model'] = None
    if not any(method in ['fixed','rag'] for method in cfg['methods']):
        cfg['bank_snapshot_id'] = None
    ds = jobs.s.get('snapshots', cfg['dataset_snapshot_id'])
    scenarios = [s for s in ds['records'] if s['split'] == cfg['split']]
    if ds['kind'] != 'dataset' or not scenarios:
        raise ValueError('Select a dataset snapshot containing the requested split')
    bank, review_mode = [], 'approved'
    if any(method in ['fixed','rag'] for method in cfg['methods']):
        snap = jobs.s.get('snapshots', cfg['bank_snapshot_id'])
        review_mode = snap.get('review_mode', 'approved')
        if snap['kind'] != 'bank' or (review_mode == 'experimental' and cfg['split'] != 'development'):
            raise ValueError('Experimental bank snapshots are development-only')
        bank = snap['records']
    names = (cfg['models'] if any(method != 'fixed' for method in cfg['methods']) else []) + ([cfg['evaluator_model']] if cfg['ai_control'] else [])
    metadata = jobs._pin_models(names)
    address = getattr(jobs.provider,'url',None)
    record = {'id': uid('preflight'), 'created_at': now(), 'config': cfg, 'model_metadata': metadata, 'ollama_address':address, 'identity': identity(cfg, metadata,address), 'checks': [],
              'notice': 'Preflight only; no research outputs or aggregate timings. One representative scenario per method/model, not a quality validation.'}
    for method in cfg['methods']:
        for model in ([None] if method == 'fixed' else cfg['models']):
            check = {'method': method, 'model': model}
            try:
                trace = prepare(method, scenarios[0]['inputs'], bank, cfg['top_k'], review_mode)
                check['trace'] = trace
                if method == 'fixed':
                    question = trace['question']
                else:
                    model_policy.validate_research(metadata[model], cfg['settings'], cfg['mode'])
                    settings = {**cfg['settings'], '_model_digest': metadata[model]['digest'], '_ollama_url': address}
                    response = jobs.provider.generate(model, trace['system_prompt'], trace['prompt'], settings, lambda: False)
                    check['response'] = response
                    question = response['text']
                    status = model_policy.classify(question, response['runtime'], output_errors(question, scenarios[0]['inputs'].get('previous_question')))
                    if status != 'completed':
                        raise ValueError('Research preflight final-answer status: ' + status)
                if cfg['ai_control']:
                    model_policy.validate_research(metadata[cfg['evaluator_model']], cfg['evaluation_settings'], cfg['evaluation_mode'])
                    settings = {**cfg['evaluation_settings'], '_model_digest': metadata[cfg['evaluator_model']]['digest'], '_ollama_url': address}
                    check['evaluation'] = jobs._assess(None, {'id': uid('preflight-output'), 'version':1,'inputs':prompt_inputs(scenarios[0]['inputs']),'question':question}, cfg['evaluator_model'], settings, calibration=True)
                    if check['evaluation']['status'] != 'completed':
                        raise ValueError('Evaluator preflight failed: ' + check['evaluation'].get('error','invalid assessment'))
                check['status'] = 'passed'
            except (ValueError, ProviderError) as exc:
                check.update(status='failed', error=str(exc), diagnostics=getattr(exc,'diagnostics',{}), request=getattr(exc,'request',None))
            record['checks'].append(check)
    record['passed'] = bool(record['checks']) and all(c['status'] == 'passed' for c in record['checks'])
    record['ended_at'] = now()
    return jobs.s.put('preflights', record, insert=True)
