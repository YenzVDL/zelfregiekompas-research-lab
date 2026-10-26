"""Version-bound local assessment reusing the research rubric and label format."""
import json
import time
from pydantic import Field
from typing import Literal
from .schema import Strict, prompt_inputs
from .store import now, uid
from .provider import ProviderError, Cancelled
from .methods import EVALUATOR_SYSTEM, EVALUATOR_VERSION, output_errors
from . import model_policy


class AICriterion(Strict):
    value: Literal['Yes', 'No']
    explanation: str = Field(min_length=1, max_length=2000)
    context: str = Field(min_length=1, max_length=3000)


class AIJudgment(Strict):
    usefulness: AICriterion
    correctness: AICriterion


class EvaluationSelection(Strict):
    source_job_id: str
    methods: list[str] = Field(default_factory=list)
    generator_models: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    ai_assessed: Literal['all', 'yes', 'no'] = 'all'
    human_assessed: Literal['all', 'yes', 'no'] = 'all'
    reevaluate: bool = False


CALIBRATION_CASES = [
    ('relevant reflection', 'Which approach would you like to try to check your understanding?', {'usefulness': 'Yes', 'correctness': 'Yes'}),
    ('contradicts facts', 'How did you complete all five tests?', {'correctness': 'No'}),
    ('invented emotions', 'How did you overcome your frustration and anxiety?', {'correctness': 'No'}),
    ('irrelevant', 'Which beach should we visit on holiday?', {'usefulness': 'No'}),
    ('generic safe', 'What would you like to reflect on?', {'correctness': 'Yes'}),
    ('technical limited reflection', 'What exact SQL syntax creates an index?', {'usefulness': 'No'}),
]


class EvaluationJobsMixin:
    def evaluation_preview(self, raw):
        cfg = EvaluationSelection.model_validate(raw).model_dump()
        source = self.s.get('jobs', cfg['source_job_id'])
        if source['kind'] != 'experiment':
            raise ValueError('Select a research experiment to evaluate')
        labels = self.s.all('labels')
        eligible, skipped = [], []
        for output in self.s.all('outputs'):
            if output['job_id'] != source['id']:
                continue
            if cfg['methods'] and output['method'] not in cfg['methods']:
                continue
            if cfg['generator_models'] and (output.get('model') or 'Not applicable') not in cfg['generator_models']:
                continue
            if cfg['categories'] and not set(cfg['categories']) & set(output.get('tags', [])):
                continue
            assessed = {kind: any(l['source'] == kind and l['output_id'] == output['id'] and l['output_version'] == output['version'] for l in labels) for kind in ['ai', 'human']}
            if any(cfg[kind + '_assessed'] != 'all' and assessed[kind] != (cfg[kind + '_assessed'] == 'yes') for kind in assessed):
                continue
            reason = None
            if output['status'] != 'completed' or not output.get('question') or output_errors(output.get('question') or '', output.get('inputs', {}).get('previous_question')):
                reason = 'Execution failure or invalid final question: ' + output['status']
            elif assessed['ai'] and not cfg['reevaluate']:
                reason = 'This exact output version already has an AI assessment; enable re-evaluation explicitly'
            item = {'output_id': output['id'], 'output_version': output['version'], 'generator_model': output.get('model'), 'generator_digest':(output.get('model_metadata') or {}).get('digest')}
            if reason:
                skipped.append({**item, 'reason': reason})
            else:
                eligible.append(item)
        return {'selection': cfg, 'eligible_count': len(eligible), 'eligible': eligible, 'skipped_count': len(skipped), 'skipped': skipped}

    def evaluation_task(self, raw):
        from .jobs import Settings
        allowed = set(EvaluationSelection.model_fields) | {'model', 'settings', 'mode'}
        if set(raw) - allowed:
            raise ValueError('Unknown local evaluation configuration keys')
        preview = self.evaluation_preview({k: v for k, v in raw.items() if k in EvaluationSelection.model_fields})
        if not preview['eligible']:
            raise ValueError('No eligible valid unassessed output versions in this selection')
        settings = Settings.model_validate(raw.get('settings', {'temperature': 0, 'num_predict': 512, 'think': False})).model_dump()
        model = raw.get('model')
        metadata = self._pin_models([model])
        mode = raw.get('mode', 'short')
        if mode not in ['short', 'thinking']:
            raise ValueError('Evaluation mode must be short or thinking')
        model_policy.validate_research(metadata[model], settings, mode)
        config = {**preview['selection'], 'model': model, 'settings': settings, 'mode': mode, 'name': 'Evaluate saved outputs locally'}
        job = self._job('evaluation', config)
        job.update(total=len(preview['eligible']), targets=preview['eligible'], skipped=preview['skipped'], evaluation_items={},
                   model_metadata=metadata, ollama_address=getattr(self.provider, 'url', None),
                   rubric_version=EVALUATOR_VERSION, assessment_ids=[],
                   bias_flags=['Evaluator also generated selected questions (same tag or installed digest); correlated self-evaluation bias is possible'] if any(t['generator_model'] == model or (t['generator_model'] and t['generator_digest'] == metadata[model]['digest']) for t in preview['eligible']) else [])
        self.s.put('jobs', job, insert=True)
        self.schedule(job['id'])
        return job

    def _assess(self, job, output, model, settings, calibration=False):
        start = time.perf_counter()
        identifier = uid('assessment')
        prompt = json.dumps({'scenario_inputs': prompt_inputs(output['inputs']), 'reflection_question': output['question']}, ensure_ascii=False)
        attempt = {'id': identifier, 'evaluation_job_id': job['id'] if job else None, 'job_id': output.get('job_id'),
                   'output_id': output['id'], 'output_version': output['version'], 'source': 'ai', 'model': model,
                   'model_metadata': (job or {}).get('model_metadata', {}).get(model), 'rubric_version': EVALUATOR_VERSION,
                   'system_prompt': EVALUATOR_SYSTEM, 'prompt': prompt, 'response_schema': AIJudgment.model_json_schema(),
                   'settings': {k: v for k, v in settings.items() if not k.startswith('_')}, 'status': 'running', 'created_at': now(), 'calibration': calibration}
        self.s.put('assessment_attempts', attempt, insert=True)
        if job:
            current = self.s.get('jobs', job['id'])
            self.s.patch('jobs', job['id'], assessment_ids=current.get('assessment_ids', []) + [identifier])
        try:
            response = self.provider.generate(model, EVALUATOR_SYSTEM, prompt, settings, lambda: self.cancelled(job['id']) if job else False, response_schema=attempt['response_schema'])
            attempt.update(raw_response=response['text'], request=response.get('request'), runtime=response['runtime'],
                           effective_settings=response.get('effective_settings'), diagnostics=response.get('diagnostics', {}), model_metadata=attempt['model_metadata'] or response.get('model_metadata'))
            self.s.put('assessment_attempts', attempt)
            if response['runtime'].get('done_reason') == 'length':
                raise ValueError('Evaluation output limit reached; incomplete assessment is not accepted')
            def unique_pairs(pairs):
                value = {}
                for key, entry in pairs:
                    if key in value:
                        raise ValueError('Duplicate assessment JSON key: ' + key)
                    value[key] = entry
                return value
            judgment = AIJudgment.model_validate(json.loads(response['text'], object_pairs_hook=unique_pairs)).model_dump()
            if any(not judgment[c][f].strip() for c in judgment for f in ['explanation', 'context']):
                raise ValueError('Each criterion requires an evidence-based explanation and context')
            attempt.update(status='completed', judgment=judgment, validation_errors=[])
            if not calibration:
                label = {'id': uid('label'), 'output_id': output['id'], 'output_version': output['version'], 'job_id': output['job_id'],
                         'evaluation_job_id': job['id'] if job else None, 'assessment_id': identifier, 'source': 'ai', 'reviewer': model, 'role': 'automatic',
                         'judgment': judgment, 'created_at': now(), 'provenance': {**attempt}, 'notice': 'Model judgment, not proof of educational effectiveness'}
                self.s.put('labels', label, insert=True)
                attempt['label_id'] = label['id']
        except (ValueError, ProviderError) as exc:
            attempt.update(status='cancelled' if isinstance(exc, Cancelled) else 'failed', error=str(exc), validation_errors=[str(exc)],
                           diagnostics=getattr(exc, 'diagnostics', {}), partial_text=getattr(exc, 'partial', ''))
            if getattr(exc, 'request', None):
                attempt['request'] = exc.request
            if isinstance(exc, Cancelled):
                raise
        finally:
            attempt.update(ended_at=now(), evaluation_seconds=time.perf_counter() - start)
            self.s.put('assessment_attempts', attempt)
        return attempt

    def _evaluation(self, job):
        cfg = job['config']
        items = dict(job.get('evaluation_items', {}))
        for target in job['targets']:
            key = target['output_id'] + ':' + str(target['output_version'])
            if items.get(key, {}).get('status') in ['completed', 'skipped']:
                continue
            if self.cancelled(job['id']):
                raise Cancelled()
            output = self.s.get('outputs', target['output_id'])
            committed = next((label for label in self.s.all('labels') if label.get('evaluation_job_id') == job['id'] and label['output_id'] == target['output_id'] and label['output_version'] == target['output_version']),None)
            if committed:
                items[key] = {'status':'completed','assessment_id':committed.get('assessment_id'),'recovered_committed_assessment':True}
            elif output['version'] != target['output_version'] or output['status'] != 'completed':
                items[key] = {'status': 'skipped', 'reason': 'Output version/status changed; no fictional assessment created'}
            elif not cfg['reevaluate'] and any(l['source'] == 'ai' and l['output_id'] == output['id'] and l['output_version'] == output['version'] for l in self.s.all('labels')):
                items[key] = {'status': 'skipped', 'reason': 'Exact output version is already AI assessed'}
            else:
                settings = self._pinned_settings(job, cfg['model'], cfg['settings'])
                attempt = self._assess(job, output, cfg['model'], settings)
                items[key] = {'status': attempt['status'], 'assessment_id': attempt['id'], 'error': attempt.get('error')}
            self.s.patch('jobs', job['id'], evaluation_items=items, done=len(items),
                         assessed_count=sum(v['status'] == 'completed' for v in items.values()), failed_count=sum(v['status'] == 'failed' for v in items.values()), skipped_count=sum(v['status'] == 'skipped' for v in items.values()))
        failures = [v for v in items.values() if v['status'] == 'failed']
        self.s.patch('jobs', job['id'], error=(f"Evaluation processing finished with {len(failures)} failed assessment(s); valid saved assessments are retained. " + '; '.join(v.get('error','') for v in failures[:3]))[:1800] if failures else None)
        return 'completed_with_errors' if failures else 'completed'

    def calibrate(self, raw):
        from .jobs import Settings
        model = raw['model']
        metadata = self._pin_models([model])[model]
        settings = Settings.model_validate(raw.get('settings', {'temperature': 0, 'num_predict': 512, 'think': False})).model_dump()
        model_policy.validate_research(metadata, settings, raw.get('mode', 'short'))
        pinned = {**settings, '_model_digest': metadata['digest'], '_ollama_url': getattr(self.provider, 'url', None)}
        checks = []
        for name, question, expectations in CALIBRATION_CASES:
            output = {'id': uid('calibration-output'), 'version': 1, 'question': question,
                      'inputs': {'learning_goal': 'Understand Python unit testing', 'planned_activity': 'Write five unit tests', 'progress': {'planned_units': 5, 'completed_units': 2, 'unit': 'tests'}}}
            assessment = self._assess(None, output, model, pinned, calibration=True)
            differences = [{'criterion':criterion,'development_expectation':expected,'observed':assessment.get('judgment',{}).get(criterion,{}).get('value')} for criterion,expected in expectations.items() if assessment.get('judgment',{}).get(criterion,{}).get('value') != expected]
            checks.append({'case': name, 'development_expectations': expectations, 'assessment': assessment, 'expectation_differences':differences})
        record = {'id': uid('calibration'), 'created_at': now(), 'model_metadata': metadata, 'settings': settings, 'checks': checks,
                  'notice': 'Controlled development checks, not proof of evaluator accuracy. Excluded from research labels and metrics.'}
        return self.s.put('calibrations', record, insert=True)
