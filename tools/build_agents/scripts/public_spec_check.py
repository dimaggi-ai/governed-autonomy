"""Offline check of a frozen Apache-2.0 MLCommons system-description SAMPLE.

This checks a real upstream open-source format, not measured hardware performance.
No URL is fetched. Source and license pins are immutable reviewed constants; a
source update requires an explicit code/fixture review, not merely a new hash.
GB and TiB retain their different decimal/binary meanings. Sample device memory
is not usable workload capacity and establishes no SLO or portability claim.
"""
import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from agent_profile_contract import strict_loads

COMMIT = '3fbc329939999c13d0a7b5e67fb2092287e06047'
SOURCE_PATH = 'tools/submission/directory_structure_samples/sample_1/closed/AMD/systems/8xMI325X_2xEPYC_9575F_1.json'
BASE = f'https://raw.githubusercontent.com/mlcommons/inference/{COMMIT}/'
RECORD_HASH = 'afad07b9f8b7e6b5364a0848527e4b2c5415c601b8bea063aeb7bbd5c4179343'
LICENSE_HASH = '0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594'
FIELDS = {'schema', 'source_repository', 'source_commit', 'source_path', 'source_url',
          'retrieved_at', 'evidence_kind', 'not_a_measured_benchmark', 'execution_authority',
          'record_utf8', 'record_sha256', 'license_id', 'license_url', 'license_utf8',
          'license_sha256', 'attribution'}


def memory_bytes(text):
    """Parse this source's explicit units; never infer ambiguous T or bare numbers."""
    match = re.fullmatch(r'([0-9]{1,6}(?:\.[0-9]{1,6})?)\s*(GB|GiB|TB|TiB)', text) if isinstance(text, str) else None
    if not match:
        raise ValueError('explicit supported memory units required')
    # Integer rational arithmetic is independent of callers' Decimal contexts.
    whole, dot, fraction = match[1].partition('.')
    denominator = 10 ** len(fraction)
    numerator = int(whole) * denominator + (int(fraction) if dot else 0)
    multiplier = {'GB': 10**9, 'GiB': 2**30, 'TB': 10**12, 'TiB': 2**40}[match[2]]
    value, remainder = divmod(numerator * multiplier, denominator)
    if value <= 0 or remainder:
        raise ValueError('positive integral byte capacity required')
    return value


def extract(record):
    if not isinstance(record, dict):
        raise ValueError('system description must be an object')
    for field in ('accelerators_per_node', 'number_of_nodes', 'host_processors_per_node'):
        if type(record.get(field)) is not int or not 0 < record[field] < 1000000:
            raise ValueError(f'{field}: positive bounded integer required')
    for field in ('accelerator_model_name', 'accelerator_interconnect', 'framework', 'division', 'submitter'):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f'{field}: source description required')
    return dict(accelerators_per_node=record['accelerators_per_node'],
                number_of_nodes=record['number_of_nodes'],
                accelerator_memory_bytes=memory_bytes(record.get('accelerator_memory_capacity')),
                host_memory_bytes=memory_bytes(record.get('host_memory_capacity')),
                accelerator_interconnect=record['accelerator_interconnect'],
                evidence_kind='upstream_format_sample', measured_performance=False,
                execution_authority=False)


def check(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != FIELDS:
        raise ValueError('snapshot missing/unknown fields')
    expected = dict(schema='dimaggi-public-spec-snapshot/v1',
                    source_repository='https://github.com/mlcommons/inference',
                    source_commit=COMMIT, source_path=SOURCE_PATH, source_url=BASE+SOURCE_PATH,
                    license_id='Apache-2.0', license_url=BASE+'LICENSE.md',
                    evidence_kind='upstream_format_sample')
    if any(snapshot[key] != value for key, value in expected.items()):
        raise ValueError('source/license provenance differs from reviewed pin')
    if snapshot['not_a_measured_benchmark'] is not True or snapshot['execution_authority'] is not False:
        raise ValueError('sample cannot establish measured performance or execution authority')
    if not isinstance(snapshot['attribution'], str) or not snapshot['attribution'].strip():
        raise ValueError('attribution required')
    timestamp = dt.datetime.fromisoformat(snapshot['retrieved_at'])
    if timestamp.tzinfo is None:
        raise ValueError('retrieved_at requires timezone')
    for name, pin in (('record', RECORD_HASH), ('license', LICENSE_HASH)):
        content = snapshot[name+'_utf8']
        if not isinstance(content, str) or snapshot[name+'_sha256'] != pin or hashlib.sha256(content.encode()).hexdigest() != pin:
            raise ValueError(f'{name} digest differs from reviewed source')
    return extract(strict_loads(snapshot['record_utf8']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    try:
        facts = check(strict_loads(args.path.read_text(encoding='utf-8')))
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print(json.dumps({'valid': False, 'error': str(exc), 'execution_authority': False}))
        return 1
    print(json.dumps({'valid': True, 'facts': facts, 'qualification': 'not_established'}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
