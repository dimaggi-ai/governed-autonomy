#!/usr/bin/env python3
"""One-job POSIX lifetime supervisor; no task authority or persistent service."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time


def process_snapshot():
    """Return the exact observer PID with its bounded process-table snapshot."""
    observer = subprocess.Popen(['ps', '-A', '-o', 'pid=,pgid=,uid=,stat='],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        output, _ = observer.communicate(timeout=2)
        return observer.pid, observer.returncode, output
    finally:
        if observer.poll() is None:
            observer.kill()  # Only this known direct observation child.
            observer.wait(timeout=2)
        observer.stdout.close()
        observer.stderr.close()


def group_stopped(pgid):
    """Observe the original group and same-UID runner session; unknown is unsafe.

    This never signals other groups. A child that creates another session is
    outside this observation boundary and needs external reconciliation.
    """
    try:
        observer_pid, returncode, output = process_snapshot()
        if returncode or not output.strip():
            return False
        for line in output.splitlines():
            columns = line.split()
            if len(columns) != 4:
                return False
            pid, group, uid = map(int, columns[:3])
            if pid <= 0 or group <= 0 or uid < 0:
                return False
            if pid == observer_pid:
                continue  # Only this exact launched observer may be omitted.
            if columns[3].startswith('Z'):
                continue  # Zombies cannot write or spawn further children.
            if group == pgid:
                return False
            if uid == os.getuid():
                try:
                    if os.getsid(pid) == pgid:
                        return False
                except ProcessLookupError:
                    # It may have forked after the snapshot and then exited.
                    # Do not infer its unobserved children are absent; retry.
                    return False
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def supervise(spec, parent_fd):
    interrupted = [None]
    def on_signal(signum, _frame):
        interrupted[0] = 'interrupted_' + signal.Signals(signum).name
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)
    start = time.monotonic()
    deadline = start + spec['seconds']
    result = {'reason': 'supervisor_error', 'exit_code': None, 'captured_bytes': 0,
              'supervisor_pid': os.getpid(), 'runner_stopped': False,
              'started_at_unix': time.time(), 'timebox_seconds': spec['seconds']}
    process = None
    spawning = False
    selector = None
    try:
        selector = selectors.DefaultSelector()
        with Path(spec['output_path']).open('xb') as output:
            # close_fds prevents all control descriptors reaching the runner or
            # its descendants. Only supervisor holds parent_fd; parent alone
            # holds the write end, so dispatcher death reliably produces EOF.
            spawning = True
            process = subprocess.Popen(spec['argv'], cwd=spec['checkout'], stdin=sys.stdin.buffer,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True, close_fds=True, shell=False)
            spawning = False
            result.update(runner_pid=process.pid, process_group=process.pid,
                          runner_session=process.pid, runner_uid=os.getuid())
            started = dict(result, reason='running')
            (Path(spec['output_path']).parent / 'supervisor-start.json').write_text(json.dumps(started) + '\n')
            selector.register(parent_fd, selectors.EVENT_READ, 'parent')
            selector.register(process.stdout, selectors.EVENT_READ, 'output')
            while True:
                if interrupted[0]:
                    result['reason'] = interrupted[0]; break
                if time.monotonic() >= deadline:
                    result['reason'] = 'timeout'; break
                if process.poll() is not None and not any(k.data == 'output' for k in selector.get_map().values()):
                    result['reason'] = 'completed'; break
                events = selector.select(min(0.05, max(0, deadline - time.monotonic())))
                for key, _ in events:
                    if key.data == 'parent':
                        message = os.read(parent_fd, 128)
                        name = message.decode('ascii', errors='replace')
                        result['reason'] = ('interrupted_' + name if name in ('SIGINT', 'SIGTERM', 'SIGHUP')
                                            else 'dispatcher_lost')
                        return result
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        selector.unregister(process.stdout)
                        continue
                    remaining = spec['output_limit'] - result['captured_bytes']
                    output.write(chunk[:remaining])
                    result['captured_bytes'] += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        result['reason'] = 'output_limit'
                        return result
    except BaseException as error:
        result['error_type'] = type(error).__name__
    finally:
        def cleanup(operation, action):
            try:
                action()
            except Exception as error:
                result.setdefault('cleanup_errors', []).append(
                    {'operation': operation, 'error_type': type(error).__name__})

        if process is not None:
            # Terminate first: selector/descriptor failures must not skip this.
            def kill_original_group():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            cleanup('kill_original_group', kill_original_group)
            cleanup('wait_runner', lambda: process.wait(timeout=5))
            result['exit_code'] = process.returncode
            end = time.monotonic() + 5
            while time.monotonic() < end:
                if group_stopped(process.pid):
                    result['runner_stopped'] = True
                    break
                time.sleep(0.05)
            cleanup('runner_stdout_close', process.stdout.close)
        else:
            # Any exception during Popen can leave an unassigned live child.
            # Exception class alone is not proof that a failed exec was reaped.
            result['runner_stopped'] = not spawning
            if spawning:
                result['launch_uncertain'] = True
        if selector is not None:
            cleanup('selector_close', selector.close)
        cleanup('parent_fd_close', lambda: os.close(parent_fd))
        result['finished_at_unix'] = time.time()
        path = Path(spec['output_path']).with_name('process.json')
        temporary = path.with_suffix('.tmp')
        with temporary.open('x') as receipt:
            json.dump(result, receipt, indent=2)
            receipt.write('\n'); receipt.flush(); os.fsync(receipt.fileno())
        os.replace(temporary, path)
    return result


if __name__ == '__main__':
    supervise(json.loads(sys.argv[1]), int(sys.argv[2]))
