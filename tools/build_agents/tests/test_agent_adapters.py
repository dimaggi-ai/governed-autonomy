import copy
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_adapters import install, load, render, validate, verify_installed


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'schema': 'dimaggi-build-roles/v1', 'common_instructions': 'Use the existing tracker. Never expand authority.', 'roles': [
            {'name': 'dimaggi-builder', 'description': 'Implement bounded tasks.', 'instructions': 'Preserve scope.\nQuote "facts" and paths \\ safely.', 'mode': 'scoped_writer'},
            {'name': 'dimaggi-assurance', 'description': 'Independently review evidence.', 'instructions': 'No self acceptance.', 'mode': 'read_only'}]}

    def test_codex_toml_round_trip_and_no_permission_override(self):
        outputs = render(self.manifest)
        for path, content in outputs.items():
            if path.endswith('.toml'):
                data = tomllib.loads(content)
                self.assertEqual(set(data), {'name', 'description', 'developer_instructions'})
                self.assertIn('Role manifest SHA-256:', data['developer_instructions'])
                self.assertIn('Never expand authority.', data['developer_instructions'])

    def test_control_and_astral_unicode_round_trip(self):
        self.manifest['common_instructions'] += ' DEL:' + chr(127) + ' emoji:🧪'
        outputs = render(self.manifest)
        for path, content in outputs.items():
            if path.endswith('.toml'):
                self.assertIn(self.manifest['common_instructions'], tomllib.loads(content)['developer_instructions'])

    def test_reviewer_claude_has_no_shell_or_write_tool(self):
        content = render(self.manifest)['.claude/agents/dimaggi-assurance.md']
        tools = next(line for line in content.splitlines() if line.startswith('tools:'))
        self.assertNotIn('Bash', tools)
        self.assertNotIn('Write', tools)
        self.assertIn('model: inherit', content)

    def test_idempotent_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = install(self.manifest, tmp)
            self.assertEqual(paths, install(self.manifest, tmp))
            self.assertEqual(len(paths), 4)

    def test_preflight_rejects_conflict_without_partial_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / '.claude/agents/dimaggi-assurance.md'
            target.parent.mkdir(parents=True)
            target.write_text('existing independent definition')
            with self.assertRaises(ValueError):
                install(self.manifest, tmp)
            self.assertFalse((Path(tmp) / '.codex').exists())
            self.assertEqual(target.read_text(), 'existing independent definition')

    def test_symlink_destination_refused(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            (Path(tmp) / '.codex').symlink_to(other, target_is_directory=True)
            with self.assertRaises(ValueError):
                install(self.manifest, tmp)
            self.assertEqual(list(Path(other).iterdir()), [])

    def test_path_traversal_name_and_duplicate_name_refused(self):
        for bad in ['../builder', 'dimaggi-../builder', 'Builder', 'dimaggi-builder/child']:
            m = copy.deepcopy(self.manifest)
            m['roles'][0]['name'] = bad
            with self.assertRaises(ValueError):
                validate(m)
        m = copy.deepcopy(self.manifest)
        m['roles'][1]['name'] = m['roles'][0]['name']
        with self.assertRaises(ValueError):
            validate(m)

    def test_unknown_permission_field_refused(self):
        m = copy.deepcopy(self.manifest)
        m['roles'][0]['permissionMode'] = 'bypassPermissions'
        with self.assertRaises(ValueError):
            validate(m)

    def test_check_does_not_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            install(self.manifest, tmp, check=True)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_drift_check_rejects_missing_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            install(self.manifest, tmp)
            path = Path(tmp) / '.codex/agents/dimaggi-builder.toml'
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'missing installed'):
                verify_installed(self.manifest, tmp)

    def test_duplicate_json_key_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.json'
            path.write_text('{"roles": [], "roles": []}')
            with self.assertRaises(ValueError):
                load(path)

    def test_changed_manifest_changes_both_harnesses(self):
        original = render(self.manifest)
        self.manifest['common_instructions'] += ' Preserve checkpoints.'
        changed = render(self.manifest)
        self.assertTrue(all(original[k] != changed[k] for k in original))


if __name__ == '__main__':
    unittest.main()
