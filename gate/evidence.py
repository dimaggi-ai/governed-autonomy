"""Authenticated local-run receipts. HMAC requires an explicitly trusted runner.

This is shared-secret authentication, not public attestation or proof of honest
measurement. Never put production keys in a repository. A verifier holding the
key can also sign; compromise of that trust boundary invalidates the assurance.
"""
import hashlib
import hmac
import json
import re
import math
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def record_digest(record):
    return hashlib.sha256(canonical({k:v for k,v in record.items() if k != 'receipts'})).hexdigest()


def result_predicates(result, payload, subject, limits):
    """Evaluate signed experiment observations, not caller-supplied pass flags.

    Values remain observations from a trusted runner, not proof of hardware
    execution. Every trial must have internally consistent scope and bounds.
    """
    if not isinstance(result,dict) or not isinstance(limits,dict):
        raise ValueError('result and predicate limits must be objects')
    for field, expected in [('schema','infra-experiment-result/v1'),
                            ('implementation_sha256',subject),
                            ('experiment',payload['experiment']),
                            ('execution_kind',payload['execution_kind'])]:
        if result.get(field)!=expected: raise ValueError('inconsistent result '+field)
    def number(row,key,positive=False):
        value=row.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or value<0 or (positive and value<=0):
            raise ValueError('invalid observation '+key)
        return value
    passed=set()
    observations=result.get('observations',{})
    if not isinstance(observations,dict): raise ValueError('observations must be an object')
    for predicate, trials in observations.items():
        if not isinstance(trials,list) or not trials: raise ValueError('empty predicate trials')
        for t in trials:
            if not isinstance(t,dict): raise ValueError('invalid trial')
            if predicate in ('abort_never_missed','rollback'):
                if t.get('triggered') is not True: raise ValueError('unexercised abort/rollback')
                if number(t,'completed_ms')>number(t,'deadline_ms',True): raise ValueError('missed deadline')
                if t.get('safe_state') is not True: raise ValueError('unsafe terminal state')
                if number(t,'deadline_ms',True)>number(limits,predicate+'_deadline_ms',True):
                    raise ValueError('deadline exceeds trusted policy')
            elif predicate=='reversible':
                before=t.get('before_state_sha256','');after=t.get('restored_state_sha256','')
                if not re.fullmatch('[0-9a-f]{64}',before) or before!=after or t.get('action_applied') is not True:
                    raise ValueError('reversibility not demonstrated')
            elif predicate=='quality_canary':
                baseline=number(t,'baseline_goodput',True);actual=number(t,'canary_goodput')
                limit=number(t,'max_relative_regression')
                if type(t.get('samples')) is not int: raise ValueError('sample count must be an integer')
                if limit>1 or actual<baseline*(1-limit) or number(t,'samples',True)<1:
                    raise ValueError('quality canary violates declared bound')
                if limit>number(limits,'max_relative_regression') or number(t,'samples',True)<number(limits,'min_canary_samples',True):
                    raise ValueError('canary weaker than trusted policy')
            elif predicate=='gpu_second_budget':
                if number(t,'wasted_gpu_seconds')>number(t,'budget_gpu_seconds',True):
                    raise ValueError('GPU-second budget exceeded')
                if number(t,'budget_gpu_seconds',True)>number(limits,'budget_gpu_seconds',True):
                    raise ValueError('budget exceeds trusted policy')
            elif predicate=='control_plane_dark':
                if t.get('control_plane_available') is not False or t.get('local_safety_held') is not True:
                    raise ValueError('dark control-plane safety not demonstrated')
                number(t,'duration_ms',True)
                if t['duration_ms']<number(limits,'min_dark_duration_ms',True):
                    raise ValueError('dark interval too short')
            else: raise ValueError('unknown observation predicate')
        passed.add(predicate)
    return passed


def verify_receipts(record, root, trust):
    """Verify issuer, scope, declaration binding, artifact bytes and predicates.

    Trust is supplied out of band by the operator, never accepted from a record.
    Receipt paths and artifacts cannot escape the supplied evidence directory.
    """
    errors, predicates = [], set()
    root = Path(root).resolve()
    paths = record.get('receipts')
    subject = record.get('implementation_sha256', '')
    if not isinstance(subject,str) or not re.fullmatch('[0-9a-f]{64}',subject):
        return ['promotion must bind implementation_sha256 to the implementation tested']
    if not isinstance(paths, list) or not paths:
        return ['authenticated evidence receipts required at L2+']
    for path in paths:
        try:
            target = (root / path).resolve()
            if not target.is_relative_to(root):
                raise ValueError('receipt escapes evidence root')
            receipt = json.loads(target.read_text())
            payload = receipt['payload']
            authority = trust[payload['issuer']]
            key = bytes.fromhex(authority['key_hex'])
            if len(key) < 32:
                raise ValueError('trusted key must contain >= 32 bytes')
            expected = hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, receipt['hmac_sha256']):
                raise ValueError('receipt authentication failed')
            if payload['schema'] != 'infra-evidence-receipt/v1':
                raise ValueError('unsupported receipt schema')
            if payload['record_sha256'] != record_digest(record):
                raise ValueError('receipt is for a different promotion declaration')
            if payload['implementation_sha256'] != subject:
                raise ValueError('receipt is for a different implementation')
            for field in ('action', 'domain', 'region'):
                if record[field] not in authority['allowed_' + field + 's']:
                    raise ValueError('issuer not trusted for this ' + field)
            if payload['execution_kind'] not in authority['execution_kinds']:
                raise ValueError('execution kind not authorized by trust policy')
            if payload['experiment'] not in record['certified_by']:
                raise ValueError('receipt experiment not cited by promotion')
            if not payload['artifacts']:
                raise ValueError('receipt contains no artifacts')
            verified=set()
            for artifact in payload['artifacts']:
                p = (root / artifact['path']).resolve()
                if not p.is_relative_to(root) or not p.is_file():
                    raise ValueError('missing/out-of-root evidence artifact')
                if hashlib.sha256(p.read_bytes()).hexdigest() != artifact['sha256']:
                    raise ValueError('artifact digest mismatch')
                verified |= result_predicates(json.loads(p.read_text()),payload,subject,authority.get('predicate_limits',{}))
            if not isinstance(payload.get('checks'),dict): raise ValueError('checks must be an object')
            declared={k for k,v in payload['checks'].items() if v is True}
            if declared != verified: raise ValueError('checks disagree with experiment observations')
            predicates |= verified
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f'invalid evidence receipt: {exc}')
    required = {'abort_never_missed', 'reversible'}
    if int(record['level'][1]) >= 3:
        required |= {'quality_canary', 'gpu_second_budget'}
    if record['level'] == 'L4':
        required |= {'control_plane_dark', 'rollback'}
    errors += [f'missing authenticated predicate: {p}' for p in sorted(required - predicates)]
    return errors
