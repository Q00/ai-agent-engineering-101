#!/usr/bin/env python3
"""Supervise only the deployed seller and named tunnel until the fixed guard fires."""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import time

DEADLINE = dt.datetime.fromisoformat('2026-10-21T09:00:00+09:00').timestamp()
CALL_LIMIT = 5000
USAGE_PREFIX = b'MODEL_USAGE model='


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_baseline(data: Path, supplied: Path) -> int:
    value = json.loads(supplied.read_text())['model_usage_count']
    if type(value) is not int or value < 0:
        raise ValueError('baseline must be a nonnegative integer')
    stored = data / 'baseline.json'
    if stored.exists():
        if json.loads(stored.read_text()) != {'model_usage_count': value}:
            raise ValueError('baseline differs from the immutable cutover baseline')
    else:
        # A lost baseline must never reinitialize a previously running deployment.
        if (data / 'seller.log').exists() or (data / 'guard-state.json').exists():
            raise ValueError('existing deployment has lost its baseline')
        atomic_json(stored, {'model_usage_count': value})
    return value


def count_usage(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open('rb') as stream:
        return sum(line.startswith(USAGE_PREFIX) for line in stream)


def stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    end = time.monotonic() + 5
    for process in processes:
        try:
            process.wait(timeout=max(0.01, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        # Kill any surviving descendants even if the group leader already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def supervise(data: Path, baseline_file: Path, commands: dict[str, list[str]],
              prepare=None) -> int:
    """Persist all child output before counting, with one owner per data volume."""
    data.mkdir(parents=True, exist_ok=True)
    with (data / 'guard.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        baseline = load_baseline(data, baseline_file)
        used = baseline + count_usage(data / 'seller.log')
        status_path = data / 'guard-state.json'
        if status_path.exists():
            previous = json.loads(status_path.read_text())
            minimum = previous['model_usage_count']
            if type(minimum) is not int or minimum < baseline or used < minimum:
                raise ValueError('persistent usage log lost records; refusing counter reset')
        stopped = False
        previous_handlers = {}
        processes = []
        streams = {}
        buffers = {}
        selector = selectors.DefaultSelector()

        def signal_stop(_number, _frame):
            nonlocal stopped
            stopped = True

        def record(reason: str):
            atomic_json(data / 'guard-state.json', {
                'reason': reason, 'baseline': baseline, 'model_usage_count': used,
                'limit': CALL_LIMIT, 'deadline': '2026-10-21T09:00:00+09:00',
                'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            })

        def stop_reason():
            if time.time() >= DEADLINE:
                return 'deadline'
            if used >= CALL_LIMIT:
                return 'model_usage_limit'
            if stopped:
                return 'signal'
            return None

        def capture(key, chunk):
            nonlocal used
            name = key.data
            output = streams[name]
            output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
            pending = buffers[name] + chunk
            lines = pending.split(b'\n')
            buffers[name] = lines.pop()
            if name == 'seller':
                increment = sum(line.startswith(USAGE_PREFIX) for line in lines)
                used += increment
                if increment:
                    record('running')

        try:
            reason = stop_reason()
            if reason:
                record(reason)
                return 0
            if prepare:
                prepare()
            for number in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[number] = signal.signal(number, signal_stop)
            for name, command in commands.items():
                streams[name] = (data / f'{name}.log').open('ab')
                # Complete any interrupted output record before appending a new run.
                if streams[name].tell():
                    streams[name].write(b'\n')
                    streams[name].flush()
                buffers[name] = b''
                process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT,
                                           start_new_session=True)
                processes.append(process)
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ, name)
            record('running')
            reason = None
            while reason is None:
                for key, _events in selector.select(timeout=0.1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if chunk:
                        capture(key, chunk)
                    else:
                        selector.unregister(key.fileobj)
                reason = stop_reason()
                if reason is None and any(p.poll() is not None for p in processes):
                    reason = 'child_exit'
            stop_processes(processes)
            # Preserve all final output, including usage emitted during shutdown.
            for key in list(selector.get_map().values()):
                while True:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        break
                    capture(key, chunk)
            used = baseline + count_usage(data / 'seller.log')
            record(reason)
            return 1 if reason == 'child_exit' else 0
        finally:
            stop_processes(processes)
            selector.close()
            for process in processes:
                if process.stdout:
                    process.stdout.close()
            for stream in streams.values():
                stream.close()
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)


def prepare_key() -> None:
    runtime = Path('/run/runtime')
    runtime.mkdir(exist_ok=True)
    destination = runtime / 'shop_key.pem'
    shutil.copyfile('/run/private/shop_key.pem', destination)
    destination.chmod(0o600)


def main() -> int:
    try:
        commands = {
            'seller': ['uv', 'run', '--offline', '--no-project', '--no-sync',
                       '--env-file', '/run/private/openai.env',
                       'python', '/app/deploy/vps/serve_public.py'],
            'cloudflared': ['cloudflared', 'tunnel', '--no-autoupdate', '--config',
                           '/run/private/cloudflared.yml', 'run'],
        }
        if os.environ.get('AX_TUNNEL_ENABLED', '1') == '0':
            del commands['cloudflared']
        return supervise(Path('/data'), Path('/run/private/baseline.json'),
                         commands, prepare=prepare_key)
    except Exception as error:
        # Do not print command/environment/credential contents on failure.
        print(f'GUARD_ERROR type={type(error).__name__}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
