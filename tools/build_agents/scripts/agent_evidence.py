"""Pure, offline checks for proposed agent handoff and evaluation evidence.

This module validates supplied records; it does NOT authenticate receipts, run
tools, award acceptance, authorize execution, or change tracker state. A valid
handoff can still describe failed, unknown, local-only or simulated work.

Schemas (all dictionaries reject extra/missing keys):
* handoff/v1: schema, repository (owner/repo), issue (positive tracker number),
  commit (40 lowercase hex), implementer/reviewer ({identity: opaque session/actor ID,
  tool: codex|claude-code|human}), delivery (local_only|pushed|pull_request),
  outcome (succeeded|failed|unknown), evidence_kind, remote_receipt.
  A remote receipt is {repository, commit, ref, observed_at: ISO UTC timestamp,
  url: exact GitHub commit/PR URL, verification: observed}. It is required for
  remote delivery, must match the claim, and is absent for local_only. The caller
  must independently establish its authenticity; a string is not proof.
  validate_handoff requires an explicit timezone-aware as_of datetime; future
  observations are rejected. Actor identity is NOT the accountable GitHub owner:
  two harnesses may share that owner while having distinct session IDs. Agent
  cross-review requires different harnesses and distinct session identities; a
  human reviewer identity is an opaque reference, not an acceptance receipt.
  Accountable ownership and human acceptance remain in the existing tracker.
* economics/v1: schema, evidence_kind, attempts_scope (all|unknown), costs,
  accepted_outcomes, baseline_cost. Costs has inference, infrastructure, tooling,
  human, other: mutually exclusive amounts in one agreed currency and cohort.
  Include every failure/retry, review, supervision, rework and amortization once.
  Null means unknown. baseline_cost is the matched baseline cost for that same
  cohort/service quality; ROI is (baseline_cost-total_cost)/total_cost, a cost-
  saving comparison, not a revenue forecast. Costs, counts and baseline may be
  null. Decimal amounts are nonnegative finite decimal strings or integers.
* coverage/v1: schema, evidence_kind, numerator, denominator (nonnegative counts
  or null). Define the inventoried cohort and metric externally. Missing assets
  cannot be silently excluded from the denominator. This is a ratio, not a
  compliance declaration.
* comparison/v1: schema, evidence_kind, baseline/candidate ({task_digest,
  evaluator_digest, quality, cost_per_accepted}), holdout ({digest, count,
  baseline_quality, candidate_quality} or null), critical_defects (count/null),
  thresholds ({objective: quality|cost, min_quality_gain, max_quality_regression,
  min_cost_reduction}). Digests are SHA-256; qualities/thresholds in [0,1].
  Missing quantities are null; task/evaluator digests must be pinned. Thresholds
  must be fixed independently before running candidates. The result only says
  eligible for independent review, never promoted or approved. Statistical
  adequacy and threshold authorization are outside this structural check.

evidence_kind: observed|simulated|attributed|unknown. Labels are preserved, never
upgraded. Invalid shapes raise ValueError; incomplete valid evidence produces
explicit missing/undefined results. Numbers are emitted as decimal strings.
"""

from datetime import datetime, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
import re
from urllib.parse import urlsplit


KINDS = frozenset({'observed', 'simulated', 'attributed', 'unknown'})
COSTS = frozenset({'inference', 'infrastructure', 'tooling', 'human', 'other'})


def _fields(value, names, label):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError(f'{label}: exact fields required')


def _schema(value, name, names):
    _fields(value, {'schema', 'evidence_kind'} | set(names), name)
    if value['schema'] != name or type(value['evidence_kind']) is not str or value['evidence_kind'] not in KINDS:
        raise ValueError(f'{name}: invalid schema or evidence kind')


def _count(value, label, nullable=True):
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f'{label}: nonnegative integer required')
    return value


def _number(value, label, fraction=False):
    if value is None:
        return None
    if type(value) not in (str, int):
        raise ValueError(f'{label}: decimal string or integer required')
    # Bound parsing and arithmetic; no floats, booleans, exponent bombs or NaN.
    raw = str(value)
    if len(raw) > 80 or not re.fullmatch(r'(0|[1-9][0-9]{0,29})(\.[0-9]{1,18})?', raw):
        raise ValueError(f'{label}: bounded nonnegative decimal required')
    try:
        result = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f'{label}: invalid decimal') from exc
    if not result.is_finite() or (fraction and result > 1):
        raise ValueError(f'{label}: invalid range')
    return result


def _digest(value, label, size=64):
    if type(value) is not str or re.fullmatch('[0-9a-f]{' + str(size) + '}', value) is None:
        raise ValueError(f'{label}: full lowercase digest required')
    return value


def _text(value):
    return format(value, 'f') if value is not None else None


def _ratio(numerator, denominator):
    if numerator is None or denominator is None:
        return {'status': 'missing', 'value': None}
    if denominator == 0:
        return {'status': 'undefined_zero_denominator', 'value': None}
    with localcontext(Context(prec=80)):
        return {'status': 'computed', 'value': _text(Decimal(numerator) / Decimal(denominator))}


def validate_handoff(record, *, as_of):
    """Validate a handoff shape and bindings, not the truth of its assertions."""
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('as_of: timezone-aware datetime required')
    _schema(record, 'handoff/v1', {'repository', 'issue', 'commit', 'implementer',
                                'reviewer', 'delivery', 'outcome', 'remote_receipt'})
    repository = record['repository']
    if type(repository) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', repository):
        raise ValueError('repository: owner/repo required')
    if _count(record['issue'], 'issue', False) == 0:
        raise ValueError('issue: positive tracker issue required')
    _digest(record['commit'], 'commit', 40)
    for role in ('implementer', 'reviewer'):
        person = record[role]
        _fields(person, {'identity', 'tool'}, role)
        if type(person['identity']) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}', person['identity']):
            raise ValueError(f'{role}: bounded opaque session/actor identity required')
        if person['tool'] not in ('codex', 'claude-code', 'human'):
            raise ValueError(f'{role}: unsupported tool')
    if record['implementer']['identity'].casefold() == record['reviewer']['identity'].casefold():
        raise ValueError('independent reviewer identity required')
    if record['implementer']['tool'] == record['reviewer']['tool'] and record['reviewer']['tool'] != 'human':
        raise ValueError('agent cross-review requires the other harness')
    if record['delivery'] not in ('local_only', 'pushed', 'pull_request'):
        raise ValueError('invalid delivery state')
    if record['outcome'] not in ('succeeded', 'failed', 'unknown'):
        raise ValueError('invalid outcome')
    receipt = record['remote_receipt']
    if record['delivery'] == 'local_only':
        if receipt is not None:
            raise ValueError('local_only cannot claim a remote receipt')
    else:
        _fields(receipt, {'repository', 'commit', 'ref', 'observed_at', 'url', 'verification'}, 'remote receipt')
        if receipt['repository'] != repository or receipt['commit'] != record['commit'] or receipt['verification'] != 'observed':
            raise ValueError('remote receipt must observe the same repository and commit')
        if (type(receipt['ref']) is not str
                or not re.fullmatch(r'refs/heads/[A-Za-z0-9][A-Za-z0-9_./-]*', receipt['ref'])
                or '..' in receipt['ref'] or receipt['ref'].endswith('.')
                or any(not part or part.startswith('.') or part.endswith('.lock')
                       for part in receipt['ref'].split('/'))):
            raise ValueError('remote receipt: qualified branch ref required')
        timestamp = receipt['observed_at']
        if type(timestamp) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z', timestamp):
            raise ValueError('remote receipt: UTC observation timestamp required')
        try:
            observed_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(timezone.utc)
        except ValueError as exc:
            raise ValueError('remote receipt: invalid timestamp') from exc
        if observed_at > as_of:
            raise ValueError('remote receipt: future observation')
        url = receipt['url']
        if type(url) is not str:
            raise ValueError('remote receipt: URL required')
        parsed = urlsplit(url)
        expected = '/' + repository + '/commit/' + record['commit']
        path_ok = parsed.path == expected if record['delivery'] == 'pushed' else bool(re.fullmatch('/' + re.escape(repository) + r'/pull/[1-9][0-9]*', parsed.path))
        if parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment or not path_ok:
            raise ValueError('remote receipt: bound GitHub URL required')
    return {'structurally_valid': True, 'evidence_kind': record['evidence_kind'],
            'outcome': record['outcome'], 'delivery': record['delivery'],
            'receipt_authenticated': False, 'acceptance_authorized': False,
            'execution_authorized': False}


def economics(record):
    """Calculate cohort cost and baseline cost-saving ROI, preserving gaps."""
    _schema(record, 'economics/v1', {'attempts_scope', 'costs', 'accepted_outcomes', 'baseline_cost'})
    _fields(record['costs'], COSTS, 'costs')
    if record['attempts_scope'] not in ('all', 'unknown'):
        raise ValueError('all-attempt cohort required; unknown must be explicit')
    amounts = {key: _number(value, key) for key, value in record['costs'].items()}
    accepted = _count(record['accepted_outcomes'], 'accepted_outcomes')
    baseline = _number(record['baseline_cost'], 'baseline_cost')
    missing = sorted(key for key, value in amounts.items() if value is None)
    if record['attempts_scope'] != 'all':
        missing.append('attempts_scope')
    with localcontext(Context(prec=80)):
        total = None if missing else sum(amounts.values(), Decimal(0))
        savings = None if baseline is None or total is None else baseline - total
    return {'evidence_kind': record['evidence_kind'], 'missing_cost_inputs': missing,
            'total_cost': _text(total), 'accepted_outcomes': accepted,
            'cost_per_accepted_outcome': _ratio(total, accepted),
            'baseline_cost': _text(baseline), 'net_cost_savings': _text(savings),
            'cost_saving_roi': _ratio(savings, total)}


def data_coverage(record):
    """Compute an explicitly scoped coverage ratio; zero is not full coverage."""
    _schema(record, 'coverage/v1', {'numerator', 'denominator'})
    numerator = _count(record['numerator'], 'numerator')
    denominator = _count(record['denominator'], 'denominator')
    if numerator is not None and denominator is not None and numerator > denominator:
        raise ValueError('numerator exceeds denominator')
    return {'evidence_kind': record['evidence_kind'], 'numerator': numerator,
            'denominator': denominator, 'coverage': _ratio(numerator, denominator)}


def assess_improvement(record):
    """Screen a comparison for independent review; never authorize promotion."""
    _schema(record, 'comparison/v1', {'baseline', 'candidate', 'holdout', 'critical_defects', 'thresholds'})
    parsed = {}
    for side in ('baseline', 'candidate'):
        value = record[side]
        _fields(value, {'task_digest', 'evaluator_digest', 'quality', 'cost_per_accepted'}, side)
        _digest(value['task_digest'], side + ' task')
        _digest(value['evaluator_digest'], side + ' evaluator')
        parsed[side] = (_number(value['quality'], side + ' quality', True),
                        _number(value['cost_per_accepted'], side + ' cost'))
    threshold = record['thresholds']
    _fields(threshold, {'objective', 'min_quality_gain', 'max_quality_regression', 'min_cost_reduction'}, 'thresholds')
    if threshold['objective'] not in ('quality', 'cost'):
        raise ValueError('invalid improvement objective')
    thresholds = {k: _number(threshold[k], k, True) for k in ('min_quality_gain', 'max_quality_regression', 'min_cost_reduction')}
    if any(v is None for v in thresholds.values()):
        raise ValueError('fixed thresholds required')
    critical = _count(record['critical_defects'], 'critical_defects')
    blockers = []
    if record['evidence_kind'] != 'observed':
        blockers.append('not_observed_evidence')
    if critical is None or critical > 0:
        blockers.append('critical_defects_unknown' if critical is None else 'critical_defects_present')
    for key in ('task_digest', 'evaluator_digest'):
        if record['baseline'][key] != record['candidate'][key]:
            blockers.append(key + '_changed')
    holdout = record['holdout']
    if holdout is None:
        blockers.append('holdout_missing')
    else:
        _fields(holdout, {'digest', 'count', 'baseline_quality', 'candidate_quality'}, 'holdout')
        _digest(holdout['digest'], 'holdout')
        if holdout['digest'] == record['baseline']['task_digest'] or holdout['digest'] == record['candidate']['task_digest']:
            blockers.append('holdout_not_separate')
        count = _count(holdout['count'], 'holdout count')
        hbase = _number(holdout['baseline_quality'], 'holdout baseline', True)
        hcand = _number(holdout['candidate_quality'], 'holdout candidate', True)
        if count is None or count == 0 or hbase is None or hcand is None:
            blockers.append('holdout_incomplete')
        elif hcand < hbase:
            blockers.append('holdout_regressed')
    bquality, bcost = parsed['baseline']
    cquality, ccost = parsed['candidate']
    quality_gain = cost_reduction = None
    with localcontext(Context(prec=80)):
        if None in (bquality, bcost, cquality, ccost):
            blockers.append('metrics_missing')
        else:
            quality_gain = cquality - bquality
            if quality_gain < -thresholds['max_quality_regression']:
                blockers.append('quality_regressed')
            if bcost == 0:
                if threshold['objective'] == 'cost':
                    blockers.append('cost_baseline_zero')
            else:
                cost_reduction = (bcost - ccost) / bcost
            if threshold['objective'] == 'quality':
                if quality_gain <= 0 or quality_gain < thresholds['min_quality_gain']:
                    blockers.append('quality_gain_insufficient')
                if ccost > bcost:
                    blockers.append('cost_increased')
            elif cost_reduction is not None and (cost_reduction <= 0 or cost_reduction < thresholds['min_cost_reduction']):
                blockers.append('cost_reduction_insufficient')
    return {'evidence_kind': record['evidence_kind'], 'review_eligible': not blockers,
            'blockers': blockers, 'quality_gain': _text(quality_gain),
            'cost_reduction_fraction': _text(cost_reduction),
            'promotion_authorized': False, 'execution_authorized': False}
