"""Run named Studio experiments sequentially and retain the exact API records."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default='http://127.0.0.1:18890')
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('output/studio_eval'))
    args = parser.parse_args()
    root = args.output_dir / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    root.mkdir(parents=True, exist_ok=False)
    def call(path, payload=None):
        req = Request(args.server.rstrip('/') + path,
                      data=None if payload is None else json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(error.read().decode()) from error
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    records = []
    for item in cases:
        payload = dict(item)
        name = payload.pop('name')
        record = {'name': name, 'request': payload, 'started': datetime.now(timezone.utc).isoformat()}
        records.append(record)
        try:
            accepted = call('/api/jobs', payload)
            job_id = accepted['id']
            record['id'] = job_id
            print(f'{name}: queued {job_id}', flush=True)
            deadline = time.monotonic() + 900
            stage = None
            while time.monotonic() < deadline:
                job = call('/api/jobs/' + job_id)
                record['job'] = job
                (root / 'results.json').write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding='utf-8')
                if job.get('stage') != stage:
                    stage = job.get('stage')
                    print(f'  {stage} {job.get("progress")}%', flush=True)
                if job['status'] in {'success', 'failed', 'cancelled'}:
                    break
                time.sleep(1)
            else:
                raise TimeoutError('Client timeout: job may still be running; check queue before retrying.')
            print(f'  {job["status"]}: {job.get("elapsed_seconds")}s, {len(job.get("candidates", []))} candidates', flush=True)
            if job['status'] != 'success':
                raise RuntimeError(job.get('error', job['status']))
            for index, candidate in enumerate(job['candidates']):
                with urlopen(args.server.rstrip('/') + candidate['url'], timeout=30) as response:
                    content = response.read()
                target = root / f'{name}_{index + 1}.png'
                target.write_bytes(content)
                candidate['downloaded_to'] = str(target)
        except Exception as error:
            record['error'] = str(error)
            (root / 'results.json').write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding='utf-8')
            raise
        (root / 'results.json').write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Results: {root}', flush=True)


if __name__ == '__main__':
    main()
