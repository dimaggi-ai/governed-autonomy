"""Adversarial contract checks; no network, no model equations or live claims."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from agent_profile_contract import validate, strict_loads


class ProfileContractTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((ROOT / 'config' / 'agent-profile-fixtures.json').read_text())
        self.today = dt.date(2026, 10, 21)

    def errors(self):
        return validate(self.doc, as_of=self.today)

    def assertRejected(self, phrase):
        errors = self.errors()
        self.assertTrue(any(phrase in error for error in errors), errors)

    def test_authored_fixture_is_structurally_valid_not_measured(self):
        self.assertEqual(self.errors(), [])
        self.assertFalse(self.doc['execution_authority'])
        self.assertNotIn('measured', [e['kind'] for e in self.doc['evidence']])

    def test_payload_tampering(self):
        self.doc['evidence'][0]['payload'] += ' '
        self.assertRejected('hash mismatch')

    def test_missing_provenance(self):
        self.doc['profiles'][0]['claims'][0]['evidence'] = 'absent'
        self.assertRejected('missing provenance')

    def test_source_cannot_support_an_unrelated_fact(self):
        self.doc['profiles'][0]['claims'][0]['value'] = 999999
        self.assertRejected('not bound to source')

    def test_unqualified_gpu_tpu_equivalence(self):
        target = copy.deepcopy(self.doc['profiles'][0])
        target['id'] = 'profile-tpu'
        target['config'].update(hardware='synthetic-tpu', vendor='synthetic-vendor-b', runtime='xla-fixture')
        self.doc['profiles'].append(target)
        self.doc['comparisons'] = [dict(left='profile-a',right='profile-tpu',portability_evidence='synthetic-v1')]
        self.assertRejected('portability requires exact measured')

    def test_workload_configuration_cannot_be_silently_changed(self):
        target = copy.deepcopy(self.doc['profiles'][0])
        target['id'] = 'profile-other'
        target['config']['input_tokens'] *= 2
        self.doc['profiles'].append(target)
        self.doc['comparisons'] = [dict(left='profile-a',right='profile-other')]
        self.assertRejected('workload/SLO configurations differ')

    def test_ocs_rated_is_not_measured(self):
        self.doc['evidence'][0]['kind'] = 'vendor_rated'
        claim = self.doc['network_coverage'][3]['claims'][0]
        claim['kind'] = 'measured'
        self.assertRejected('claim/evidence kind mismatch')

    def test_measured_requires_run_configuration(self):
        self.doc['evidence'][0]['kind'] = 'measured'
        self.doc['profiles'][0]['claims'][0]['kind'] = 'measured'
        self.assertRejected('measured run identity')
        self.assertRejected('exact matching configuration')

    def test_network_slo_inputs_required(self):
        self.doc['network_coverage'][3]['claims'].pop()
        self.assertRejected('missing/invalid network-SLO input loss_fraction')

    def test_slo_inputs_required(self):
        del self.doc['profiles'][0]['config']['slo']['latency_p99_ms']
        self.assertRejected('complete valid SLO')

    def test_scale_in_is_operation_not_an_unchecked_label(self):
        self.doc['network_coverage'][-1]['requires_checkpoint'] = False
        self.assertRejected('drain/consolidation')

    def test_energized_does_not_imply_qualified(self):
        self.doc['supply_states'][3]['capacity_basis'] = 'qualified_service'
        self.assertRejected('not qualified capacity')

    def test_state_relabel_requires_evidence(self):
        self.doc['supply_states'][0]['state'] = 'qualified'
        self.doc['supply_states'][0]['capacity_basis'] = 'qualified_service'
        self.assertRejected('supply state must be bound')

    def test_freshness_and_licensing(self):
        self.doc['evidence'][0]['expires'] = '2026-10-20'
        self.doc['evidence'][0]['license'] = ''
        self.assertRejected('stale evidence')
        self.assertRejected('license or reuse restriction')

    def test_bad_url_and_duplicate_id(self):
        self.doc['evidence'][0]['url'] = 'https://user:secret@example.org/'
        self.doc['evidence'].append(copy.deepcopy(self.doc['evidence'][0]))
        self.assertRejected('invalid source URL')
        self.assertRejected('duplicate evidence id')

    def test_nonfinite_boolean_and_bad_types_fail_closed(self):
        for value in (float('nan'), float('inf'), True, 'fast'):
            self.doc['network_coverage'][0]['claims'][0]['value'] = value
            self.assertRejected('invalid network-SLO input')
        self.doc['evidence'][0]['kind'] = []
        self.doc['supply_states'][0]['state'] = []
        self.assertRejected('invalid evidence kind')
        self.assertRejected('distinct supply state')

    def test_missing_network_tier(self):
        self.doc['network_coverage'].pop()
        self.assertRejected('all declared network tiers')

    def test_strict_parser_rejects_duplicates_and_nonfinite(self):
        for text in ('{"x": 1, "x": 2}', '{"x": NaN}', '{"x": Infinity}', '{"x": 1e999}'):
            with self.assertRaises(ValueError):
                strict_loads(text)
        row = self.doc['evidence'][0]
        row['payload'] = '{"facts": [], "facts": []}'
        row['sha256'] = hashlib.sha256(row['payload'].encode()).hexdigest()
        self.assertRejected('payload must encode an object')

    def test_empty_contract_sections_refused(self):
        for key in ('evidence', 'profiles', 'supply_states', 'comparisons'):
            original = self.doc[key]
            self.doc[key] = []
            self.assertRejected(f'{key} must be a nonempty list')
            self.doc[key] = original

    def test_unknown_fields_and_duplicate_supply(self):
        self.doc['allow_execution'] = True
        self.doc['profiles'][0]['auto_approve'] = True
        self.doc['supply_states'].append(copy.deepcopy(self.doc['supply_states'][0]))
        self.assertRejected('document: missing or unknown')
        self.assertRejected('profile: missing or unknown')
        self.assertRejected('duplicate supply id')

    def test_portability_run_must_bind_both_configs(self):
        target = copy.deepcopy(self.doc['profiles'][0])
        target['id'] = 'target'
        target['config']['hardware'] = 'other'
        self.doc['profiles'].append(target)
        a, b = self.doc['profiles'][0]['config'], target['config']
        payload = json.dumps({'portability': dict(from_config=a, to_config=b,
                             qualified=True, **{'from': 'profile-a', 'to': 'target'})})
        proof = copy.deepcopy(self.doc['evidence'][0])
        proof.update(id='proof', kind='measured', payload=payload,
                     sha256=hashlib.sha256(payload.encode()).hexdigest(),
                     run={'id': 'run', 'config': {'unrelated': 'configuration'}})
        self.doc['evidence'].append(proof)
        self.doc['comparisons'] = [dict(left='profile-a', right='target', portability_evidence='proof')]
        self.assertRejected('portability requires exact measured')
        proof['run']['config'] = {'from': a, 'to': b}
        self.assertFalse(any('portability requires' in e for e in self.errors()))

    def test_empty_measured_network_config_refused(self):
        network = self.doc['network_coverage'][3]
        network['config'] = {}
        evidence = self.doc['evidence'][0]
        evidence['kind'] = 'measured'
        evidence['run'] = {'id': 'observed-fixture', 'config': {}}
        for claim in network['claims']:
            claim['kind'] = 'measured'
        self.assertRejected('network measurement config: missing or unknown')

    def test_huge_integer_network_value_is_rejected_without_crash(self):
        self.doc['network_coverage'][0]['claims'][0]['value'] = 10 ** 400
        self.assertRejected('missing/invalid network-SLO input')

    def rewrite_source(self, transform):
        row = self.doc['evidence'][0]
        payload = json.loads(row['payload'])
        transform(payload)
        row['payload'] = json.dumps(payload)
        row['sha256'] = hashlib.sha256(row['payload'].encode()).hexdigest()

    def test_boolean_source_fact_cannot_back_numeric_claim_and_can_recover(self):
        original = copy.deepcopy(self.doc)
        for prop, replacement in (('rtt_ms', True), ('loss_fraction', False)):
            with self.subTest(property=prop):
                self.doc = copy.deepcopy(original)
                def change(payload):
                    item = next(f for f in payload['facts']
                                if f['subject'] == 'chip' and f['property'] == prop)
                    item['value'] = replacement
                self.rewrite_source(change)
                self.assertRejected('invalid source fact')
                self.assertRejected('fact not bound to source payload')
                def repair(payload):
                    item = next(f for f in payload['facts']
                                if f['subject'] == 'chip' and f['property'] == prop)
                    item['value'] = 1 if prop == 'rtt_ms' else 0
                self.rewrite_source(repair)
                self.assertEqual(self.errors(), [])

    def test_invalid_unreferenced_source_facts_refused(self):
        original = copy.deepcopy(self.doc)
        for field, value in (('subject', ''), ('property', ' '), ('unit', None),
                             ('value', True), ('value', False), ('value', '1'),
                             ('value', -1), ('value', 10 ** 400)):
            with self.subTest(field=field, value=value):
                self.doc = copy.deepcopy(original)
                item = dict(subject='unused', property='capacity', value=1, unit='units')
                item[field] = value
                self.rewrite_source(lambda payload: payload['facts'].append(item))
                self.assertRejected('invalid source fact')

    def test_finite_integral_float_source_fact_remains_numeric(self):
        self.rewrite_source(lambda payload: [f.update(value=float(f['value']))
                                            for f in payload['facts']])
        self.assertEqual(self.errors(), [])

    def test_measured_config_boolean_cannot_impersonate_numeric_concurrency(self):
        row = self.doc['evidence'][0]
        row['kind'] = 'measured'
        row['run'] = {'id': 'synthetic-regression-run',
                      'config': copy.deepcopy(self.doc['profiles'][0]['config'])}
        self.doc['profiles'][0]['claims'][0]['kind'] = 'measured'
        row['run']['config']['concurrency'] = True
        self.assertRejected('profile-a: measured fact lacks exact matching configuration')
        row['run']['config']['concurrency'] = 1
        self.assertFalse(any('profile-a: measured fact lacks exact matching configuration'
                             in e for e in self.errors()))

    def test_numeric_portability_qualification_is_not_boolean(self):
        row = copy.deepcopy(self.doc['evidence'][0])
        row['id'] = 'unreferenced-portability'
        row['payload'] = json.dumps({'portability': {
            'from': 'profile-a', 'to': 'target', 'qualified': 1,
            'from_config': {}, 'to_config': {}}})
        row['sha256'] = hashlib.sha256(row['payload'].encode()).hexdigest()
        self.doc['evidence'].append(row)
        self.assertRejected('portability qualified must be a boolean')

    def test_no_execution_authority(self):
        self.doc['execution_authority'] = True
        self.assertRejected('execution_authority must be false')


if __name__ == '__main__':
    unittest.main()
