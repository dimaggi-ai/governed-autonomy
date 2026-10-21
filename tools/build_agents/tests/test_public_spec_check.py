import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from public_spec_check import check, extract, memory_bytes
from agent_profile_contract import strict_loads


class PublicSpecTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = strict_loads((ROOT / 'config/public-spec-source.json').read_text())

    def test_actual_upstream_sample_format_and_numeric_units(self):
        facts = check(self.snapshot)
        self.assertEqual(facts['accelerators_per_node'], 8)
        self.assertEqual(facts['number_of_nodes'], 1)
        self.assertEqual(facts['accelerator_memory_bytes'], 256_000_000_000)
        self.assertEqual(facts['host_memory_bytes'], 2_473_901_162_496)
        self.assertEqual(facts['accelerator_interconnect'], 'XGMI')
        self.assertFalse(facts['measured_performance'])
        self.assertFalse(facts['execution_authority'])

    def test_tamper_with_recomputed_hash_still_refused(self):
        self.snapshot['record_utf8'] = self.snapshot['record_utf8'].replace('256 GB', '512 GB')
        self.snapshot['record_sha256'] = hashlib.sha256(self.snapshot['record_utf8'].encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'digest'):
            check(self.snapshot)

    def test_wrong_license_and_provenance_refused(self):
        for key in ('source_commit', 'source_url', 'license_id', 'license_url'):
            mutated = copy.deepcopy(self.snapshot)
            mutated[key] = 'incorrect'
            with self.assertRaises(ValueError):
                check(mutated)

    def test_license_content_retained_and_pinned(self):
        self.assertIn('Apache License', self.snapshot['license_utf8'])
        self.snapshot['license_utf8'] = ''
        with self.assertRaisesRegex(ValueError, 'license digest'):
            check(self.snapshot)

    def test_ambiguous_and_nonfinite_units_refused(self):
        for value in ('256', '256 Gb', '1.7T', 'NaN GB', '-1 GB', '0 GB', '1e100 GB'):
            with self.assertRaises(ValueError):
                memory_bytes(value)
        self.assertNotEqual(memory_bytes('256 GB'), memory_bytes('256 GiB'))

    def test_source_numeric_type_mismatch_refused(self):
        record = strict_loads(self.snapshot['record_utf8'])
        for value in (True, '8', 0, 10**400):
            record['accelerators_per_node'] = value
            with self.assertRaises(ValueError):
                extract(record)

    def test_sample_not_promoted_to_measured_or_authorized(self):
        self.snapshot['not_a_measured_benchmark'] = False
        with self.assertRaises(ValueError):
            check(self.snapshot)

    def test_unknown_envelope_field_and_duplicate_json_refused(self):
        self.snapshot['approved'] = True
        with self.assertRaises(ValueError):
            check(self.snapshot)
        with self.assertRaises(ValueError):
            strict_loads('{"accelerators_per_node":8,"accelerators_per_node":16}')


if __name__ == '__main__':
    unittest.main()
