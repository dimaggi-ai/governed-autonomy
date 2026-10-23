#!/usr/bin/env python3
"""Plan or explicitly run one scoped agent job, never a daemon or task authority.

The caller supplies an authenticated, current tracker snapshot and trusted repository
allowlist. This validates consistency, not author identity or permission to spend.
Execution keeps installed CLI safeguards; path scope is checked before/after, not
an OS-level per-file sandbox. Tool policy must still enforce forbidden actions.
Locks coordinate cooperating processes on one host/root only. Use existing CI
concurrency and tracker ownership for cross-host coordination. A crash leaves its
claim for manual investigation: this module never takes over stale claims.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import sys
import subprocess
import tempfile
import time
from urllib.parse import urlparse
import uuid
import unicodedata

SCHEMA = 'agent-work-packet/v1'
FIELDS = {'schema', 'issue_url', 'execution_block', 'tool', 'repository', 'base_sha',
          'branch', 'paths', 'reviewer', 'timebox_seconds', 'max_output_bytes', 'task'}
TOOLS = {'codex', 'claude-code'}
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')


class Refusal(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def load_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_object)


def lexical_scope(value):
    """Validate a relative Git path while preserving case and Unicode spelling."""
    require(isinstance(value, str) and 0 < len(value) <= 512, 'invalid scope')
    require(not any(ord(c) < 32 for c in value) and '\\' not in value and ':' not in value,
            'invalid scope characters')
    p = PurePosixPath(value)
    require(not p.is_absolute() and '..' not in p.parts, 'scope traversal')
    require(not p.parts or p.parts[0].casefold() != '.git', 'Git metadata is not a work scope')
    return p.as_posix()


def canonical_scope(value):
    """Conservative alias key for conflicts, never write authorization."""
    return unicodedata.normalize('NFC', lexical_scope(value)).casefold()


def overlaps(left, right):
    return any(a == '.' or b == '.' or a == b or a.startswith(b + '/') or
               b.startswith(a + '/') for a in left for b in right)


def within_scope(path, scopes):
    """Write containment is directional; owning a child never owns its parent."""
    return any(scope == '.' or path == scope or path.startswith(scope + '/') for scope in scopes)


def validate_packet(packet, allow_repositories, tracker_repository):
    require(isinstance(packet, dict) and set(packet) == FIELDS, 'unknown or missing packet fields')
    require(packet['schema'] == SCHEMA, 'unknown packet schema')
    require(isinstance(tracker_repository, str) and REPO.fullmatch(tracker_repository),
            'invalid tracker repository')
    require(isinstance(packet['repository'], str) and REPO.fullmatch(packet['repository']),
            'invalid repository')
    require(packet['repository'] in allow_repositories, 'repository not allowed')
    require(isinstance(packet['tool'], str) and isinstance(packet['reviewer'], str) and
            packet['tool'] in TOOLS and packet['reviewer'] in TOOLS and
            packet['tool'] != packet['reviewer'], 'reviewer must be the other tool')
    require(isinstance(packet['base_sha'], str) and SHA.fullmatch(packet['base_sha']),
            'base must be exact lowercase commit SHA')
    require(isinstance(packet['branch'], str) and
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,199}', packet['branch']) and
            '..' not in packet['branch'] and not packet['branch'].endswith('/'), 'invalid branch')
    require(isinstance(packet['paths'], list) and 0 < len(packet['paths']) <= 64, 'invalid paths')
    scopes = sorted(set(canonical_scope(p) for p in packet['paths']))
    require(len(scopes) == len(packet['paths']), 'duplicate path aliases')
    require(type(packet['timebox_seconds']) is int and 1 <= packet['timebox_seconds'] <= 14400,
            'timebox must be 1..14400 seconds')
    require(type(packet['max_output_bytes']) is int and 1 <= packet['max_output_bytes'] <= 16777216,
            'output budget must be 1..16777216 bytes')
    require(isinstance(packet['task'], str) and 0 < len(packet['task'].encode()) <= 65536 and
            '\x00' not in packet['task'], 'invalid task prompt')
    require(isinstance(packet['issue_url'], str) and re.fullmatch(
        r'https://github\.com/' + re.escape(tracker_repository) + r'/issues/[1-9][0-9]*',
        packet['issue_url']), 'wrong tracker issue')
    require(isinstance(packet['execution_block'], str) and len(packet['execution_block']) <= 8192,
            'invalid Execution block')
    lines = packet['execution_block'].strip().splitlines()
    require(lines and lines[0] == 'Execution:', 'missing Execution heading')
    actual = {}
    for line in lines[1:]:
        key, separator, value = line.partition(':')
        require(separator and key not in actual, 'malformed or duplicate Execution field')
        actual[key] = value.strip()
    expected = {'Implementer': packet['tool'], 'Repository': packet['repository'],
                'Base SHA': packet['base_sha'], 'Branch': packet['branch'],
                'Owned paths': json.dumps(packet['paths'], separators=(',', ':')),
                'Cross-reviewer': packet['reviewer'],
                'Timebox seconds': str(packet['timebox_seconds']),
                'Output budget bytes': str(packet['max_output_bytes'])}
    # Human context remains verbatim in the receipt; it is never dispatch authority.
    contextual_fields = {'Model', 'Session', 'Last checked', 'Handoff'}
    require(actual.get('Owned paths') == expected['Owned paths'],
            'Execution Owned paths must match packet JSON order and spelling exactly')
    require(set(actual) <= set(expected) | contextual_fields and
            {key: value for key, value in actual.items() if key not in contextual_fields} == expected,
            'Execution block does not match snapshot')
    return scopes


def git_command(checkout, *args):
    base = ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
            '-c', 'core.hooksPath=/dev/null', '-C', str(checkout)]
    if args and args[0] == 'status':
        # Root status can recurse into initialized submodules and execute their
        # separately configured filters. Inspect the index without touching file
        # content, and refuse every gitlink (initialized or not) before status.
        staged = subprocess.run(base + ['ls-files', '--stage', '-z'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        require(staged.returncode == 0, 'cannot inspect index for submodules')
        require(not any(entry.startswith(b'160000 ') for entry in staged.stdout.split(b'\x00')),
                'submodule gitlinks are unsupported for dispatch; use a standalone checkout')
        # Status may execute clean/process filters while refreshing tracked files.
        # Config inspection itself invokes no filters. Include effective system,
        # global, local and conditional config, then override only for this read.
        inspected = subprocess.run(base + ['config', '--includes', '--null', '--name-only',
            '--get-regexp', r'^filter\..*\.(clean|process)$'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        require(inspected.returncode in (0, 1), 'cannot inspect content filter configuration')
        disabled = set()
        for key in inspected.stdout.decode().split('\x00'):
            if not key:
                continue
            match = re.fullmatch(r'filter\.([A-Za-z0-9_.-]+)\.(clean|process)', key)
            require(match is not None, 'unsupported content filter name; cannot safely disable')
            disabled.add(match[1])
        for driver in sorted(disabled):
            base.extend(['-c', f'filter.{driver}.clean=', '-c', f'filter.{driver}.process=',
                         '-c', f'filter.{driver}.required=false'])
    return base + list(args)


def git(checkout, *args):
    result = subprocess.run(git_command(checkout, *args),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    require(result.returncode == 0, 'checkout verification failed: ' + ' '.join(args[:2]))
    return result.stdout.decode().strip()


def repository_identity(remote):
    match = re.fullmatch(r'git@github\.com:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?', remote)
    if match:
        return match.group(1)
    parsed = urlparse(remote)
    require(parsed.scheme == 'https' and parsed.netloc == 'github.com' and
            not parsed.query and not parsed.fragment, 'unsupported origin URL')
    identity = parsed.path.lstrip('/')
    if identity.endswith('.git'):
        identity = identity[:-4]
    require(REPO.fullmatch(identity), 'invalid origin identity')
    return identity


def check_checkout(packet, root, checkout):
    root = Path(root).resolve(strict=True)
    requested = Path(checkout).absolute()
    resolved = requested.resolve(strict=True)
    require(resolved != root and root in resolved.parents, 'checkout outside trusted root')
    # Reject symlink traversal even if it lands inside the trusted root.
    require(requested == resolved, 'checkout path must be canonical, without symlinks')
    require(Path(git(resolved, 'rev-parse', '--show-toplevel')).resolve() == resolved,
            'checkout must be repository root')
    require(repository_identity(git(resolved, 'remote', 'get-url', 'origin')) == packet['repository'],
            'checkout origin does not match packet')
    require(git(resolved, 'rev-parse', 'HEAD') == packet['base_sha'], 'checkout base changed')
    require(git(resolved, 'branch', '--show-current') == packet['branch'], 'checkout branch changed')
    require(not git(resolved, 'status', '--porcelain', '--untracked-files=all'), 'checkout is not clean')
    for scope in packet['paths']:
        part = resolved
        for component in PurePosixPath(scope).parts:
            part /= component
            require(not part.is_symlink(), 'scope contains a symlink')
        require(part.resolve() == resolved or resolved in part.resolve().parents,
                'scope escapes checkout')
        # A directory scope must not hide a descendant link escaping the checkout.
        if part.is_dir():
            visited = 0
            for base, directories, files in os.walk(part, followlinks=False):
                if Path(base) == resolved and '.git' in directories:
                    directories.remove('.git')
                for name in directories + files:
                    visited += 1
                    require(visited <= 100000, 'scope scan too large; narrow the owned paths')
                    require(not (Path(base) / name).is_symlink(), 'scope contains a descendant symlink')
    return root, resolved


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, exist_ok=True)
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and st.st_mode & 0o077 == 0,
            'state directory must be private and not a symlink')
    return path


@contextmanager
def scope_claim(root, repository, scopes, cleanup_state=None):
    """An atomic scoped claim, no PID/age-based takeover and no persistent task ledger."""
    root = Path(root).resolve(strict=True)
    scopes = sorted(set(canonical_scope(s) for s in scopes))
    require(scopes, 'empty claim scope')
    directory = private_directory(root / '.agent-dispatch-locks')
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    fd = os.open(directory / 'guard.lock', flags, 0o600)
    st = os.fstat(fd)
    if not (stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == os.getuid()
            and st.st_mode & 0o077 == 0):
        os.close(fd)
        raise Refusal('invalid guard lock')
    claim = directory / (uuid.uuid4().hex + '.json')
    record = {'schema': 'agent-scope-claim/v1', 'repository': repository,
              'scopes': scopes, 'pid': os.getpid()}
    created = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            for p in directory.glob('*.json'):
                require(not p.is_symlink() and p.is_file(), 'invalid existing claim')
                prior = load_json(p)
                require(isinstance(prior, dict) and set(prior) == set(record) and
                        prior['schema'] == record['schema'] and
                        isinstance(prior['repository'], str) and
                        isinstance(prior['scopes'], list) and prior['scopes'], 'malformed existing claim')
                normalized = [canonical_scope(s) for s in prior['scopes']]
                require(prior['repository'].casefold() != repository.casefold() or
                        not overlaps(scopes, normalized), 'overlapping active claim; reconcile manually')
            with claim.open('x') as f:
                os.chmod(claim, 0o600)
                json.dump(record, f)
                f.flush()
                os.fsync(f.fileno())
            created = True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
        yield claim.name
    finally:
        if created and (cleanup_state is None or cleanup_state.get("safe", False)):
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                claim.unlink()  # Only our own claim; never remove another process's record.
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def invocation(tool, executable):
    if tool == 'codex':
        return [str(executable), 'exec', '--sandbox', 'workspace-write', '--json', '-']
    return [str(executable), '-p', '--permission-mode', 'manual',
            '--permission-prompts', 'none', '--output-format', 'json']


def plan(packet, allow_repositories, tracker_repository, root, checkout):
    scopes = validate_packet(packet, allow_repositories, tracker_repository)
    root, checkout = check_checkout(packet, root, checkout)
    executable = shutil.which('codex' if packet['tool'] == 'codex' else 'claude')
    require(executable, 'requested CLI is not installed')
    binary = Path(executable).resolve(strict=True)
    require(binary.is_file() and os.access(binary, os.X_OK), 'CLI is not an executable file')
    require(checkout not in binary.parents and binary != checkout, 'CLI cannot come from checkout')
    return {'schema': 'agent-dispatch-plan/v1', 'repository': packet['repository'],
            'issue_url': packet['issue_url'], 'base_sha': packet['base_sha'],
            'checkout': str(checkout), 'trusted_root': str(root), 'scopes': scopes,
            'argv': invocation(packet['tool'], binary), 'prompt_transport': 'stdin',
            'timebox_seconds': packet['timebox_seconds'], 'max_output_bytes': packet['max_output_bytes'],
            'cost_or_token_limit': 'not enforced; time and captured-output limits only',
            'scope_enforcement': 'advisory prompt and Git-visible post-run detection; not per-file OS sandbox',
            'scope_limitations': 'ignored files, external effects and transient changes require external tool policy',
            'execution_block_sha256': hashlib.sha256(packet['execution_block'].encode()).hexdigest()}


@contextmanager
def interruption_guard():
    """Convert catchable process interruptions into ordinary failed receipts."""
    state = {'signal': None}
    def observed(signum, _frame):
        if state['signal'] is None:
            state['signal'] = signal.Signals(signum).name
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, observed)
        yield state
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def bounded_process(argv, prompt, checkout, seconds, output_limit, output_path, interruptions):
    """An independent per-job supervisor owns the deadline and runner cleanup."""
    if interruptions['signal']:
        return {'reason': 'interrupted_' + interruptions['signal'], 'exit_code': None,
                'captured_bytes': 0, 'runner_stopped': True}
    read_fd, write_fd = os.pipe()
    process = None
    try:
        with tempfile.TemporaryFile(dir=output_path.parent) as source:
            source.write(prompt.encode()); source.seek(0)
            spec = dict(argv=argv, checkout=str(checkout), seconds=seconds,
                        output_limit=output_limit, output_path=str(output_path))
            supervisor = Path(__file__).with_name('agent_supervisor.py')
            process = subprocess.Popen([sys.executable, str(supervisor), json.dumps(spec), str(read_fd)],
                stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True, pass_fds=(read_fd,), shell=False)
            os.close(read_fd); read_fd = None
            # The write descriptor is non-inheritable and never passed to either
            # child. EOF therefore reports parent death independently of PIDs.
            stop_sent = False
            while process.poll() is None:
                if interruptions['signal'] and not stop_sent:
                    try:
                        os.write(write_fd, interruptions['signal'].encode('ascii'))
                    except BrokenPipeError:
                        pass
                    stop_sent = True
                time.sleep(0.02)
            result_path = output_path.with_name('process.json')
            require(result_path.is_file(), 'supervisor exited without cleanup evidence; retain claim')
            result = load_json(result_path)
            if interruptions['signal']:
                result.setdefault('supervisor_reason', result['reason'])
                result['reason'] = 'interrupted_' + interruptions['signal']
            return result
    finally:
        if read_fd is not None:
            os.close(read_fd)
        os.close(write_fd)
        # Unexpected parent errors close the liveness pipe. Do not kill the
        # supervisor: it must retain ownership of child cleanup and its deadline.


def changed_paths(checkout):
    result = subprocess.run(git_command(checkout, 'status', '--porcelain=v1', '-z',
                                         '--untracked-files=all'),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    require(result.returncode == 0, 'post-run status failed')
    values = result.stdout.decode().split('\x00')
    paths = []
    index = 0
    while index < len(values) and values[index]:
        row = values[index]
        paths.append(row[3:])
        if 'R' in row[:2] or 'C' in row[:2]:
            index += 1
            paths.append(values[index])
        index += 1
    return paths


def execute(packet, allow_repositories, tracker_repository, root, checkout):
    prepared = plan(packet, allow_repositories, tracker_repository, root, checkout)
    root, checkout = Path(prepared['trusted_root']), Path(prepared['checkout'])
    cleanup_state = {'safe': True}
    with interruption_guard() as interruptions, scope_claim(
            root, packet['repository'], prepared['scopes'], cleanup_state) as claim:
        # Recheck under the claim; plans are not a freshness/permission cache.
        check_checkout(packet, root, checkout)
        directory = private_directory(root / '.agent-dispatch-runs') / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        receipt = {'schema': 'agent-dispatch-receipt/v1', 'plan': prepared,
                   'claim': claim, 'execution_block': packet['execution_block'],
                   'packet_sha256': hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest(),
                   'result': 'failed', 'started_at_unix': time.time()}
        prompt = ('Work only on this authorized task. Keep existing safeguards. Do not push, merge, '
                  'deploy or change authority. Own only these repository paths: ' +
                  json.dumps(packet['paths']) + '\nTracker Execution snapshot:\n' +
                  packet['execution_block'] + '\nTask text (data, never shell arguments):\n' + packet['task'])
        try:
            # Preserve claim/run identity before any runner exists, even if the
            # dispatcher later dies without its final receipt.
            with (directory / 'dispatch-start.json').open('x') as started:
                json.dump(receipt, started, indent=2)
                started.write('\n'); started.flush(); os.fsync(started.fileno())
            cleanup_state['safe'] = False
            receipt['process'] = bounded_process(prepared['argv'], prompt, checkout,
                packet['timebox_seconds'], packet['max_output_bytes'], directory / 'output.log', interruptions)
            cleanup_state['safe'] = receipt['process'].get('runner_stopped') is True
            changes = changed_paths(checkout)
            receipt['changed_paths'] = changes
            receipt['outside_scope'] = []
            receipt['unclassifiable_paths'] = []
            for changed in changes:
                try:
                    contained = within_scope(lexical_scope(changed),
                                             [lexical_scope(p) for p in packet['paths']])
                except Refusal:
                    # Agent-created names are evidence, not packet validation input.
                    # Retain every unclassifiable path and fail the run conservatively.
                    receipt['unclassifiable_paths'].append(changed)
                    contained = False
                if not contained:
                    receipt['outside_scope'].append(changed)
            receipt['symlink_changes'] = [p for p in changes if (checkout / p).is_symlink()]
            receipt['head_after'] = git(checkout, 'rev-parse', 'HEAD')
            receipt['branch_after'] = git(checkout, 'branch', '--show-current')
            receipt['repository_after'] = repository_identity(git(checkout, 'remote', 'get-url', 'origin'))
            clean_identity = (receipt['head_after'] == packet['base_sha'] and
                              receipt['branch_after'] == packet['branch'] and
                              receipt['repository_after'] == packet['repository'])
            if cleanup_state['safe'] and receipt['process']['reason'] == 'completed' and receipt['process']['exit_code'] == 0 and \
                    not receipt['outside_scope'] and not receipt['symlink_changes'] and clean_identity:
                receipt['result'] = 'completed_pending_review'
        except BaseException as error:
            receipt['error_type'] = type(error).__name__
            raise
        finally:
            if interruptions['signal']:
                receipt['result'] = 'failed'
                receipt['interruption'] = interruptions['signal']
                if 'process' in receipt:
                    receipt['process'].setdefault('supervisor_reason', receipt['process']['reason'])
                    receipt['process']['reason'] = 'interrupted_' + interruptions['signal']
            receipt['claim_release_safe'] = cleanup_state['safe']
            receipt['finished_at_unix'] = time.time()
            output = directory / 'output.log'
            if output.exists():
                receipt['output_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
            (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        return directory / 'receipt.json', receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', required=True)
    parser.add_argument('--trusted-root', required=True)
    parser.add_argument('--checkout', required=True)
    parser.add_argument('--tracker-repository', required=True)
    parser.add_argument('--allow-repository', action='append', required=True)
    parser.add_argument('--execute', action='store_true', help='Explicitly run; default is a read-only plan')
    args = parser.parse_args(argv)
    try:
        packet = load_json(args.packet)
        values = (packet, args.allow_repository, args.tracker_repository, args.trusted_root, args.checkout)
        if args.execute:
            path, receipt = execute(*values)
            print(json.dumps({'receipt': str(path), 'result': receipt['result']}))
            return 0 if receipt['result'] == 'completed_pending_review' else 1
        print(json.dumps(plan(*values), indent=2))
        return 0
    except (Refusal, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
