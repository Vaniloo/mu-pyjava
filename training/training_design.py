"""GPU-free controls for semantic weighting, protected inputs and early stopping."""
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from mupyjava.decision_points import TOOL_INTENT
from mupyjava.judge_data import read_jsonl
from mupyjava.judge_samples import digest


def example_weights(rows, mode='uniform'):
    if mode == 'uniform':
        return [1.] * len(rows)
    if mode != 'family_request':
        raise ValueError('Unknown weighting mode')
    counts = Counter()
    requests, labels = defaultdict(set), defaultdict(set)
    for row in rows:
        family = row.get('group_id')
        request = row['state'].get('user_request')
        if not isinstance(family, str) or not family or not isinstance(request, str) or not request:
            raise ValueError('Semantic weighting requires family IDs and requests')
        label = str(row['label'])
        counts[(family, label, request)] += 1
        requests[(family, label)].add(request)
        labels[family].add(label)
    # Equal mass per family, then label, then request. Argument variants share
    # their request's mass. Mean-one weights keep the loss/learning-rate scale.
    return [len(rows) / (len(labels) * len(labels[row['group_id']])
            * len(requests[(row['group_id'], str(row['label']))])
            * counts[(row['group_id'], str(row['label']), row['state']['user_request'])]) for row in rows]


def design_weights(rows, design):
    if not rows:
        return []
    strata = defaultdict(list)
    for index, row in enumerate(rows):
        strata[bool(row['tags']['rehearsal'])].append(index)
    if True in strata and False not in strata:
        raise ValueError('New design cannot train on rehearsal alone')
    result = [0.] * len(rows)
    for rehearsal, indices in strata.items():
        mass = (design['rehearsal_fraction'] if rehearsal else 1 - design['rehearsal_fraction']) if len(strata) == 2 else 1.
        selected = [{**rows[index], 'group_id': rows[index]['tags']['semantic_family']} for index in indices]
        for index, weight in zip(indices, example_weights(selected, design['weighting'])):
            result[index] = weight * mass * len(rows) / len(indices)
    return result


class ValidationStop:
    def __init__(self, initial_loss, patience=1, min_delta=.001):
        if (type(initial_loss) not in (int, float) or not math.isfinite(initial_loss)
                or type(patience) is not int or patience < 1
                or type(min_delta) not in (int, float) or not math.isfinite(min_delta) or min_delta < 0):
            raise ValueError('Invalid validation stopping controls')
        self.best_loss, self.best_epoch = initial_loss, 0
        self.patience, self.min_delta, self.bad_epochs = patience, min_delta, 0

    def observe(self, epoch, loss):
        if not math.isfinite(loss):
            raise ValueError('Nonfinite validation loss')
        improved = loss < self.best_loss - self.min_delta
        if improved:
            self.best_loss, self.best_epoch, self.bad_epochs = loss, epoch, 0
        else:
            self.bad_epochs += 1
        return improved, self.bad_epochs >= self.patience


def load_design(path):
    design = json.loads(path.read_text())
    if (set(design) != {'schema_version', 'name', 'weighting', 'patience', 'min_delta', 'rehearsal_fraction', 'protected_inputs'}
            or type(design['schema_version']) is not int or design['schema_version'] != 1 or design['weighting'] not in {'uniform', 'family_request'}
            or not isinstance(design['name'], str) or not design['name']
            or not isinstance(design['protected_inputs'], list) or not design['protected_inputs']):
        raise ValueError('Invalid training design')
    if type(design['rehearsal_fraction']) not in (int, float) or not 0 < design['rehearsal_fraction'] < 1:
        raise ValueError('Rehearsal fraction must be between zero and one')
    ValidationStop(0., design['patience'], design['min_delta'])
    return design


def protected_key(row):
    if 'state' in row:
        return digest({'state': row['state'], 'question': row.get('question', TOOL_INTENT.question)})
    material = row['decision']
    if material['point'] != TOOL_INTENT.id:
        raise ValueError('Protected raw samples must be tool.intent')
    return digest({'state': material['state'], 'question': material['questions'][0]['question']})


def audit_design(rows, design, repository):
    protected, files, prior_train = set(), [], defaultdict(set)
    for item in design['protected_inputs']:
        if set(item) != {'path', 'selection'} or item['selection'] not in {'all', 'nontrain'}:
            raise ValueError('Invalid protected input selection')
        path = (repository / item['path']).resolve()
        path.relative_to(repository.resolve())
        candidates = read_jsonl(path)
        if item['selection'] == 'nontrain':
            for row in candidates:
                if row.get('split') == 'train':
                    prior_train[protected_key(row)].add(str(row['label']))
        selected = [row for row in candidates if item['selection'] == 'all' or row.get('split') != 'train']
        protected.update(protected_key(row) for row in selected)
        files.append({**item, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'selected_rows': len(selected)})
    overlap = [row['sample_id'] for row in rows
               if digest({'state': row['state'], 'question': row['question']}) in protected]
    if overlap:
        raise ValueError(f'New dataset overlaps {len(overlap)} protected previously inspected inputs')
    # family_id is mandatory for new-design rows; all translations, reversals and
    # near-paraphrases must be assigned the same semantic family by the curator.
    families, pairs = {}, defaultdict(list)
    for row in rows:
        tags = row.get('tags', {})
        family, pair = tags.get('semantic_family'), tags.get('contrast_pair')
        if type(tags.get('rehearsal')) is not bool:
            raise ValueError('Every row must explicitly mark rehearsal true or false')
        key = digest({'state': row['state'], 'question': row['question']})
        if tags['rehearsal']:
            if row['split'] != 'train' or prior_train.get(key) != {str(row['label'])}:
                raise ValueError('Rehearsal must preserve an unambiguous prior training input and label, in train only')
        elif key in prior_train:
            raise ValueError('Prior training input cannot be described as fresh')
        if not isinstance(family, str) or not family.strip():
            raise ValueError('New design requires semantic_family on every row, including rehearsal')
        if family in families and families[family] != row['split']:
            raise ValueError('Semantic family crosses partitions')
        families[family] = row['split']
        if pair is not None:
            if not isinstance(pair, str) or not pair.strip():
                raise ValueError('Invalid contrast pair identity')
            pairs[pair].append(row)
    if not pairs:
        raise ValueError('New design requires explicit authorization contrast pairs')
    for pair_rows in pairs.values():
        if (len(pair_rows) != 2 or {row['label'] for row in pair_rows} != {True, False}
                or len({row['split'] for row in pair_rows}) != 1
                or len({row['tags']['semantic_family'] for row in pair_rows}) != 1
                or len({digest({key: row['state'][key] for key in ('tool', 'arguments')}) for row in pair_rows}) != 1
                or len({row['state']['user_request'] for row in pair_rows}) != 2):
            raise ValueError('Contrast pairs need opposite labels, identical actions and one family/partition')
    if {pair_rows[0]['split'] for pair_rows in pairs.values()} != {'train', 'validation', 'calibration', 'test'}:
        raise ValueError('Every new partition must contain authorization contrast pairs')
    inventory = {}
    for split in ('train', 'validation', 'calibration', 'test'):
        part = [row for row in rows if row['split'] == split]
        # Weight semantic families rather than arbitrary collection/group IDs.
        weights = design_weights(part, design)
        inventory[split] = {'rows': len(part), 'families': len({row['tags']['semantic_family'] for row in part}),
                            'distinct_requests': len({row['state']['user_request'] for row in part}),
                            'weight_sum': sum(weights),
                            'entropy_floor': sum(w * math.log(2) for row, w in zip(part, weights) if row['label'] is None) / len(part) if part else None}
    return {'design': design, 'design_digest': digest(design), 'protected_files': files,
            'protected_unique_inputs': len(protected), 'contrast_pairs': len(pairs),
            'inventory': inventory,
            'limitations': 'Exact exclusion and curator-assigned semantic families; semantic independence and human provenance still require review. Not a deployment promotion gate.'}


def main():
    import argparse
    from judge_pipeline import save_new
    from mupyjava.judge_data import validate_manifest
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--design', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-synthetic-eval', action='store_true')
    parser.add_argument('--train-unknown', action='store_true')
    args = parser.parse_args()
    rows = read_jsonl(args.data)
    manifest = json.loads(args.data.with_name('manifest.json').read_text())
    validate_manifest(rows, manifest, args.allow_synthetic_eval, args.train_unknown)
    report = audit_design(rows, load_design(args.design), Path(__file__).resolve().parents[1])
    save_new(args.output, report)
    print(json.dumps(report['inventory']))


if __name__ == '__main__':
    main()
