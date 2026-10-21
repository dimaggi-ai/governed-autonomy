"""Offline validation of inference infrastructure evidence fixtures, not infrastructure models.

validate(document, as_of=date) -> list[str] returns every detected contract error.
The CLI reads one JSON file and prints a verdict with execution_authority=false.
Evidence hashes bind UTF-8 payload bytes supplied in the file; URLs are provenance
labels, never fetched or independently authenticated. A valid contract establishes
internal consistency only, not source truth, hardware qualification or approval.

Evidence payloads are JSON objects: facts [{subject, property, value, unit}], or
portability {from, to, qualified, from_config, to_config}. Portability run.config
must be {from: <exact source config>, to: <exact destination config>}. Network
measurement config requires path_id, source_endpoint, destination_endpoint,
protocol, measurement_method and timezone-aware observation_start/end timestamps.
Measured evidence also
requires run {id, config}; config must exactly match the named profile. Profile
comparison requires identical workload/SLO settings and explicit measured
portability evidence when hardware/vendor/runtime differs. Synthetic fixtures
must never be interpreted as measured performance or accepted portability.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

TIERS = {'chip', 'scale_up', 'scale_out', 'scale_in', 'campus', 'metro', 'long_haul'}
KINDS = {'synthetic', 'public_source_reference', 'vendor_rated', 'measured'}
CONFIG_KEYS = {'hardware', 'vendor', 'runtime', 'workload', 'model', 'precision',
               'input_tokens', 'output_tokens', 'batch', 'concurrency', 'accuracy', 'slo'}
SLO_KEYS = {'latency_p99_ms', 'throughput_per_second', 'availability_fraction'}
STATES = {'announced', 'ordered', 'delivered', 'energized', 'qualified'}


def _number(value, minimum=0, maximum=None):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return (math.isfinite(value) and value >= minimum
                and (maximum is None or value <= maximum))
    except (OverflowError, ValueError):
        return False


def _same_value(left, right):
    """JSON equality that never treats booleans as numbers; 1 and 1.0 agree."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_value(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_value(a, b) for a, b in zip(left, right))
    return left == right


def _valid_fact(item):
    return (isinstance(item, dict)
            and all(isinstance(item.get(k), str) and item[k].strip()
                    for k in ('subject', 'property', 'unit'))
            and _number(item.get('value')))


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def strict_loads(text):
    """Reject ambiguous JSON before inspecting an evidence or fixture envelope."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    def constant(value):
        raise ValueError(f'nonfinite JSON number: {value}')
    def floating(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError('nonfinite JSON number')
        return parsed
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)


def validate(document, *, as_of):
    """Return errors; callers must supply the review date for reproducible freshness."""
    errors = []
    if not isinstance(as_of, dt.date):
        raise TypeError('as_of must be a date')
    if not isinstance(document, dict):
        return ['document must be an object']
    def shape(row, required, optional=(), label='record'):
        if not isinstance(row, dict) or not set(required) <= set(row) or not set(row) <= set(required) | set(optional):
            errors.append(f'{label}: missing or unknown fields')

    def finite_tree(value):
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if isinstance(value, dict):
            return all(finite_tree(v) for v in value.values())
        if isinstance(value, list):
            return all(finite_tree(v) for v in value)
        return True

    if not finite_tree(document):
        errors.append('nonfinite number in document')
    shape(document, {'schema', 'execution_authority', 'evidence', 'profiles', 'comparisons', 'network_coverage', 'supply_states'}, label='document')
    if document.get('schema') != 'dimaggi-agent-inference-fixtures/v1':
        errors.append('unsupported schema')
    if document.get('execution_authority') is not False:
        errors.append('execution_authority must be false')

    def rows(key):
        value = document.get(key)
        if not isinstance(value, list) or not value:
            errors.append(f'{key} must be a nonempty list')
            return []
        return value

    evidence, profiles = {}, {}
    for row in rows('evidence'):
        if not isinstance(row, dict):
            errors.append('evidence entry must be an object')
            continue
        shape(row, {'id', 'url', 'kind', 'license', 'retrieved', 'effective', 'expires', 'payload', 'sha256'}, {'run'}, 'evidence')
        eid = row.get('id')
        if not isinstance(eid, str) or not eid or eid in evidence:
            errors.append('invalid or duplicate evidence id')
            continue
        evidence[eid] = row
        url = row.get('url')
        try:
            parsed = urlsplit(url if isinstance(url, str) else '')
            url_ok = (parsed.scheme == 'https' and bool(parsed.hostname)
                      and not parsed.username and not parsed.password and not parsed.fragment)
        except ValueError:
            url_ok = False
        if not url_ok:
            errors.append(f'{eid}: invalid source URL')
        kind = row.get('kind')
        if not isinstance(kind, str) or kind not in KINDS:
            errors.append(f'{eid}: invalid evidence kind')
        if not isinstance(row.get('license'), str) or not row['license'].strip():
            errors.append(f'{eid}: license or reuse restriction required')
        retrieved, effective = _date(row.get('retrieved')), _date(row.get('effective'))
        expires = _date(row.get('expires'))
        if (retrieved is None or effective is None or expires is None
                or retrieved > as_of or effective > as_of or expires < as_of
                or effective > retrieved or expires < retrieved):
            errors.append(f'{eid}: invalid, future or stale evidence dates')
        payload = row.get('payload')
        if not isinstance(payload, str):
            errors.append(f'{eid}: payload must be UTF-8 text')
            continue
        if row.get('sha256') != hashlib.sha256(payload.encode()).hexdigest():
            errors.append(f'{eid}: payload hash mismatch')
        try:
            parsed_payload = strict_loads(payload)
        except (ValueError, TypeError):
            parsed_payload = None
        if not isinstance(parsed_payload, dict):
            errors.append(f'{eid}: payload must encode an object')
        if isinstance(parsed_payload, dict):
            if kind == 'public_source_reference':
                shape(parsed_payload, {'reference_only', 'description'}, label='reference payload')
                if parsed_payload.get('reference_only') is not True:
                    errors.append('reference payload must be reference_only')
            elif 'portability' in parsed_payload:
                shape(parsed_payload, {'portability'}, label='portability payload')
                assertion = parsed_payload['portability']
                shape(assertion, {'from', 'to', 'qualified', 'from_config', 'to_config'}, label='portability assertion')
                if not isinstance(assertion, dict) or type(assertion.get('qualified')) is not bool:
                    errors.append('portability qualified must be a boolean')
            else:
                shape(parsed_payload, {'facts'}, label='facts payload')
                facts = parsed_payload.get('facts')
                if not isinstance(facts, list) or not facts:
                    errors.append('facts payload requires nonempty facts')
                else:
                    for item in facts:
                        shape(item, {'subject', 'property', 'value', 'unit'}, label='source fact')
                        if not _valid_fact(item):
                            errors.append(f'{eid}: invalid source fact subject/property/value/unit')
        if kind == 'measured':
            run = row.get('run')
            shape(run, {'id', 'config'}, label='measured run')
            if not isinstance(run, dict) or not isinstance(run.get('id'), str) or not run.get('id') or not isinstance(run.get('config'), dict):
                errors.append(f'{eid}: measured run identity and exact config required')

    def fact(claim, subject, config=None):
        if not isinstance(claim, dict):
            errors.append(f'{subject}: claim must be an object')
            return
        shape(claim, {'evidence', 'kind', 'property', 'value', 'unit'}, label='claim')
        eid = claim.get('evidence')
        row = evidence.get(eid) if isinstance(eid, str) else None
        if row is None:
            errors.append(f'{subject}: missing provenance')
            return
        kind = claim.get('kind')
        if not isinstance(kind, str) or kind not in KINDS or kind != row.get('kind'):
            errors.append(f'{subject}: claim/evidence kind mismatch')
        if not isinstance(claim.get('property'), str) or not isinstance(claim.get('unit'), str) or not claim.get('unit') or not _number(claim.get('value')):
            errors.append(f'{subject}: invalid fact property/value/unit')
        try:
            payload = strict_loads(row.get('payload', ''))
            facts = payload.get('facts', []) if isinstance(payload, dict) else []
        except (ValueError, TypeError):
            facts = []
        expected = {key: claim.get(key) for key in ('property', 'value', 'unit')}
        expected['subject'] = subject
        if not isinstance(facts, list) or not any(
                _valid_fact(item) and _same_value(expected, item) for item in facts):
            errors.append(f'{subject}: fact not bound to source payload')
        if kind == 'measured':
            run = row.get('run')
            if config is None or not isinstance(run, dict) or not _same_value(run.get('config'), config):
                errors.append(f'{subject}: measured fact lacks exact matching configuration')

    for row in rows('profiles'):
        if not isinstance(row, dict):
            errors.append('profile must be an object')
            continue
        shape(row, {'id', 'config', 'claims'}, label='profile')
        pid = row.get('id')
        if not isinstance(pid, str) or not pid or pid in profiles:
            errors.append('invalid or duplicate profile id')
            continue
        profiles[pid] = row
        config = row.get('config')
        if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
            errors.append(f'{pid}: complete exact workload configuration required')
            config = {}
        for key in ('hardware', 'vendor', 'runtime', 'workload', 'model', 'precision', 'accuracy'):
            if not isinstance(config.get(key), str) or not config.get(key):
                errors.append(f'{pid}: missing {key}')
        for key in ('input_tokens', 'output_tokens', 'batch', 'concurrency'):
            if not isinstance(config.get(key), int) or isinstance(config.get(key), bool) or config.get(key, 0) < 1:
                errors.append(f'{pid}: invalid {key}')
        slo = config.get('slo')
        if (not isinstance(slo, dict) or set(slo) != SLO_KEYS
                or any(not _number(slo.get(k), 0) for k in SLO_KEYS)
                or not _number(slo.get('availability_fraction'), 0, 1)
                or slo.get('latency_p99_ms', 0) <= 0 or slo.get('throughput_per_second', 0) <= 0):
            errors.append(f'{pid}: complete valid SLO inputs required')
        claims = row.get('claims')
        if not isinstance(claims, list) or not claims:
            errors.append(f'{pid}: evidence claims required')
        else:
            for claim in claims:
                fact(claim, pid, config)

    for pair in rows('comparisons'):
        if not isinstance(pair, dict):
            errors.append('comparison must be an object')
            continue
        shape(pair, {'left', 'right'}, {'portability_evidence'}, 'comparison')
        left, right = pair.get('left'), pair.get('right')
        if not isinstance(left, str) or not isinstance(right, str) or left not in profiles or right not in profiles:
            errors.append('comparison references unknown profiles')
            continue
        a, b = profiles[left].get('config'), profiles[right].get('config')
        if not isinstance(a, dict) or not isinstance(b, dict):
            errors.append('comparison missing configurations')
            continue
        portable = {'hardware', 'vendor', 'runtime'}
        if {k: v for k, v in a.items() if k not in portable} != {k: v for k, v in b.items() if k not in portable}:
            errors.append('comparison workload/SLO configurations differ')
        if any(a.get(k) != b.get(k) for k in portable):
            eid = pair.get('portability_evidence')
            proof = evidence.get(eid) if isinstance(eid, str) else None
            try:
                payload = strict_loads(proof.get('payload', '')) if proof else {}
            except (ValueError, TypeError):
                payload = {}
            expected = {'from': left, 'to': right, 'qualified': True, 'from_config': a, 'to_config': b}
            run = proof.get('run') if proof else None
            if (not proof or proof.get('kind') != 'measured' or not isinstance(payload, dict) or not _same_value(payload.get('portability'), expected)
                    or not isinstance(run, dict) or not _same_value(run.get('config'), {'from': a, 'to': b})):
                errors.append('hardware/vendor/runtime portability requires exact measured qualification evidence')

    covered = set()
    for network in rows('network_coverage'):
        if not isinstance(network, dict):
            errors.append('network entry must be an object')
            continue
        shape(network, {'tier'}, {'operation', 'requires_checkpoint', 'claims', 'config'}, 'network')
        tier = network.get('tier')
        if not isinstance(tier, str) or tier not in TIERS or tier in covered:
            errors.append('invalid or duplicate network tier')
            continue
        covered.add(tier)
        if tier == 'scale_in':
            shape(network, {'tier', 'operation', 'requires_checkpoint'}, label='scale_in')
            if network.get('operation') != 'drain_consolidate' or network.get('requires_checkpoint') is not True:
                errors.append('scale_in must declare drain/consolidation with checkpoint constraint')
            continue
        shape(network, {'tier', 'claims'}, {'config'}, 'network tier')
        claims = network.get('claims')
        if not isinstance(claims, list):
            errors.append(f'{tier}: network evidence claims required')
            continue
        by_property = {c.get('property'): c for c in claims if isinstance(c, dict) and isinstance(c.get('property'), str)}
        for prop, unit in {'rtt_ms': 'ms', 'bandwidth_gbps': 'Gbit/s', 'loss_fraction': 'fraction'}.items():
            claim = by_property.get(prop)
            if not claim or claim.get('unit') != unit or not _number(claim.get('value'), 0, 1 if prop == 'loss_fraction' else None):
                errors.append(f'{tier}: missing/invalid network-SLO input {prop}')
        if any(isinstance(c, dict) and c.get('kind') == 'measured' for c in claims):
            config = network.get('config')
            required = {'path_id', 'source_endpoint', 'destination_endpoint', 'protocol',
                        'observation_start', 'observation_end', 'measurement_method'}
            shape(config, required, label='network measurement config')
            if isinstance(config, dict):
                if any(not isinstance(config.get(key), str) or not config.get(key, '').strip()
                       for key in required):
                    errors.append('network measurement config requires nonempty fields')
                try:
                    start = dt.datetime.fromisoformat(config.get('observation_start', ''))
                    end = dt.datetime.fromisoformat(config.get('observation_end', ''))
                    if (start.tzinfo is None or end.tzinfo is None or end <= start
                            or end.date() > as_of):
                        raise ValueError('invalid window')
                except (ValueError, TypeError):
                    errors.append('network measurement observation window invalid')
        for claim in claims:
            fact(claim, tier, network.get('config'))
    if covered != TIERS:
        errors.append('all declared network tiers including scale_in must be covered')

    supply_ids = set()
    for supply in rows('supply_states'):
        if not isinstance(supply, dict) or not isinstance(supply.get('id'), str):
            errors.append('supply entry requires identity')
            continue
        shape(supply, {'id', 'state', 'capacity_basis', 'claim'}, {'config'}, 'supply')
        if not supply['id'] or supply['id'] in supply_ids:
            errors.append('invalid or duplicate supply id')
        supply_ids.add(supply['id'])
        state = supply.get('state')
        if not isinstance(state, str) or state not in STATES:
            errors.append('distinct supply state required')
        # Capacity can only be attributed to qualified service, not announcements,
        # delivery or energized power. This is a label gate, not capacity arithmetic.
        if supply.get('capacity_basis') != ('qualified_service' if state == 'qualified' else 'not_qualified'):
            errors.append('announced/delivered/energized supply is not qualified capacity')
        claim = supply.get('claim')
        if not isinstance(claim, dict) or claim.get('property') != f'{state}_units':
            errors.append('supply state must be bound to evidence fact')
        fact(claim, supply['id'], supply.get('config'))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('--as-of', required=True, type=dt.date.fromisoformat)
    args = parser.parse_args()
    try:
        with open(args.path, encoding='utf-8') as handle:
            document = strict_loads(handle.read())
        errors = validate(document, as_of=args.as_of)
    except (OSError, ValueError, TypeError) as exc:
        errors = [f'invalid input: {exc}']
    print(json.dumps({'valid': not errors, 'errors': errors,
                      'execution_authority': False, 'qualification': 'not_established'}, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
