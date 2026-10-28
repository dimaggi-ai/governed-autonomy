import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_adapters import CLAUDE_TOOLS, MAX_ROLES, install, load, render, validate, verify_installed

MANIFEST = Path(__file__).resolve().parents[1] / 'config' / 'roles.json'
BASE_ROLES = ['dimaggi-coordinator', 'dimaggi-builder', 'dimaggi-assurance', 'dimaggi-intelligence',
              'dimaggi-product', 'dimaggi-infrastructure', 'dimaggi-economics']
# Rendering of the seven roles at base ee08cd3, with the manifest digest line masked.
BASE_RENDER_SHA256 = '664375b96bbc857299d817884b231af13d17a29ab5e9bde395c1684ed1921896'
# The one intended common-instruction change since base: the peer-approval paragraph now records
# Maggie's time-boxed exception (AGENTS.md and POLICY.md, 27 October 2026) instead of a blanket ban.
BASE_PERMISSION_PARAGRAPH = ("Permission prompts. Never approve another agent's permission or access prompt. You may decline "
                             "it and log the blocked action and reason. Escalate required access through the existing human "
                             "decision route; do not work around a denial.")


def claude_tools(content):
    return next(line for line in content.splitlines() if line.startswith('tools: '))[len('tools: '):]


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

    def test_file_parent_refused_before_any_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / '.claude'
            parent.mkdir()
            (parent / 'agents').write_text('preserve me')
            with self.assertRaisesRegex(ValueError, 'parent must be'):
                install(self.manifest, tmp)
            self.assertFalse((Path(tmp) / '.codex').exists())
            self.assertEqual((parent / 'agents').read_text(), 'preserve me')

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



class ManifestRoleTests(unittest.TestCase):
    """The shipped manifest: two added roles, preserved existing roles, exact tool grants."""

    def setUp(self):
        self.manifest = load(MANIFEST)
        self.out = render(self.manifest)

    def test_shipped_manifest_has_nine_roles_including_the_two_added(self):
        names = [r['name'] for r in self.manifest['roles']]
        self.assertEqual(names, BASE_ROLES + ['dimaggi-security', 'dimaggi-si-labs'])
        self.assertEqual(len(self.out), 18)

    def test_existing_roles_render_unchanged_apart_from_manifest_digest(self):
        base = copy.deepcopy(self.manifest)
        base['roles'] = [r for r in base['roles'] if r['name'] in BASE_ROLES]
        current = next(p for p in base['common_instructions'].split('\n') if p.startswith('Permission prompts.'))
        base['common_instructions'] = base['common_instructions'].replace(current, BASE_PERMISSION_PARAGRAPH)
        mask = lambda v: re.sub(r'Role manifest SHA-256: [0-9a-f]{64}', 'Role manifest SHA-256: <digest>', v)
        blob = json.dumps({k: mask(v) for k, v in sorted(render(base).items())}, sort_keys=True)
        self.assertEqual(hashlib.sha256(blob.encode()).hexdigest(), BASE_RENDER_SHA256)
        for name in BASE_ROLES:   # and the same files come out of the nine-role manifest
            for path in (f'.codex/agents/{name}.toml', f'.claude/agents/{name}.md'):
                self.assertEqual(mask(self.out[path]).replace(current, BASE_PERMISSION_PARAGRAPH),
                                 mask(render(base)[path]))

    def test_peer_approval_wording_matches_the_narrow_time_boxed_exception(self):
        common = self.manifest['common_instructions']
        para = next(p for p in common.split('\n') if p.startswith('Permission prompts.'))
        for phrase in ('until 3 November 2026', 'only for reading files',
                       "editing files inside the asking agent's own assigned paths", 'using synthetic data',
                       'secrets or credentials', 'settings, hooks or Tool Guard policy', 'push, merge, deploy or publish',
                       'deletes', 'payments or costs', 'new network destinations', 'log each approval in COORDINATION.md',
                       'No permanent auto-approval configuration', 'the exception lapses', 'decline a prompt'):
            self.assertIn(phrase, para)
        self.assertEqual(common.count('Permission prompts.'), 1)
        for text in self.out.values():   # every generated file carries the same wording
            self.assertIn('until 3 November 2026', text)

    def test_exact_claude_tool_grants(self):
        expect = {'dimaggi-coordinator': 'scoped_writer', 'dimaggi-builder': 'scoped_writer',
                  'dimaggi-assurance': 'read_only', 'dimaggi-intelligence': 'read_only',
                  'dimaggi-product': 'read_only', 'dimaggi-infrastructure': 'read_only',
                  'dimaggi-economics': 'read_only', 'dimaggi-security': 'read_only',
                  'dimaggi-si-labs': 'offline_writer'}
        for name, mode in expect.items():
            self.assertEqual(claude_tools(self.out[f'.claude/agents/{name}.md']), CLAUDE_TOOLS[mode], name)
        self.assertEqual(claude_tools(self.out['.claude/agents/dimaggi-si-labs.md']), 'Read, Glob, Grep, Write, Edit')
        self.assertEqual(claude_tools(self.out['.claude/agents/dimaggi-security.md']),
                         'Read, Glob, Grep, WebFetch, WebSearch')

    def test_no_shell_or_web_for_si_labs_and_no_write_for_security(self):
        si = claude_tools(self.out['.claude/agents/dimaggi-si-labs.md']).split(', ')
        for tool in ('Bash', 'WebFetch', 'WebSearch'):
            self.assertNotIn(tool, si)
        sec = claude_tools(self.out['.claude/agents/dimaggi-security.md']).split(', ')
        for tool in ('Bash', 'Write', 'Edit'):
            self.assertNotIn(tool, sec)

    def test_codex_files_claim_no_isolation_and_carry_no_permission_keys(self):
        for name in ('dimaggi-security', 'dimaggi-si-labs'):
            data = tomllib.loads(self.out[f'.codex/agents/{name}.toml'])
            self.assertEqual(set(data), {'name', 'description', 'developer_instructions'})
        si = tomllib.loads(self.out['.codex/agents/dimaggi-si-labs.toml'])['developer_instructions']
        self.assertIn('inherits its parent sandbox, so there this line is an instruction, not isolation', si)
        for text in self.out.values():
            self.assertNotRegex(text.lower(), r'sandbox_mode|network_access|approval_policy|permissionmode')

    def test_role_count_bounds(self):
        base = load(MANIFEST)
        template = base['roles'][0]
        for count, ok in ((0, False), (1, True), (9, True), (MAX_ROLES, True), (MAX_ROLES + 1, False)):
            m = copy.deepcopy(base)
            m['roles'] = [dict(template, name=f'dimaggi-r{i:03d}') for i in range(count)]
            if ok:
                validate(m)
            else:
                with self.assertRaises(ValueError):
                    validate(m)

    def test_unknown_or_missing_mode_refused(self):
        for mode in ('offline-writer', 'admin', None, ''):
            m = load(MANIFEST)
            m['roles'][-1]['mode'] = mode
            with self.assertRaises(ValueError):
                validate(m)
        m = load(MANIFEST)
        del m['roles'][-1]['mode']
        with self.assertRaises(ValueError):
            validate(m)

    def test_seven_role_install_is_refused_not_overwritten(self):
        # A workspace holding the base seven-role files must not be silently replaced: the digest changed.
        with tempfile.TemporaryDirectory() as tmp:
            base = load(MANIFEST)
            base['roles'] = base['roles'][:7]
            install(base, tmp)
            before = {p: (Path(tmp) / p).read_text() for p in render(base)}
            with self.assertRaisesRegex(ValueError, 'existing adapter differs'):
                install(self.manifest, tmp)
            self.assertEqual(before, {p: (Path(tmp) / p).read_text() for p in render(base)})
            self.assertFalse((Path(tmp) / '.claude/agents/dimaggi-si-labs.md').exists())


if __name__ == '__main__':
    unittest.main()
