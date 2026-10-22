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


def group_stopped(pgid):
    """Confirm no runnable/sleeping member remains; zombies cannot do work."""
    state = subprocess.run(['ps', '-A', '-o', 'pid=,pgid=,stat='],
                           capture_output=True, text=True, timeout=2, check=False)
    if state.returncode:
        return False
    for line in state.stdout.splitlines():
        columns = line.split()
        if len(columns) >= 3 and columns[1] == str(pgid) and not columns[2].startswith('Z'):
            return False
    return True


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
    selector = selectors.DefaultSelector()
    try:
        with Path(spec['output_path']).open('xb') as output:
            # close_fds prevents all control descriptors reaching the runner or
            # its descendants. Only supervisor holds parent_fd; parent alone
            # holds the write end, so dispatcher death reliably produces EOF.
            process = subprocess.Popen(spec['argv'], cwd=spec['checkout'], stdin=sys.stdin.buffer,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True, close_fds=True, shell=False)
            result.update(runner_pid=process.pid, process_group=process.pid)
            (Path(spec['output_path']).parent / 'supervisor-start.json').write_text(json.dumps(result) + '\n')
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
        selector.close()
        os.close(parent_fd)
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
                result['exit_code'] = process.returncode
                # Do not declare cleanup merely because the group leader exited.
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    if group_stopped(process.pid):
                        result['runner_stopped'] = True
                        break
                    time.sleep(0.05)
            except (OSError, subprocess.SubprocessError) as error:
                result['cleanup_error_type'] = type(error).__name__
            process.stdout.close()
        else:
            result['runner_stopped'] = True
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
