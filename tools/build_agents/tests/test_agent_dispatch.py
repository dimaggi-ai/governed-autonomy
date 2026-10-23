"""Offline regression tests: no model calls, credentials, network, push or merge."""
import contextlib
import copy
import errno
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'agent_dispatch.py'
if not MODULE.exists():
    MODULE = Path(__file__).with_name('agent_dispatch.py')
spec = importlib.util.spec_from_file_location('agent_dispatch', MODULE)
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)
supervisor_spec = importlib.util.spec_from_file_location('agent_supervisor', MODULE.with_name('agent_supervisor.py'))
supervisor = importlib.util.module_from_spec(supervisor_spec)
supervisor_spec.loader.exec_module(supervisor)


def claim_worker(root, start, release, answers):
    start.wait(5)
    try:
        with dispatch.scope_claim(root, 'example/service', ['SRC/./']):
            answers.put('claimed')
            release.wait(5)
    except dispatch.Refusal:
        answers.put('refused')


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.checkout = self.root / 'checkouts' / 'work'
        self.checkout.mkdir(parents=True)
        self.git('init', '-b', 'codex/7-example')
        self.git('config', 'user.name', 'Example Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'core.hooksPath', '/dev/null')
        self.git('remote', 'add', 'origin', 'https://github.com/example/service.git')
        (self.checkout / 'src').mkdir()
        (self.checkout / 'src' / 'one.py').write_text('original\n')
        (self.checkout / 'outside.txt').write_text('original\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.packet = dict(schema=dispatch.SCHEMA,
            issue_url='https://github.com/example/tasks/issues/7', execution_block='',
            tool='codex', repository='example/service', base_sha=self.git('rev-parse', 'HEAD'),
            branch='codex/7-example', paths=['src'], reviewer='claude-code',
            timebox_seconds=3, max_output_bytes=8192, task='Make the scoped change.')
        self.refresh_execution()
        self.binary = self.root / 'fake-agent'
        self.fake("import sys\nprint(sys.stdin.read())\n")
        self.which = mock.patch.object(dispatch.shutil, 'which', return_value=str(self.binary))
        self.which.start()
        self.addCleanup(self.which.stop)

    def git(self, *args):
        process = subprocess.run(['git', '-C', str(self.checkout), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        return process.stdout.strip()

    def refresh_execution(self):
        p = self.packet
        values = [('Implementer', p['tool']), ('Repository', p['repository']),
            ('Base SHA', p['base_sha']), ('Branch', p['branch']),
            ('Owned paths', json.dumps(p['paths'], separators=(',', ':'))),
            ('Cross-reviewer', p['reviewer']), ('Timebox seconds', str(p['timebox_seconds'])),
            ('Output budget bytes', str(p['max_output_bytes']))]
        p['execution_block'] = 'Execution:\n' + '\n'.join(k + ': ' + str(v) for k, v in values)

    def fake(self, body):
        self.binary.write_text('#!' + sys.executable + '\n' + body)
        self.binary.chmod(0o700)

    def arguments(self):
        return (self.packet, ['example/service'], 'example/tasks', self.root, self.checkout)

    def plan(self):
        return dispatch.plan(*self.arguments())

    def execute(self):
        return dispatch.execute(*self.arguments())

    def test_plan_is_read_only_and_preserves_cli_safeguards(self):
        plan = self.plan()
        self.assertEqual(plan['argv'][1:], ['exec', '--sandbox', 'workspace-write', '--json', '-'])
        self.assertEqual(plan['prompt_transport'], 'stdin')
        self.assertFalse((self.root / '.agent-dispatch-locks').exists())
        self.assertFalse((self.root / '.agent-dispatch-runs').exists())
        self.packet.update(tool='claude-code', reviewer='codex')
        self.refresh_execution()
        self.assertEqual(self.plan()['argv'][1:], ['-p', '--permission-mode', 'manual',
            '--permission-prompts', 'none', '--output-format', 'json'])

    def test_unknown_schema_fields_duplicate_json_and_bad_types_fail_closed(self):
        baseline = copy.deepcopy(self.packet)
        variants = [dict(schema='unknown'), dict(extra=True), dict(tool=[]), dict(reviewer={}),
                    dict(timebox_seconds=True), dict(max_output_bytes=0), dict(task=''),
                    dict(reviewer='codex'), dict(base_sha='main'), dict(paths=[])]
        for change in variants:
            with self.subTest(change=change):
                self.packet = dict(baseline, **change)
                with self.assertRaises(dispatch.Refusal):
                    self.plan()
        duplicate = self.root / 'duplicate.json'
        duplicate.write_text('{"schema":"one","schema":"two"}')
        with self.assertRaises(dispatch.Refusal):
            dispatch.load_json(duplicate)

    def test_execution_snapshot_must_match_and_extra_fields_fail(self):
        for block in [self.packet['execution_block'].replace('codex\n', 'claude-code\n', 1),
                      self.packet['execution_block'] + '\nAuthority: unknown',
                      self.packet['execution_block'] + '\nBranch: duplicate']:
            with self.subTest(block=block), self.assertRaises(dispatch.Refusal):
                dispatch.validate_packet(dict(self.packet, execution_block=block),
                                         ['example/service'], 'example/tasks')

    def test_execution_owned_path_order_has_explicit_refusal(self):
        self.packet['paths'] = ['src', 'outside.txt']
        self.refresh_execution()
        self.packet['execution_block'] = self.packet['execution_block'].replace(
            '["src","outside.txt"]', '["outside.txt","src"]')
        with self.assertRaisesRegex(dispatch.Refusal, 'JSON order and spelling exactly'):
            self.plan()

    def test_known_contextual_prose_is_preserved_without_authority(self):
        self.packet['execution_block'] += '\nModel: example-model\nSession: sample-session\nLast checked: unknown\nHandoff: ask peer to review'
        _, receipt = self.execute()
        self.assertEqual(receipt['execution_block'], self.packet['execution_block'])
        self.assertEqual(receipt['result'], 'completed_pending_review')

    def test_wrong_repository_or_tracker_is_refused(self):
        with self.assertRaises(dispatch.Refusal):
            dispatch.plan(self.packet, ['example/different'], 'example/tasks', self.root, self.checkout)
        with self.assertRaises(dispatch.Refusal):
            dispatch.plan(self.packet, ['example/service'], 'example/different', self.root, self.checkout)
        self.git('remote', 'set-url', 'origin', 'https://github.com/example/different.git')
        with self.assertRaises(dispatch.Refusal):
            self.plan()

    def test_wrong_head_branch_dirty_or_untracked_checkout_is_refused(self):
        for key, value in [('base_sha', '0' * 40), ('branch', 'codex/8-other')]:
            original = self.packet[key]
            self.packet[key] = value
            self.refresh_execution()
            with self.assertRaises(dispatch.Refusal):
                self.plan()
            self.packet[key] = original
        self.refresh_execution()
        (self.checkout / 'untracked').write_text('work')
        with self.assertRaises(dispatch.Refusal):
            self.plan()
        (self.checkout / 'untracked').unlink()
        (self.checkout / 'src' / 'one.py').write_text('local work')
        with self.assertRaises(dispatch.Refusal):
            self.plan()

    def test_traversal_git_metadata_and_duplicate_alias_paths_refused(self):
        for paths in [['../outside'], ['/absolute'], ['src/../../escape'], ['src\\escape'],
                      ['.git/config'], ['src', './SRC/'], ['src', 'src/.']]:
            self.packet['paths'] = paths
            self.refresh_execution()
            with self.subTest(paths=paths), self.assertRaises(dispatch.Refusal):
                self.plan()

    def test_symlink_scope_and_descendant_refused_even_inside_checkout(self):
        (self.checkout / 'src' / 'link').symlink_to(self.checkout / 'outside.txt')
        self.git('add', 'src/link')
        self.git('commit', '-qm', 'link fixture')
        self.packet['base_sha'] = self.git('rev-parse', 'HEAD')
        for paths in [['src/link'], ['src']]:
            self.packet['paths'] = paths
            self.refresh_execution()
            with self.assertRaises(dispatch.Refusal):
                self.plan()

    def test_checkout_escape_and_alias_refused(self):
        with self.assertRaises(dispatch.Refusal):
            dispatch.check_checkout(self.packet, self.root / 'checkouts', self.root)
        alias = self.root / 'alias'
        alias.symlink_to(self.checkout, target_is_directory=True)
        with self.assertRaises(dispatch.Refusal):
            dispatch.check_checkout(self.packet, self.root, alias)

    def test_cli_from_checkout_or_missing_refused(self):
        with mock.patch.object(dispatch.shutil, 'which', return_value=None):
            with self.assertRaises(dispatch.Refusal):
                self.plan()
        # A tracked executable cannot impersonate the installed agent CLI.
        candidate = self.checkout / 'src' / 'one.py'
        candidate.chmod(0o755)
        self.git('add', 'src/one.py')
        self.git('commit', '-qm', 'executable fixture')
        self.packet['base_sha'] = self.git('rev-parse', 'HEAD')
        self.refresh_execution()
        with mock.patch.object(dispatch.shutil, 'which', return_value=str(candidate)):
            with self.assertRaises(dispatch.Refusal):
                self.plan()

    def test_unicode_path_aliases_cannot_bypass_claim(self):
        with dispatch.scope_claim(self.root, 'example/service', ['caf\u00e9']):
            with self.assertRaises(dispatch.Refusal):
                with dispatch.scope_claim(self.root, 'example/service', ['cafe\u0301/file.py']):
                    pass

    def test_case_and_unicode_aliases_do_not_expand_write_authorization(self):
        # Git names can be distinct on a case-sensitive host. Mock only status
        # discovery so every host exercises the actual receipt authorization.
        variants = [('src/one.py', 'SRC/one.py'),
                    ('src/Token.py', 'src/token.py'),
                    ('src/caf\u00e9.py', 'src/cafe\u0301.py'),
                    ('src/stra\u00dfe.py', 'src/strasse.py')]
        for owned, changed in variants:
            with self.subTest(owned=owned, changed=changed):
                self.packet['paths'] = [owned]
                self.refresh_execution()
                with mock.patch.object(dispatch, 'changed_paths', return_value=[changed]):
                    _, receipt = self.execute()
                self.assertEqual(receipt['result'], 'failed')
                self.assertEqual(receipt['outside_scope'], [changed])

    def test_exact_git_spelling_succeeds_even_for_unicode_paths(self):
        for owned in ('src/one.py', 'src/Token.py', 'src/caf\u00e9.py', 'src/cafe\u0301.py'):
            with self.subTest(owned=owned):
                self.packet['paths'] = [owned]
                self.refresh_execution()
                with mock.patch.object(dispatch, 'changed_paths', return_value=[owned]):
                    _, receipt = self.execute()
                self.assertEqual(receipt['result'], 'completed_pending_review')
                self.assertEqual(receipt['outside_scope'], [])

    def test_claim_alias_overlap_and_disjoint_scopes(self):
        with dispatch.scope_claim(self.root, 'Example/Service', ['src']):
            for scope in ['SRC/one.py', './src/', '.']:
                with self.subTest(scope=scope), self.assertRaises(dispatch.Refusal):
                    with dispatch.scope_claim(self.root, 'example/service', [scope]):
                        pass
            with dispatch.scope_claim(self.root, 'example/service', ['docs']):
                pass
            with dispatch.scope_claim(self.root, 'example/different', ['src']):
                pass
        self.assertEqual(list((self.root / '.agent-dispatch-locks').glob('*.json')), [])

    def test_simultaneous_claims_have_exactly_one_writer(self):
        context = multiprocessing.get_context('fork')
        start, release, answers = context.Event(), context.Event(), context.Queue()
        workers = [context.Process(target=claim_worker, args=(self.root, start, release, answers))
                   for _ in range(2)]
        try:
            for worker in workers:
                worker.start()
            start.set()
            results = [answers.get(timeout=5), answers.get(timeout=5)]
            self.assertCountEqual(results, ['claimed', 'refused'])
        finally:
            release.set()
            for worker in workers:
                worker.join(5)
                if worker.is_alive():
                    worker.kill()
                    worker.join()
        self.assertTrue(all(worker.exitcode == 0 for worker in workers))

    def test_stale_claim_never_taken_over_and_malformed_claim_blocks(self):
        directory = dispatch.private_directory(self.root / '.agent-dispatch-locks')
        claim = directory / 'old.json'
        claim.write_text(json.dumps(dict(schema='agent-scope-claim/v1',
            repository='example/service', scopes=['src'], pid=99999999)))
        with self.assertRaises(dispatch.Refusal):
            with dispatch.scope_claim(self.root, 'example/service', ['src']):
                pass
        self.assertTrue(claim.exists())
        claim.write_text('{"schema":"unknown"}')
        with self.assertRaises(dispatch.Refusal):
            with dispatch.scope_claim(self.root, 'example/service', ['elsewhere']):
                pass

    def test_lock_directory_symlink_is_refused(self):
        actual = self.root / 'actual'
        actual.mkdir(mode=0o700)
        (self.root / '.agent-dispatch-locks').symlink_to(actual, target_is_directory=True)
        with self.assertRaises(dispatch.Refusal):
            with dispatch.scope_claim(self.root, 'example/service', ['src']):
                pass

    def test_shell_text_only_reaches_stdin(self):
        marker = self.root / 'must-not-exist'
        self.packet['task'] = '$(touch ' + str(marker) + '); `touch ' + str(marker) + '`\n--dangerous'
        path, receipt = self.execute()
        self.assertEqual(receipt['result'], 'completed_pending_review')
        self.assertEqual(receipt['process']['exit_code'], 0)
        self.assertIn(self.packet['task'], path.with_name('output.log').read_text())
        self.assertFalse(marker.exists())
        self.assertEqual(json.loads(path.read_text())['execution_block'], self.packet['execution_block'])
        self.assertEqual(list((self.root / '.agent-dispatch-locks').glob('*.json')), [])

    def test_scoped_change_succeeds_pending_independent_review(self):
        self.fake("from pathlib import Path\nPath('src/one.py').write_text('updated\\n')\n")
        _, receipt = self.execute()
        self.assertEqual(receipt['result'], 'completed_pending_review')
        self.assertEqual(receipt['changed_paths'], ['src/one.py'])
        self.assertEqual(receipt['outside_scope'], [])

    def test_outside_scope_or_new_symlink_change_fails_receipt(self):
        self.fake("from pathlib import Path\nPath('outside.txt').write_text('updated\\n')\n")
        _, receipt = self.execute()
        self.assertEqual(receipt['result'], 'failed')
        self.assertEqual(receipt['outside_scope'], ['outside.txt'])
        self.git('checkout', '--', 'outside.txt')
        self.fake("from pathlib import Path\nPath('src/link').symlink_to('../outside.txt')\n")
        _, receipt = self.execute()
        self.assertEqual(receipt['result'], 'failed')
        self.assertEqual(receipt['symlink_changes'], ['src/link'])

    def test_unclassifiable_agent_filenames_are_retained_as_failure_evidence(self):
        names = ['src/x:y', 'src/a\\b', 'src/line\nbreak', 'outside:scope']
        body = 'from pathlib import Path\n'
        for name in names:
            body += 'Path(' + repr(name) + ').write_text("changed")\n'
        body += "Path('src/link').symlink_to('../outside.txt')\n"
        self.fake(body)
        path, receipt = self.execute()
        self.assertEqual(receipt['result'], 'failed')
        self.assertCountEqual(receipt['unclassifiable_paths'], names)
        self.assertCountEqual(receipt['outside_scope'], names)
        self.assertEqual(receipt['symlink_changes'], ['src/link'])
        self.assertEqual(receipt['head_after'], self.packet['base_sha'])
        self.assertNotIn('error_type', receipt)
        saved = json.loads(path.read_text())
        self.assertEqual(saved, receipt)

    def test_owning_file_does_not_allow_replacing_its_parent_directory(self):
        self.packet['paths'] = ['src/one.py']
        self.refresh_execution()
        self.fake("from pathlib import Path\nPath('src/one.py').unlink()\nPath('src').rmdir()\nPath('src').write_text('ancestor write')\n")
        _, receipt = self.execute()
        self.assertEqual(receipt['result'], 'failed')
        self.assertIn('src', receipt['outside_scope'])
        self.assertNotIn('src/one.py', receipt['outside_scope'])

    def test_timeout_kills_runner_and_writes_failure_receipt(self):
        self.fake('import time\ntime.sleep(30)\n')
        self.packet['timebox_seconds'] = 1
        self.refresh_execution()
        start = time.monotonic()
        path, receipt = self.execute()
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(receipt['process']['reason'], 'timeout')
        self.assertLess(receipt['process']['exit_code'], 0)
        self.assertEqual(receipt['result'], 'failed')
        self.assertTrue(path.is_file())
        self.assertFalse(list((self.root / '.agent-dispatch-locks').glob('*.json')))

    def interruption_journey(self, signum):
        self.packet['timebox_seconds'] = 2
        self.refresh_execution()
        packet = self.root / 'packet.json'
        packet.write_text(json.dumps(self.packet))
        pids = self.root / 'runner-pids.json'
        self.fake('import json, os, time\nfrom pathlib import Path\n'
                  'child = os.fork()\n'
                  'if child: Path(' + repr(str(pids)) + ').write_text(json.dumps([os.getpid(), child]))\n'
                  'beat = Path("src/parent-beat" if child else "src/child-beat")\n'
                  'end = time.monotonic() + 20\n'
                  'while time.monotonic() < end:\n'
                  '    with beat.open("a") as f: f.write(str(time.monotonic()) + "\\n")\n'
                  '    time.sleep(0.05)\n')
        bin_dir = self.root / 'bin'; bin_dir.mkdir()
        (bin_dir / 'codex').symlink_to(self.binary)
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                   TMPDIR=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        argv = [sys.executable, str(MODULE), '--packet', str(packet),
                '--trusted-root', str(self.root), '--checkout', str(self.checkout),
                '--tracker-repository', 'example/tasks', '--allow-repository', 'example/service', '--execute']
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        runner_pids = []
        def live(pid):
            found = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                   capture_output=True, text=True, check=False)
            return bool(found.stdout.strip()) and not found.stdout.strip().startswith('Z')
        try:
            deadline = time.monotonic() + 5
            while not pids.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pids.exists(), 'runner never started')
            runner_pids = json.loads(pids.read_text())
            with self.assertRaises(dispatch.Refusal):
                with dispatch.scope_claim(self.root, 'example/service', ['src']): pass
            process.send_signal(signum)
            # Observe beyond the timebox without killing any process from the
            # harness. While a claim can be acquired, neither runner may be live.
            until = time.monotonic() + self.packet['timebox_seconds'] + 1
            while time.monotonic() < until:
                try:
                    with dispatch.scope_claim(self.root, 'example/service', ['src']):
                        self.assertFalse(any(live(pid) for pid in runner_pids), 'claim released while runner alive')
                except dispatch.Refusal:
                    pass
                time.sleep(0.05)
            self.assertFalse(any(live(pid) for pid in runner_pids), 'runner outlived dispatcher and timebox')
            beats = [self.checkout / 'src' / name for name in ('parent-beat', 'child-beat')]
            before = [p.read_bytes() for p in beats]
            time.sleep(0.2)
            self.assertEqual([p.read_bytes() for p in beats], before, 'writes continued after cleanup')
            stdout, stderr = process.communicate(timeout=5)
            runs = list((self.root / '.agent-dispatch-runs').iterdir())
            self.assertEqual(len(runs), 1)
            started = json.loads((runs[0] / 'supervisor-start.json').read_text())
            self.assertEqual(started['process_group'], runner_pids[0])
            self.assertFalse(live(started['supervisor_pid']))
            if signum != signal.SIGKILL:
                receipt = json.loads((runs[0] / 'receipt.json').read_text())
                self.assertEqual(receipt['result'], 'failed')
                self.assertEqual(receipt['process']['reason'], 'interrupted_' + signal.Signals(signum).name)
                self.assertTrue(receipt['process']['runner_stopped'])
                self.assertEqual(process.returncode, 1, (stdout, stderr))
                self.assertFalse(list((self.root / '.agent-dispatch-locks').glob('*.json')))
            else:
                # SIGKILL cannot produce a parent receipt. The supervisor's
                # independent process record plus retained claim are evidence.
                state = json.loads((runs[0] / 'process.json').read_text())
                self.assertEqual(state['reason'], 'dispatcher_lost')
                self.assertTrue(state['runner_stopped'])
                start = json.loads((runs[0] / 'dispatch-start.json').read_text())
                self.assertTrue((self.root / '.agent-dispatch-locks' / start['claim']).is_file())
        finally:
            # Emergency harness cleanup happens only after observations/assertions.
            if process.poll() is None: process.kill()
            process.communicate(timeout=5)
            for pid in runner_pids:
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def test_sigint_cleanup_receipt_and_claim_exclusion(self):
        self.interruption_journey(signal.SIGINT)

    def test_sigterm_cleanup_receipt_and_claim_exclusion(self):
        self.interruption_journey(signal.SIGTERM)

    def test_sighup_cleanup_receipt_and_claim_exclusion(self):
        self.interruption_journey(signal.SIGHUP)

    def test_sigkill_cannot_leave_runner_or_children_past_timebox(self):
        self.interruption_journey(signal.SIGKILL)

    def test_late_interruption_preserves_supervisor_reason(self):
        def late_signal(*args):
            os.kill(os.getpid(), signal.SIGTERM)
            return dict(reason='timeout', exit_code=-9, captured_bytes=0, runner_stopped=True)
        with mock.patch.object(dispatch, 'bounded_process', side_effect=late_signal):
            _, receipt = self.execute()
        self.assertEqual(receipt['process']['reason'], 'interrupted_SIGTERM')
        self.assertEqual(receipt['process']['supervisor_reason'], 'timeout')

    def controlled_failure_journey(self, mode):
        # Maximum four long-lived processes: dispatcher, supervisor, runner,
        # one child. Every fake runner self-expires; cleanup targets recorded IDs.
        self.packet['timebox_seconds'] = 2
        self.refresh_execution()
        packet = self.root / 'packet.json'; packet.write_text(json.dumps(self.packet))
        pids = self.root / 'runner-pids.json'
        self.fake('import json, os, time\nfrom pathlib import Path\n'
                  'child = os.fork()\n'
                  'if child:\n'
                  '    target = Path(' + repr(str(pids)) + ')\n'
                  '    target.with_suffix(".tmp").write_text(json.dumps([os.getpid(), child]))\n'
                  '    target.with_suffix(".tmp").replace(target)\n'
                  + ('else: os.setpgid(0, 0)\n' if mode == 'escaped' else '') +
                  'beat = Path("src/parent-beat" if child else "src/child-beat")\n'
                  'end = time.monotonic() + 20\n'
                  'while time.monotonic() < end:\n'
                  '    with beat.open("a") as f: f.write("beat\\n")\n'
                  '    time.sleep(0.05)\n')
        bin_dir = self.root / 'bin'; bin_dir.mkdir()
        (bin_dir / 'codex').symlink_to(self.binary)
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                   TMPDIR=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        argv = [sys.executable, str(MODULE), '--packet', str(packet),
                '--trusted-root', str(self.root), '--checkout', str(self.checkout),
                '--tracker-repository', 'example/tasks', '--allow-repository', 'example/service', '--execute']
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        runner_pids, supervisor_pid = [], None
        identities = {}
        supervisor_finished = False
        def identity(pid):
            state = subprocess.run(['ps', '-p', str(pid), '-o', 'uid=,lstart=,args='],
                                   capture_output=True, text=True, timeout=2)
            if state.returncode == 1 and not state.stdout.strip() and not state.stderr.strip():
                return None
            self.assertEqual(state.returncode, 0, state.stderr)
            self.assertTrue(state.stdout.strip(), 'unknown process identity')
            return state.stdout.strip()
        def live(pid):
            state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                   capture_output=True, text=True, timeout=2)
            if state.returncode == 1 and not state.stdout.strip() and not state.stderr.strip(): return False
            self.assertEqual(state.returncode, 0, state.stderr)
            self.assertTrue(state.stdout.strip(), 'unknown process liveness')
            return not state.stdout.strip().startswith('Z')
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                starts = list((self.root / '.agent-dispatch-runs').glob('*/supervisor-start.json'))
                if starts and pids.exists() and (self.checkout / 'src/child-beat').exists(): break
                time.sleep(0.02)
            self.assertTrue(starts and pids.exists(), 'bounded fake runner did not start')
            runner_pids = json.loads(pids.read_text())
            self.assertEqual(len(runner_pids), 2)
            run = starts[0].parent
            started = json.loads(starts[0].read_text())
            supervisor_pid = started['supervisor_pid']
            self.assertEqual(started['process_group'], runner_pids[0])
            self.assertEqual(os.getsid(supervisor_pid), supervisor_pid)
            self.assertTrue(all(os.getsid(pid) == runner_pids[0] for pid in runner_pids))
            for pid in [*runner_pids, supervisor_pid]:
                identities[pid] = identity(pid)
                self.assertIsNotNone(identities[pid])
                self.assertIn(str(self.root), identities[pid])
            if mode == 'stopped_dispatcher':
                process.send_signal(signal.SIGSTOP)
                end = time.monotonic() + 9
                while not (run / 'process.json').exists() and time.monotonic() < end: time.sleep(0.02)
                state = json.loads((run / 'process.json').read_text())
                self.assertEqual(state['reason'], 'timeout')
                self.assertTrue(state['runner_stopped'])
                self.assertFalse(any(live(pid) for pid in runner_pids))
                with self.assertRaises(dispatch.Refusal):
                    with dispatch.scope_claim(self.root, 'example/service', ['src']): pass
                process.send_signal(signal.SIGCONT)
            elif mode == 'supervisor_killed':
                os.kill(supervisor_pid, signal.SIGKILL)
            elif mode == 'escaped':
                self.assertEqual(os.getpgid(runner_pids[1]), runner_pids[1])
                process.send_signal(signal.SIGTERM)
            else:
                process.send_signal(signal.SIGTERM)
                time.sleep(0.02)
                process.kill()
            stdout, stderr = process.communicate(timeout=12)
            if mode == 'term_then_kill':
                end = time.monotonic() + 9
                while not (run / 'process.json').exists() and time.monotonic() < end: time.sleep(0.02)
                state = json.loads((run / 'process.json').read_text())
                self.assertTrue(state['runner_stopped'])
                self.assertFalse(any(live(pid) for pid in runner_pids))
                self.assertTrue(list((self.root / '.agent-dispatch-locks').glob('*.json')))
                return
            receipt = json.loads((run / 'receipt.json').read_text())
            self.assertEqual(receipt['result'], 'failed')
            supervisor_finished = not live(supervisor_pid)
            self.assertTrue(supervisor_finished)
            if mode == 'supervisor_killed':
                self.assertEqual(process.returncode, 2, (stdout, stderr))
                self.assertEqual(receipt['error_type'], 'Refusal')
                self.assertFalse(receipt['claim_release_safe'])
                self.assertTrue(any(live(pid) for pid in runner_pids))
            elif mode == 'escaped':
                self.assertFalse(receipt['process']['runner_stopped'])
                self.assertFalse(receipt['claim_release_safe'])
                self.assertTrue(live(runner_pids[1]))
                beat = self.checkout / 'src/child-beat'; before = beat.stat().st_size
                time.sleep(0.15)
                self.assertGreater(beat.stat().st_size, before)
            else:
                self.assertEqual(process.returncode, 1, (stdout, stderr))
                self.assertTrue(receipt['claim_release_safe'])
                self.assertEqual(receipt['process']['reason'], 'timeout')
                self.assertFalse(list((self.root / '.agent-dispatch-locks').glob('*.json')))
                return
            with self.assertRaises(dispatch.Refusal):
                with dispatch.scope_claim(self.root, 'example/service', ['src']): pass
        finally:
            if process.poll() is None: process.kill()
            process.communicate(timeout=5)
            if not runner_pids and pids.exists(): runner_pids = json.loads(pids.read_text())
            for pid in [*runner_pids, *([supervisor_pid] if supervisor_pid is not None and not supervisor_finished else [])]:
                try:
                    if not live(pid): continue
                    current = identity(pid)
                    self.assertIn(pid, identities, 'missing initial identity; runner self-expires, do not signal')
                    self.assertEqual(current, identities[pid], 'changed identity; do not signal')
                    self.assertEqual(os.getsid(pid), supervisor_pid if pid == supervisor_pid else runner_pids[0])
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass
            deadline = time.monotonic() + 5
            while any(live(pid) for pid in runner_pids) and time.monotonic() < deadline: time.sleep(0.02)
            self.assertFalse(any(live(pid) for pid in runner_pids), 'harness cleanup left a runner alive')

    def test_setpgid_child_retains_claim_while_writing(self):
        self.controlled_failure_journey('escaped')

    def test_sigstop_dispatcher_does_not_stop_supervisor_deadline(self):
        self.controlled_failure_journey('stopped_dispatcher')

    def test_supervisor_sigkill_retains_claim_without_cleanup_claim(self):
        self.controlled_failure_journey('supervisor_killed')

    def test_sigterm_followed_by_sigkill_retains_claim_and_cleans_runner(self):
        self.controlled_failure_journey('term_then_kill')

    def test_unconfirmed_group_cleanup_preserves_claim_and_failed_process_receipt(self):
        uncertain = dict(reason='completed', exit_code=0, captured_bytes=0, runner_stopped=False)
        with mock.patch.object(dispatch, 'bounded_process', return_value=uncertain):
            path, receipt = self.execute()
        self.assertEqual(receipt['result'], 'failed')
        self.assertEqual(receipt['process'], uncertain)
        self.assertFalse(receipt['claim_release_safe'])
        self.assertTrue(path.is_file())
        self.assertTrue(list((self.root / '.agent-dispatch-locks').glob('*.json')))
        with self.assertRaises(dispatch.Refusal):
            with dispatch.scope_claim(self.root, 'example/service', ['src']): pass

    def test_signal_handlers_restored_after_dispatch(self):
        before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        self.execute()
        self.assertEqual({s: signal.getsignal(s) for s in before}, before)

    def test_output_budget_caps_log_and_kills_runner(self):
        self.fake("import os, time\nos.write(1, b'x' * 65536)\ntime.sleep(30)\n")
        self.packet['max_output_bytes'] = 128
        self.refresh_execution()
        path, receipt = self.execute()
        self.assertEqual(receipt['process']['reason'], 'output_limit')
        self.assertEqual(path.with_name('output.log').stat().st_size, 128)
        self.assertEqual(receipt['result'], 'failed')

    def test_closed_stdout_does_not_escape_timebox(self):
        self.fake('import os, time\nos.close(1)\nos.close(2)\ntime.sleep(30)\n')
        self.packet['timebox_seconds'] = 1
        self.refresh_execution()
        _, receipt = self.execute()
        self.assertEqual(receipt['process']['reason'], 'timeout')

    def test_read_only_plan_does_not_execute_repository_fsmonitor(self):
        marker = self.root / 'fsmonitor-was-run'
        hook = self.root / 'fsmonitor'
        hook.write_text('#!' + sys.executable + '\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
        hook.chmod(0o700)
        self.git('config', 'core.fsmonitor', str(hook))
        self.plan()
        self.assertFalse(marker.exists())

    def test_status_disables_clean_and_process_filters_without_changing_config(self):
        marker = self.root / 'filter-was-run'
        hook = self.root / 'content-filter'
        hook.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\nPath(' +
                        repr(str(marker)) + ').touch()\nsys.stdout.write(sys.stdin.read())\n')
        hook.chmod(0o700)
        (self.checkout / '.gitattributes').write_text('src/one.py filter=review\n')
        self.git('add', '.gitattributes')
        self.git('commit', '-qm', 'attribute fixture')
        self.packet['base_sha'] = self.git('rev-parse', 'HEAD')
        self.refresh_execution()
        # Exercise effective included config as well as repository-local config.
        included = self.root / 'filters.gitconfig'
        included.write_text('[filter "review"]\n    required = true\n    clean = ' + str(hook) + '\n')
        self.git('config', 'include.path', str(included))
        for field in ('clean', 'process'):
            with self.subTest(field=field):
                self.git('config', 'filter.review.' + field, str(hook))
                source = self.checkout / 'src/one.py'
                source.write_text('original\n')
                os.utime(source, (time.time() + 2, time.time() + 2))
                self.plan()
                self.assertEqual(dispatch.changed_paths(self.checkout), [])
                self.assertFalse(marker.exists())
                self.assertEqual(self.git('config', '--get', 'filter.review.' + field), str(hook))

    def test_uninitialized_submodule_gitlink_refused_before_status(self):
        self.git('update-index', '--add', '--cacheinfo',
                 '160000,' + self.packet['base_sha'] + ',src/vendor')
        self.git('commit', '-qm', 'gitlink fixture')
        self.packet['base_sha'] = self.git('rev-parse', 'HEAD')
        self.refresh_execution()
        with self.assertRaisesRegex(dispatch.Refusal, 'submodule gitlinks'):
            self.plan()

    def test_initialized_submodule_filter_cannot_execute_during_plan(self):
        source = self.root / 'submodule-source'
        source.mkdir()

        def nested_git(directory, *args):
            process = subprocess.run(['git', '-C', str(directory), *args],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            return process.stdout.strip()

        nested_git(source, 'init', '-b', 'main')
        nested_git(source, 'config', 'user.name', 'Example Test')
        nested_git(source, 'config', 'user.email', 'test@example.invalid')
        nested_git(source, 'config', 'core.hooksPath', '/dev/null')
        (source / 'file.txt').write_text('original\n')
        (source / '.gitattributes').write_text('file.txt filter=review\n')
        nested_git(source, 'add', '.')
        nested_git(source, 'commit', '-qm', 'nested fixture')
        self.git('-c', 'protocol.file.allow=always', 'submodule', 'add', str(source), 'src/sub')
        self.git('commit', '-qm', 'submodule fixture')
        self.packet['base_sha'] = self.git('rev-parse', 'HEAD')
        self.refresh_execution()
        nested = self.checkout / 'src/sub'
        marker = self.root / 'nested-filter-was-run'
        hook = self.root / 'nested-filter'
        hook.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\nPath(' +
                        repr(str(marker)) + ').touch()\nsys.stdout.write(sys.stdin.read())\n')
        hook.chmod(0o700)
        nested_git(nested, 'config', 'filter.review.clean', str(hook))
        path = nested / 'file.txt'
        path.write_text('original\n')
        os.utime(path, (time.time() + 2, time.time() + 2))
        with self.assertRaisesRegex(dispatch.Refusal, 'submodule gitlinks'):
            self.plan()
        self.assertFalse(marker.exists())
        with self.assertRaisesRegex(dispatch.Refusal, 'submodule gitlinks'):
            dispatch.changed_paths(self.checkout)
        self.assertFalse(marker.exists())

    def test_unsupported_filter_names_fail_closed_before_status(self):
        self.git('config', 'filter.driver=unsafe.clean', 'false')
        with self.assertRaisesRegex(dispatch.Refusal, 'filter name'):
            self.plan()

    def test_nonzero_cli_exit_is_failure(self):
        self.fake('raise SystemExit(4)\n')
        _, receipt = self.execute()
        self.assertEqual(receipt['process']['exit_code'], 4)
        self.assertEqual(receipt['result'], 'failed')

    def test_cli_defaults_to_plan_and_never_executes_without_flag(self):
        packet = self.root / 'packet.json'
        packet.write_text(json.dumps(self.packet))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = dispatch.main(['--packet', str(packet), '--trusted-root', str(self.root),
                '--checkout', str(self.checkout), '--tracker-repository', 'example/tasks',
                '--allow-repository', 'example/service'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['schema'], 'agent-dispatch-plan/v1')
        self.assertFalse((self.root / '.agent-dispatch-runs').exists())


class SupervisorFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        self.addCleanup(lambda: [signal.signal(sig, handler) for sig, handler in self.handlers.items()])

    def run_spec(self, name):
        return dict(argv=[sys.executable, '-c', 'import time; time.sleep(20)'],
                    checkout=str(self.root), seconds=1, output_limit=128, output_path=str(self.root / name / 'output.log'))

    def test_all_uncertain_launch_exceptions_retain_claim(self):
        for error in (RuntimeError('uncertain post-fork failure'),
                      FileNotFoundError('failed exec not positively confirmed reaped'),
                      OSError(errno.EIO, 'exec-status pipe read failed')):
            with self.subTest(error=type(error).__name__):
                spec = self.run_spec(type(error).__name__)
                Path(spec['output_path']).parent.mkdir()
                read_fd, write_fd = os.pipe()
                try:
                    with mock.patch.object(supervisor.subprocess, 'Popen', side_effect=error):
                        result = supervisor.supervise(spec, read_fd)
                    self.assertFalse(result['runner_stopped'])
                    self.assertTrue(result['launch_uncertain'])
                    self.assertEqual(result['error_type'], type(error).__name__)
                    self.assertEqual(json.loads(Path(spec['output_path']).with_name('process.json').read_text()), result)
                finally:
                    with contextlib.suppress(OSError): os.close(read_fd)
                    os.close(write_fd)

    def test_close_failures_cannot_skip_runner_kill_or_receipt(self):
        real_popen, real_close = subprocess.Popen, os.close
        for failing in ('selector', 'descriptor'):
            with self.subTest(failing=failing):
                spec = self.run_spec(failing); Path(spec['output_path']).parent.mkdir()
                read_fd, write_fd = os.pipe(); children = []
                selector = mock.Mock()
                selector.register.side_effect = RuntimeError('registration failed')
                if failing == 'selector': selector.close.side_effect = OSError('selector close failed')
                def launch(*args, **kwargs):
                    child = real_popen(*args, **kwargs); children.append(child); return child
                def close(fd):
                    real_close(fd)
                    if failing == 'descriptor' and fd == read_fd: raise OSError('descriptor close failed')
                with tempfile.TemporaryFile() as input_file:
                    try:
                        with mock.patch.object(supervisor.selectors, 'DefaultSelector', return_value=selector), \
                             mock.patch.object(supervisor.subprocess, 'Popen', side_effect=launch), \
                             mock.patch.object(supervisor.os, 'close', side_effect=close), \
                             mock.patch.object(supervisor.sys, 'stdin', mock.Mock(buffer=input_file)):
                            result = supervisor.supervise(spec, read_fd)
                        self.assertTrue(result['runner_stopped'])
                        self.assertEqual(result['error_type'], 'RuntimeError')
                        self.assertTrue(all(child.poll() is not None for child in children))
                        self.assertEqual(json.loads(Path(spec['output_path']).with_name('process.json').read_text()), result)
                    finally:
                        for child in children:
                            if child.poll() is None: child.kill()
                            child.wait(timeout=5)
                            child.stdout.close()
                        with contextlib.suppress(OSError): real_close(read_fd)
                        real_close(write_fd)

    def test_session_inspection_fails_closed_on_unknown_or_live_member(self):
        uid = os.getuid()
        snapshot = (321, 0, f'123 456 {uid} S\n321 321 {uid} R\n')
        with mock.patch.object(supervisor, 'process_snapshot', return_value=snapshot):
            for observed in (PermissionError('denied'), ProcessLookupError(), 999):
                with self.subTest(observed=observed), mock.patch.object(supervisor.os, 'getsid',
                        side_effect=observed if isinstance(observed, Exception) else None,
                        return_value=observed if isinstance(observed, int) else None):
                    self.assertFalse(supervisor.group_stopped(999))
            with mock.patch.object(supervisor.os, 'getsid', return_value=456) as getsid:
                self.assertTrue(supervisor.group_stopped(999))
                getsid.assert_called_once_with(123)  # Ignore only exact observer PID.
        for output in ('', 'malformed row\n', '-1 0 -2 S\n'):
            with self.subTest(output=output), mock.patch.object(supervisor, 'process_snapshot',
                    return_value=(321, 0, output)):
                self.assertFalse(supervisor.group_stopped(999))
        for failure in (OSError('ps unavailable'), subprocess.TimeoutExpired('ps', 2)):
            with self.subTest(failure=failure), mock.patch.object(supervisor, 'process_snapshot', side_effect=failure):
                self.assertFalse(supervisor.group_stopped(999))

    def test_disappearing_row_invalidates_snapshot_until_fresh_observation(self):
        uid = os.getuid()
        # Parent disappears after spawning an unseen child; that first scan
        # must not release. A later complete scan observes the child session.
        snapshots = [(321, 0, f'123 456 {uid} S\n321 321 {uid} R\n'),
                     (322, 0, f'124 456 {uid} S\n322 322 {uid} R\n'),
                     (323, 0, f'323 323 {uid} R\n')]
        with mock.patch.object(supervisor, 'process_snapshot', side_effect=snapshots), \
             mock.patch.object(supervisor.os, 'getsid', side_effect=[ProcessLookupError(), 999]):
            self.assertFalse(supervisor.group_stopped(999))
            self.assertFalse(supervisor.group_stopped(999))
            self.assertTrue(supervisor.group_stopped(999))


if __name__ == '__main__':
    unittest.main()
