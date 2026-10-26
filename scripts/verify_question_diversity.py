"""Small real HTTP/Ollama check in an isolated DB; never downloads models."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--count', type=int, default=8)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = {'evidence': 'Actual application HTTP endpoints and local Ollama, isolated DB, no mocks', 'model': args.model}
    with tempfile.TemporaryDirectory() as directory, (root/'docs/question-diversity-live.log').open('w', encoding='utf-8') as log:
        env = {**os.environ, 'RESEARCH_DB': str(Path(directory)/'live.db')}
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8001'], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            with httpx.Client(base_url='http://127.0.0.1:8001', trust_env=False, timeout=30) as client:
                def post(path, body=None):
                    response = client.post(path, json=body or {})
                    response.raise_for_status()
                    return response.json()
                for _ in range(100):
                    try:
                        client.get('/api/health').raise_for_status()
                        break
                    except httpx.ConnectError:
                        time.sleep(.2)
                models = client.get('/api/models').json()
                if not any(m['name'] == args.model for m in models.get('models', [])):
                    evidence['unavailable'] = models
                    raise RuntimeError('Requested local model unavailable; no execution simulated')
                bank = post('/api/banks', {'name': 'LIVE diversity verification'})
                for text in ['What concept would you like to revisit or explore next to deepen your understanding?', 'What learning strategy would you try next to improve your understanding of new concepts?']:
                    post('/api/questions', {'bank_id': bank['id'], 'text': text, 'origin': 'Existing reference question for software verification'})
                job = post(f"/api/banks/{bank['id']}/generate", {'model': args.model, 'count': args.count, 'themes': ['understanding', 'learning strategy']})
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    job = client.get(f"/api/jobs/{job['id']}").json()
                    if job['status'] not in ['queued', 'running', 'cancelling']:
                        break
                    time.sleep(.5)
                else:
                    post(f"/api/jobs/{job['id']}/cancel")
                    raise RuntimeError('Live check exceeded deadline; cancellation requested')
                questions = client.get('/api/questions', params={'bank_id': bank['id']}).json()
                evidence.update(job=job, questions=questions)
                assert len(job['results']) == job['saved_count']
                assert job['requested_count'] == job['saved_count'] + job['failed_count'] + job['pending_count']
                assert len({q['text'].strip().casefold() for q in questions}) == len(questions)
                for question in questions:
                    if question['id'] in job['results']:
                        payload = question['generation']['request']['payload']
                        assert payload['system'] == job['question_attempts'][0]['system_prompt']
                        assert 'Existing bank question reference data' in payload['prompt']
                        assert 'ITEM FOCUS:' in payload['prompt']
                        assert payload['options']['temperature'] == .4
                print(json.dumps({key: job.get(key) for key in ['id','status','requested_count','saved_count','failed_count','pending_count','error']}))
        finally:
            process.terminate()
            process.wait(timeout=15)
            (root/'docs/question-diversity-live.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
