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
import selectors
import shutil
import signal
import stat
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


def canonical_scope(value):
    require(isinstance(value, str) and 0 < len(value) <= 512, 'invalid scope')
    require(not any(ord(c) < 32 for c in value) and '\\' not in value and ':' not in value,
            'invalid scope characters')
    p = PurePosixPath(value)
    require(not p.is_absolute() and '..' not in p.parts, 'scope traversal')
    require(not p.parts or p.parts[0].casefold() != '.git', 'Git metadata is not a work scope')
    return unicodedata.normalize('NFC', p.as_posix()).casefold()


def overlaps(left, right):
    return any(a == '.' or b == '.' or a == b or a.startswith(b + '/') or
               b.startswith(a + '/') for a in left for b in right)


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
    require(set(actual) <= set(expected) | contextual_fields and
            {key: value for key, value in actual.items() if key not in contextual_fields} == expected,
            'Execution block does not match snapshot')
    return scopes


def git(checkout, *args):
    result = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                             '-c', 'core.hooksPath=/dev/null', '-C', str(checkout), *args],
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
def scope_claim(root, repository, scopes):
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
        if created:
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


def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def bounded_process(argv, prompt, checkout, seconds, output_limit, output_path):
    """No shell, bounded log and wall time, process-group termination on every exit."""
    with tempfile.TemporaryFile() as source, output_path.open('xb') as output:
        source.write(prompt.encode())
        source.seek(0)
        process = subprocess.Popen(argv, cwd=checkout, stdin=source, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True, shell=False)
        deadline = time.monotonic() + seconds
        total = 0
        reason = 'completed'
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                if time.monotonic() >= deadline:
                    reason = 'timeout'
                    break
                events = selector.select(min(0.1, max(0, deadline - time.monotonic())))
                if not events:
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    try:
                        process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        reason = 'timeout'
                    break
                remaining = output_limit - total
                output.write(chunk[:remaining])
                total += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    reason = 'output_limit'
                    break
        finally:
            selector.close()
            kill_group(process)
            process.wait(timeout=5)
            process.stdout.close()
        return {'reason': reason, 'exit_code': process.returncode, 'captured_bytes': total}


def changed_paths(checkout):
    result = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                             '-c', 'core.hooksPath=/dev/null', '-C', str(checkout),
                             'status', '--porcelain=v1', '-z', '--untracked-files=all'],
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
    with scope_claim(root, packet['repository'], prepared['scopes']) as claim:
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
            receipt['process'] = bounded_process(prepared['argv'], prompt, checkout,
                packet['timebox_seconds'], packet['max_output_bytes'], directory / 'output.log')
            changes = changed_paths(checkout)
            receipt['changed_paths'] = changes
            receipt['outside_scope'] = [p for p in changes if not overlaps(
                [canonical_scope(p)], prepared['scopes'])]
            receipt['symlink_changes'] = [p for p in changes if (checkout / p).is_symlink()]
            receipt['head_after'] = git(checkout, 'rev-parse', 'HEAD')
            receipt['branch_after'] = git(checkout, 'branch', '--show-current')
            receipt['repository_after'] = repository_identity(git(checkout, 'remote', 'get-url', 'origin'))
            clean_identity = (receipt['head_after'] == packet['base_sha'] and
                              receipt['branch_after'] == packet['branch'] and
                              receipt['repository_after'] == packet['repository'])
            if receipt['process']['reason'] == 'completed' and receipt['process']['exit_code'] == 0 and \
                    not receipt['outside_scope'] and not receipt['symlink_changes'] and clean_identity:
                receipt['result'] = 'completed_pending_review'
        except Exception as error:
            receipt['error_type'] = type(error).__name__
            raise
        finally:
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
