"""Behavioral evidence checks, including fabricated-delivery and metric attacks."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext
import unittest

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.agent_evidence import (
    validate_handoff, economics, data_coverage, assess_improvement,
)


AS_OF = datetime(2026, 10, 21, tzinfo=timezone.utc)

def handoff():
    return {'schema': 'handoff/v1', 'repository': 'example/project', 'issue': 12,
            'commit': 'a' * 40, 'implementer': {'identity': 'builder', 'tool': 'codex'},
            'reviewer': {'identity': 'reviewer', 'tool': 'claude-code'},
            'delivery': 'local_only', 'outcome': 'unknown', 'evidence_kind': 'unknown',
            'remote_receipt': None}


def receipt():
    return {'repository': 'example/project', 'commit': 'a' * 40,
            'ref': 'refs/heads/feature', 'observed_at': '2026-10-20T12:00:00Z',
            'url': 'https://github.com/example/project/commit/' + 'a' * 40,
            'verification': 'observed'}


def costing():
    return {'schema': 'economics/v1', 'evidence_kind': 'observed', 'attempts_scope': 'all',
            'costs': {'inference': '10.10', 'infrastructure': '20.20', 'tooling': 0,
                      'human': '60.60', 'other': '9.10'},
            'accepted_outcomes': 4, 'baseline_cost': 160}


def comparison():
    baseline = {'task_digest': 'a' * 64, 'evaluator_digest': 'b' * 64,
                'quality': '0.8', 'cost_per_accepted': 100}
    candidate = dict(baseline, quality='0.9', cost_per_accepted=75)
    return {'schema': 'comparison/v1', 'evidence_kind': 'observed',
            'baseline': baseline, 'candidate': candidate,
            'holdout': {'digest': 'c' * 64, 'count': 20,
                        'baseline_quality': '0.8', 'candidate_quality': '0.9'},
            'critical_defects': 0,
            'thresholds': {'objective': 'quality', 'min_quality_gain': '0.1',
                           'max_quality_regression': 0, 'min_cost_reduction': '0.2'}}


class HandoffTests(unittest.TestCase):
    def test_local_unknown_is_not_upgraded_to_success_or_authority(self):
        result = validate_handoff(handoff(), as_of=AS_OF)
        self.assertTrue(result['structurally_valid'])
        self.assertEqual((result['outcome'], result['evidence_kind']), ('unknown', 'unknown'))
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['acceptance_authorized'])

    def test_distinct_sessions_same_account_do_not_need_fabricated_logins(self):
        record = handoff()
        record['implementer']['identity'] = 'codex:session-a'
        record['reviewer']['identity'] = 'claude:session-b'
        self.assertTrue(validate_handoff(record, as_of=AS_OF)['structurally_valid'])
        record['reviewer']['tool'] = 'codex'
        with self.assertRaises(ValueError):
            validate_handoff(record, as_of=AS_OF)
        record['reviewer'] = {'identity': 'person:reviewer-a', 'tool': 'human'}
        self.assertFalse(validate_handoff(record, as_of=AS_OF)['acceptance_authorized'])

    def test_remote_time_is_bound_to_explicit_aware_review_time(self):
        record = handoff()
        record.update(delivery='pushed', remote_receipt=receipt())
        observed = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        self.assertTrue(validate_handoff(record, as_of=observed)['structurally_valid'])
        equivalent = observed.astimezone(timezone(timedelta(hours=-7)))
        self.assertTrue(validate_handoff(record, as_of=equivalent)['structurally_valid'])
        for invalid in (observed - timedelta(microseconds=1), observed.replace(tzinfo=None), None):
            with self.subTest(as_of=invalid), self.assertRaises(ValueError):
                validate_handoff(record, as_of=invalid)
        with self.assertRaises(TypeError):
            validate_handoff(record)

    def test_cannot_claim_remote_delivery_without_bound_receipt(self):
        for delivery in ('pushed', 'pull_request'):
            record = handoff()
            record['delivery'] = delivery
            with self.subTest(delivery=delivery), self.assertRaises(ValueError):
                validate_handoff(record, as_of=AS_OF)

    def test_remote_receipt_does_not_authenticate_itself(self):
        record = handoff()
        record.update(delivery='pushed', remote_receipt=receipt(), outcome='succeeded')
        result = validate_handoff(record, as_of=AS_OF)
        self.assertFalse(result['receipt_authenticated'])
        self.assertFalse(result['acceptance_authorized'])

    def test_receipt_must_bind_repository_commit_and_delivery_url(self):
        changes = {'repository': 'example/other', 'commit': 'b' * 40,
                   'url': 'https://github.com.evil.test/example/project/commit/' + 'a' * 40,
                   'verification': 'asserted', 'ref': 'main',
                   'observed_at': '2026-02-30T12:00:00Z'}
        for key, value in changes.items():
            record = handoff()
            record.update(delivery='pushed', remote_receipt=receipt())
            record['remote_receipt'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_handoff(record, as_of=AS_OF)

    def test_invalid_branch_components_are_rejected(self):
        for ref in ('refs/heads/feature//x', 'refs/heads/feature/.hidden',
                    'refs/heads/feature.lock/x', 'refs/heads/feature/',
                    'refs/heads/feature..name', 'refs/heads/feature.'):
            record = handoff()
            record.update(delivery='pushed', remote_receipt=receipt())
            record['remote_receipt']['ref'] = ref
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                validate_handoff(record, as_of=AS_OF)

    def test_pr_receipt_accepts_only_pr_url(self):
        record = handoff()
        record.update(delivery='pull_request', remote_receipt=receipt())
        with self.assertRaises(ValueError):
            validate_handoff(record, as_of=AS_OF)
        record['remote_receipt']['url'] = 'https://github.com/example/project/pull/5'
        self.assertTrue(validate_handoff(record, as_of=AS_OF)['structurally_valid'])

    def test_self_review_case_change_and_short_sha_are_rejected(self):
        record = handoff()
        record['reviewer']['identity'] = 'BUILDER'
        with self.assertRaises(ValueError):
            validate_handoff(record, as_of=AS_OF)
        record = handoff()
        record['commit'] = 'a' * 7
        with self.assertRaises(ValueError):
            validate_handoff(record, as_of=AS_OF)

    def test_no_extra_authority_fields_or_boolean_issue(self):
        for key, value in [('execution_authorized', True), ('issue', True)]:
            record = handoff()
            record[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_handoff(record, as_of=AS_OF)


class EconomicsTests(unittest.TestCase):
    def test_fully_loaded_cost_includes_human_and_other_costs(self):
        result = economics(costing())
        self.assertEqual(Decimal(result['total_cost']), Decimal('100'))
        self.assertEqual(Decimal(result['cost_per_accepted_outcome']['value']), Decimal('25'))
        self.assertEqual(Decimal(result['cost_saving_roi']['value']), Decimal('0.6'))

    def test_failure_costs_increase_cost_of_each_accepted_outcome(self):
        record = costing()
        record['costs']['inference'] = '30.10'
        self.assertEqual(Decimal(economics(record)['cost_per_accepted_outcome']['value']), Decimal('30'))

    def test_missing_cost_or_attempt_scope_never_becomes_free(self):
        for change in ('cost', 'scope'):
            record = costing()
            if change == 'cost':
                record['costs']['human'] = None
            else:
                record['attempts_scope'] = 'unknown'
            result = economics(record)
            self.assertIsNone(result['total_cost'])
            self.assertEqual(result['cost_per_accepted_outcome']['status'], 'missing')
            self.assertEqual(result['cost_saving_roi']['status'], 'missing')

    def test_zero_accepted_outcomes_and_zero_spend_are_undefined(self):
        record = costing()
        record['accepted_outcomes'] = 0
        self.assertEqual(economics(record)['cost_per_accepted_outcome']['status'], 'undefined_zero_denominator')
        record['costs'] = dict.fromkeys(record['costs'], 0)
        self.assertEqual(economics(record)['cost_saving_roi']['status'], 'undefined_zero_denominator')

    def test_unknown_baseline_and_negative_savings(self):
        record = costing()
        record['baseline_cost'] = None
        self.assertEqual(economics(record)['cost_saving_roi']['status'], 'missing')
        record['baseline_cost'] = 50
        self.assertEqual(Decimal(economics(record)['cost_saving_roi']['value']), Decimal('-0.5'))

    def test_reject_numeric_poison_and_preserve_simulation(self):
        for bad in ('NaN', 'Infinity', '-1', '1e999999999', True, 0.1, [], '0.' + '1' * 100):
            record = costing()
            record['costs']['human'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                economics(record)
        record = costing()
        record['evidence_kind'] = 'simulated'
        self.assertEqual(economics(record)['evidence_kind'], 'simulated')

    def test_caller_decimal_precision_cannot_change_result(self):
        expected = economics(costing())
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(economics(costing()), expected)

    def test_caller_rounding_and_traps_do_not_change_ratio(self):
        record = costing()
        record['accepted_outcomes'] = 3
        expected = economics(record)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            self.assertEqual(economics(record), expected)


class CoverageTests(unittest.TestCase):
    def test_missing_empty_and_partial_inventories_differ(self):
        def cover(n, d):
            return data_coverage({'schema': 'coverage/v1', 'evidence_kind': 'attributed',
                                  'numerator': n, 'denominator': d})
        self.assertEqual(cover(0, 0)['coverage']['status'], 'undefined_zero_denominator')
        self.assertEqual(cover(3, None)['coverage']['status'], 'missing')
        self.assertEqual(cover(3, 4)['coverage']['value'], '0.75')
        self.assertEqual(cover(3, 4)['evidence_kind'], 'attributed')
        with self.assertRaises(ValueError):
            cover(5, 4)
        with self.assertRaises(ValueError):
            cover(True, 4)


class ComparisonTests(unittest.TestCase):
    def test_improvement_only_qualifies_for_review(self):
        result = assess_improvement(comparison())
        self.assertTrue(result['review_eligible'])
        self.assertFalse(result['promotion_authorized'])
        self.assertFalse(result['execution_authorized'])

    def test_missing_holdout_defects_and_changed_evaluator_block(self):
        cases = [('holdout', None, 'holdout_missing'),
                 ('critical_defects', None, 'critical_defects_unknown'),
                 ('critical_defects', 1, 'critical_defects_present'),
                 ('evidence_kind', 'simulated', 'not_observed_evidence')]
        for key, value, blocker in cases:
            record = comparison()
            record[key] = value
            result = assess_improvement(record)
            with self.subTest(blocker=blocker):
                self.assertFalse(result['review_eligible'])
                self.assertIn(blocker, result['blockers'])
        for key in ('task_digest', 'evaluator_digest'):
            record = comparison()
            record['candidate'][key] = 'd' * 64
            self.assertIn(key + '_changed', assess_improvement(record)['blockers'])

    def test_holdout_reuse_empty_and_regression_block(self):
        cases = [('digest', 'a' * 64, 'holdout_not_separate'),
                 ('count', 0, 'holdout_incomplete'),
                 ('candidate_quality', None, 'holdout_incomplete'),
                 ('candidate_quality', '0.79', 'holdout_regressed')]
        for key, value, blocker in cases:
            record = comparison()
            record['holdout'][key] = value
            with self.subTest(blocker=blocker):
                self.assertIn(blocker, assess_improvement(record)['blockers'])

    def test_cheaper_cannot_mask_quality_regression(self):
        record = comparison()
        record['thresholds']['objective'] = 'cost'
        record['candidate'].update(quality='0.79', cost_per_accepted=1)
        self.assertIn('quality_regressed', assess_improvement(record)['blockers'])

    def test_zero_cost_baseline_missing_metrics_and_no_gain_block(self):
        record = comparison()
        record['thresholds']['objective'] = 'cost'
        record['baseline']['cost_per_accepted'] = 0
        self.assertIn('cost_baseline_zero', assess_improvement(record)['blockers'])
        record = comparison()
        record['candidate']['quality'] = None
        self.assertIn('metrics_missing', assess_improvement(record)['blockers'])
        record = comparison()
        record['candidate'] = deepcopy(record['baseline'])
        record['thresholds']['min_quality_gain'] = 0
        self.assertIn('quality_gain_insufficient', assess_improvement(record)['blockers'])

    def test_cost_increase_cannot_silently_buy_quality(self):
        record = comparison()
        record['candidate']['cost_per_accepted'] = 101
        self.assertIn('cost_increased', assess_improvement(record)['blockers'])

    def test_all_apis_are_non_mutating_and_reject_unknown_fields(self):
        for function, record in [(lambda r: validate_handoff(r, as_of=AS_OF), handoff()), (economics, costing()),
                                 (assess_improvement, comparison())]:
            before = deepcopy(record)
            function(record)
            self.assertEqual(record, before)
            record['permission'] = 'allow'
            with self.assertRaises(ValueError):
                function(record)


if __name__ == '__main__':
    unittest.main()
