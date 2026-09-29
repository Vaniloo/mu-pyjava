"""Validate identical frozen inputs before comparing actual serving reports."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from evaluate_intent import metrics, coverage_inventory
from judge_pipeline import save_new
from mupyjava.judge import _laya_judgment
from mupyjava.judge_data import read_jsonl


def compare(data, split, reports):
    rows = [row for row in read_jsonl(data) if split == 'all' or row.get('split') == split]
    if not rows:
        raise ValueError('No comparison inputs')
    file_hash = hashlib.sha256(data.read_bytes()).hexdigest()
    models = {}
    for name, report in reports.items():
        predictions = report['predictions']
        if report['dataset_file_sha256'] != file_hash or len(predictions) != len(rows):
            raise ValueError('Report does not match frozen file/partition')
        for row, prediction in zip(rows, predictions):
            if (row['state'] != prediction['state'] or row['label'] is not prediction['label']
                    or row.get('sample_id') != prediction.get('sample_id')):
                raise ValueError('Report inputs, labels or ordering changed')
            if _laya_judgment(prediction['probability']).answer is not prediction['answer']:
                raise ValueError('Report answer differs from application thresholds')
        overall = metrics(rows, [p['probability'] for p in predictions])
        if overall != report['overall']:
            raise ValueError('Report aggregate disagrees with predictions')
        models[name] = {'checkpoint_sha256': report['checkpoint_sha256'], 'metrics': overall,
                       'correct_decisive': sum(p['label'] is not None and p['answer'] is p['label'] for p in predictions),
                       'known_abstention': sum(p['label'] is not None and p['answer'] is None for p in predictions),
                       'unknown_abstention': sum(p['label'] is None and p['answer'] is None for p in predictions),
                       'temperature_control': report.get('temperature_control')}
    return {'dataset_sha256': file_hash, 'split': split, 'thresholds': {'decline': .2, 'allow': .8},
            'coverage_inventory': coverage_inventory(rows), 'models': models}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--split', default='test')
    parser.add_argument('--report', action='append', required=True, help='NAME=report.json[.gz]')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reports = {}
    for specification in args.report:
        name, filename = specification.split('=', 1)
        if name in reports:
            raise ValueError('Duplicate model name')
        with (gzip.open if filename.endswith('.gz') else open)(filename, 'rt', encoding='utf-8') as file:
            reports[name] = json.load(file)
    report = compare(args.data, args.split, reports)
    save_new(args.output, report)
    print(json.dumps(report['models']))


if __name__ == '__main__':
    main()
