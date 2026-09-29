"""Prepare prediction-blind intent packets and audit reviewed real-project intake.

Never fills labels, infers human identity, publishes private traces or exports training data.
Collection provenance is an explicit attestation, not automatically verified fact.
"""
import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from judge_pipeline import save_new
from mupyjava.decision_points import TOOL_INTENT
from mupyjava.judge_data import label_templates, load_labels, load_samples, read_jsonl
from mupyjava.judge_samples import digest

KINDS = {'real_project', 'constructed_harness', 'synthetic', 'unattested'}


def inputs(paths):
    samples, hashes, ids = [], [], set()
    for path in paths:
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        for sample in load_samples(path):
            if sample['sample_id'] in ids:
                raise ValueError('Duplicate sample ID across input files')
            if sample['decision']['point'] != TOOL_INTENT.id:
                raise ValueError('Review intake supports tool.intent only')
            ids.add(sample['sample_id'])
            samples.append(sample)
    if not samples:
        raise ValueError('No intent samples')
    return sorted(samples, key=lambda item: item['sample_id']), sorted(hashes)


def blind_rows(samples):
    # Exact submitted question/state remain. Predictions, policy, source metadata,
    # backend/model names and observed permissions are not sent to the reviewer.
    return [{'schema_version': 1, 'sample_id': sample['sample_id'],
             'input_digest': sample['input_digest'], 'group_id': sample['group_id'],
             'point': sample['decision']['point'], 'version': sample['decision']['version'],
             'questions': copy.deepcopy(sample['decision']['questions']),
             'state': copy.deepcopy(sample['decision']['state'])} for sample in samples]


def group_provenance(samples, groups=None):
    names = {sample['group_id'] for sample in samples}
    if groups is None:
        groups = {name: {'kind': 'unattested', 'description': 'Collection not attested',
                         'attested_by': ''} for name in sorted(names)}
    if set(groups) != names:
        raise ValueError('Collection metadata must cover exactly the input groups')
    for name, metadata in groups.items():
        if (set(metadata) != {'kind', 'description', 'attested_by'} or metadata['kind'] not in KINDS
                or not isinstance(metadata['description'], str) or not metadata['description'].strip()
                or not isinstance(metadata['attested_by'], str)
                or metadata['kind'] == 'real_project' and not metadata['attested_by'].strip()):
            raise ValueError('Invalid collection attestation')
    for sample in samples:
        source = sample.get('source', {})
        origin = str(source.get('origin', '')).lower()
        known_constructed = ('synthetic' in origin or 'controlled' in origin
                             or source.get('probe_task') is not None)
        if groups[sample['group_id']]['kind'] == 'real_project' and known_constructed:
            raise ValueError('Known constructed samples cannot be attested as real project traces')
    return copy.deepcopy(groups)


def prepare(paths, output, groups=None):
    samples, hashes = inputs(paths)
    groups = group_provenance(samples, groups)
    packet = blind_rows(samples)
    manifest = {'schema_version': 1, 'purpose': 'holdout_review_only',
                'source_file_sha256': hashes, 'packet_digest': digest(packet),
                'groups': groups, 'groups_digest': digest(groups), 'samples': len(packet),
                'prediction_blind': True, 'labels_filled': False,
                'limitations': 'Private exact inputs; not automatically redacted. Collection is attested, not verified. No model-generated human labels.'}
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    save_new(output / 'packet.jsonl', packet, jsonl=True)
    save_new(output / 'labels.jsonl', label_templates(samples), jsonl=True)
    save_new(output / 'manifest.json', manifest)
    return manifest


def fingerprint(state):
    return digest({'state': state, 'question': TOOL_INTENT.question})


def audit(paths, packet_dir, labels_path, exclusion_paths=()):
    samples, hashes = inputs(paths)
    manifest = json.loads((packet_dir / 'manifest.json').read_text())
    packet = read_jsonl(packet_dir / 'packet.jsonl')
    if (manifest.get('schema_version') != 1 or manifest.get('purpose') != 'holdout_review_only'
            or hashes != manifest.get('source_file_sha256')
            or packet != blind_rows(samples) or digest(packet) != manifest.get('packet_digest')
            or digest(manifest['groups']) != manifest.get('groups_digest')):
        raise ValueError('Frozen packet, provenance or source inputs changed')
    groups = group_provenance(samples, manifest['groups'])
    labels = load_labels(labels_path, samples)
    blocked, exclusions = set(), []
    for path in exclusion_paths:
        rows = read_jsonl(path)
        blocked.update(fingerprint(row['state']) for row in rows)
        exclusions.append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'rows': len(rows)})
    excluded, eligible, seen, all_reviewed = Counter(), [], {}, {}
    for sample in samples:
        key = fingerprint(sample['decision']['state'])
        label = labels.get(sample['sample_id'])
        if label and label['reviewed']:
            answer = label['answers'][TOOL_INTENT.id]
            if key in all_reviewed and all_reviewed[key] is not answer:
                raise ValueError('Conflicting reviewed labels for identical judge input')
            all_reviewed[key] = answer
        if not label or not label['reviewed']:
            excluded['unreviewed'] += 1
        elif label['origin'] != 'manual':
            excluded['nonmanual_label'] += 1
        elif not label['rationale'].strip():
            excluded['missing_human_rationale'] += 1
        elif groups[sample['group_id']]['kind'] != 'real_project':
            excluded['not_attested_real_project'] += 1
        elif not exclusion_paths:
            excluded['missing_prior_input_exclusions'] += 1
        elif key in blocked:
            excluded['previously_seen_exact_input'] += 1
        elif key in seen:
            excluded['duplicate_judge_input'] += 1
        else:
            seen[key] = sample['sample_id']
            eligible.append({'sample_id': sample['sample_id'], 'group_id': sample['group_id'],
                             'label': label['answers'][TOOL_INTENT.id],
                             'tool': sample['decision']['state']['tool']})
    return {'schema_version': 1, 'samples': len(samples), 'reviewed_real_project_unique': len(eligible),
            'eligible_cases': eligible, 'excluded': dict(sorted(excluded.items())),
            'known_cases': sum(row['label'] is not None for row in eligible),
            'unknown_cases': sum(row['label'] is None for row in eligible),
            'groups': len({row['group_id'] for row in eligible}),
            'by_tool': dict(Counter(row['tool'] for row in eligible)),
            'packet_digest': manifest['packet_digest'], 'exclusions': exclusions,
            'ready_for_retraining': False,
            'limitations': 'Intake audit only; no accuracy or training export. Human identity and collection attestations are not independently verified. Exact exclusion applies only to listed files; semantic overlap and related repository groups require review.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'audit'))
    parser.add_argument('--samples', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--collections', type=Path, help='Group-to-attestation JSON; default unattested')
    parser.add_argument('--packet', type=Path)
    parser.add_argument('--labels', type=Path)
    parser.add_argument('--exclude-data', type=Path, action='append', default=[])
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.packet or args.labels or args.exclude_data:
            parser.error('Packet, labels and exclusions are audit options')
        groups = json.loads(args.collections.read_text()) if args.collections else None
        result = prepare(args.samples, args.output, groups)
    else:
        if args.collections or not args.packet or not args.labels:
            parser.error('Audit requires packet and labels; collection metadata is already frozen')
        result = audit(args.samples, args.packet, args.labels, args.exclude_data)
        save_new(args.output, result)
    print(json.dumps({key: result[key] for key in ('samples', 'reviewed_real_project_unique', 'ready_for_retraining') if key in result}))


if __name__ == '__main__':
    main()
