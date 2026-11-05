"""Check the public round every ten minutes, then supervise both market roles."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import httpx
from r3.lifecycle import lease, market_active, request_stop
from r3.state import ROOT, atomic_json, policy,read_json

INTERVAL = 600
MARKET = 'https://market.ourolabs.ai'


def current_round():
    response = httpx.get(MARKET + '/api/board', timeout=15)
    response.raise_for_status()
    value = response.json().get('current')
    if type(value) is not int or value < 0:
        raise ValueError('Public board current round is not an integer')
    return value


def launch(log):
    owner = os.environ.get('MARKET_OWNER', '')
    if not owner:
        for line in (ROOT / '.market/owner.env').read_text(encoding='utf-8-sig').splitlines():
            name, sep, value = line.strip().partition('=')
            if sep and name.strip() == 'MARKET_OWNER':
                owner = value.strip().strip('\"\'')
    if not owner:
        raise RuntimeError('Existing owner credential is unavailable')
    env = dict(os.environ, MARKET_OWNER=owner, MARKET_TUNNEL_PROVIDER=policy().get('tunnel_provider','pinggy'), PYTHONUTF8='1',
               R3_LAUNCH_ID=log.stem)
    command = [sys.executable, '-u', str(ROOT / 'run_market.py'), 'join', '--dir', str(ROOT),
               '--market', MARKET, '--name', 'seochanit', '--tunnel']
    with log.open('xb') as out:
        return subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


class Watcher:
    def __init__(self, root=ROOT, fetch=current_round, spawn=launch, active=market_active):
        self.root, self.fetch, self.spawn, self.active = root, fetch, spawn, active
        self.child = None
        self.child_log = None
        self.next_check = 0
        stop_at = policy(root).get('end_at')
        self.end = datetime.fromisoformat(stop_at).timestamp() if stop_at else float('inf')
        self.state = dict(watcher_pid=os.getpid(), interval_seconds=INTERVAL,
                          market_join_attempted=False, both_roles_registered=False)
        self.stop_file = root / '.market/watch-stop.request'
        self.state_path = root / '.market/round3-watch.json'
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.log = root / 'logs' / f'round3-watch-{stamp}.jsonl'
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.touch(exist_ok=False)
        self.state['watch_log'] = str(self.log)

    def record(self, event, **data):
        self.state.update(data, status=event, updated_at=time.time())
        with self.log.open('a', encoding='utf-8') as out:
            out.write(json.dumps(dict(event=event, utc=datetime.now(timezone.utc).isoformat(), **data)) + '\n')
        self.save()

    def save(self):
        self.state['next_check_kst'] = datetime.fromtimestamp(
            self.next_check, timezone.utc).astimezone(timezone(timedelta(hours=9))).isoformat() if self.next_check else None
        atomic_json(self.state_path, self.state)

    def tick(self, now):
        if self.stop_file.exists() or now >= self.end:
            self.record('stopping', reason='requested' if self.stop_file.exists() else 'round3_deadline')
            if self.child and self.child.poll() is None:
                request_stop(self.child.pid, self.root, launch_id=self.child_log.stem)
                return True  # Keep supervising until the kit has cleaned up its children.
            self.record('stopped')
            return False
        if self.child is not None and self.child.poll() is not None:
            if self.child.returncode and self.child_log and self.child_log.exists():
                with self.child_log.open('rb') as source:
                    source.seek(max(0,self.child_log.stat().st_size-8192))
                    tail=source.read().decode('utf-8',errors='replace')
                if any(line.startswith('[market] join as shop failed:') and
                       'cannot read an agent card' in line for line in tail.splitlines()):
                    preference=read_json(self.root/'.market/transport-provider.json',{})
                    previous=preference.get('provider','cloudflare')
                    following={'cloudflare':'localhost','localhost':'pinggy','pinggy':'cloudflare'}.get(previous,'localhost')
                    pinned=policy(self.root).get('tunnel_provider')
                    if pinned in {'cloudflare','localhost','pinggy'}:following=pinned
                    atomic_json(self.root/'.market/transport-provider.json',
                        {'provider':following,'at':time.time(),'reason':'market_agent_card_unreachable'})
                    self.record('market_transport_failed',previous_provider=previous,next_provider=following)
            self.record('market_exited', market_exit_code=self.child.returncode,
                        both_roles_registered=False, market_pid=None)
            self.child = None
            self.next_check=min(self.next_check,now+30)
        if self.child is not None:
            # Read only this attempt's console output, never old shared registration lines.
            with self.child_log.open('rb') as source:
                source.seek(max(0, self.child_log.stat().st_size - 65536))
                text = source.read().decode('utf-8', errors='replace')
            both = 'shop registered as ' in text and 'buyer registered as ' in text
            if both and not self.state['both_roles_registered']:
                self.record('both_roles_registered', both_roles_registered=True)
            return True
        if now < self.next_check:
            return True
        self.next_check = now + INTERVAL
        try:
            rnd = self.fetch()
            self.record('round_checked', current_round=rnd)
            if rnd > 3:
                self.record('stopped', reason='round3_already_finished')
                return False
            if rnd != 3:
                return True
            if self.active(self.root):
                self.record('existing_market_process', duplicate_launch_skipped=True)
                return True
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            self.child_log = self.root / 'logs' / f'auto-join-{stamp}.txt'
            self.child = self.spawn(self.child_log)
            self.record('market_starting', market_pid=self.child.pid, market_log=str(self.child_log),
                        market_join_attempted=True, both_roles_registered=False)
        except Exception as exc:
            # Do not log response bodies, environment values, or credential file contents.
            self.record('check_or_start_failed', error_type=type(exc).__name__)
        return True


def main():
    with lease(ROOT / '.market/watch.lock'):
        watcher = Watcher()
        watcher.stop_file.unlink(missing_ok=True)
        watcher.record('watching')
        if os.name == 'nt':
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        try:
            while watcher.tick(time.time()):
                time.sleep(1)
        except KeyboardInterrupt:
            watcher.stop_file.touch()
            while watcher.tick(time.time()):
                time.sleep(1)
        finally:
            if os.name == 'nt':
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__ == '__main__':
    main()
