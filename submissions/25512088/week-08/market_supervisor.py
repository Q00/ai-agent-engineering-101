"""Durable local process supervisor and read-only observer for market roles."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from adaptive_control import Control


ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".market"
SUPERVISOR = STATE / "supervisor"
EVENTS = SUPERVISOR / "runtime-events.jsonl"
PIDS = SUPERVISOR / "pids.json"
PIDFILE = SUPERVISOR / "supervisor.pid"
LOG = ROOT / "logs" / "adaptive-supervisor.log"


def _alive(pid):
    if type(pid) is not int or pid <= 1:return False
    try:os.kill(pid,0);return True
    except OSError:return False


def _atomic_json(path, body):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(body,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
    tmp.replace(path)


class Supervisor:
    def __init__(self, root=ROOT, commands=None):
        self.root=Path(root);self.state=self.root/'.market';self.dir=self.state/'supervisor'
        self.events=self.dir/'runtime-events.jsonl';self.pids=self.dir/'pids.json'
        self.dir.mkdir(parents=True,exist_ok=True);(self.root/'logs').mkdir(exist_ok=True)
        runner=str(self.root/'run_ubuntu.sh')
        self.commands=commands or {
            'shop':['bash',runner,'shop'],'buyer':['bash',runner,'buyer'],
            'procurement':['bash',runner,'procurement']}
        self.children={};self.logs={};self.stop_requested=False;self.control=Control(self.state)

    def emit(self, role, event, **body):
        row={'ts':time.time(),'role':role,'event':event,'pid':body.pop('pid',None),**body}
        with self.events.open('a') as out:
            out.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n');out.flush()
        self.control.event(role,event,body,correlation=body.get('correlation'))

    def _persist(self):
        _atomic_json(self.pids,{'supervisor':os.getpid(),
            'roles':{role:{'pid':p.pid,'started':started} for role,(p,started) in self.children.items()},
            'updated':time.time()})

    def start_role(self, role):
        if role in self.children and self.children[role][0].poll() is None:return self.children[role][0]
        handle=(self.root/'logs'/f'{role}-runtime.log').open('a')
        env=dict(os.environ,MARKET_SUPERVISED='1',MARKET_ROLE=role)
        process=subprocess.Popen(self.commands[role],cwd=self.root,stdin=subprocess.DEVNULL,
            stdout=handle,stderr=subprocess.STDOUT,start_new_session=True,env=env)
        self.logs[role]=handle;self.children[role]=(process,time.time())
        self.emit(role,'role_started',pid=process.pid,command=Path(self.commands[role][0]).name)
        self.control.checkpoint(role,{'command_version':'supervisor-v1'},{'pid':process.pid})
        self._persist();return process

    def stop_role(self, role, timeout=15):
        item=self.children.get(role)
        if not item:return
        process,_=item
        if process.poll() is None:
            self.emit(role,'drain_started',pid=process.pid)
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait(timeout=5);self.emit(role,'role_killed',pid=process.pid)
        self.emit(role,'role_stopped',pid=process.pid,returncode=process.returncode)
        handle=self.logs.pop(role,None)
        if handle:handle.close()
        self.children.pop(role,None);self._persist()

    def run(self):
        lock=(self.dir/'supervisor.lock').open('a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('adaptive supervisor already owns the lock')
        def request_stop(_sig,_frame):self.stop_requested=True
        signal.signal(signal.SIGTERM,request_stop);signal.signal(signal.SIGINT,request_stop)
        PIDFILE.parent.mkdir(parents=True,exist_ok=True);PIDFILE.write_text(str(os.getpid())+'\n')
        self.emit('supervisor','supervisor_started',pid=os.getpid())
        try:
            for role in ('shop','buyer','procurement'):self.start_role(role)
            while not self.stop_requested:
                for role,(process,started) in list(self.children.items()):
                    code=process.poll()
                    if code is None:continue
                    self.emit(role,'role_exited',pid=process.pid,returncode=code,uptime=time.time()-started)
                    self.children.pop(role,None);handle=self.logs.pop(role,None)
                    if handle:handle.close()
                    if not self.stop_requested:
                        time.sleep(2);self.emit(role,'restart_started',previous_returncode=code);self.start_role(role)
                self._persist();time.sleep(1)
        finally:
            for role in tuple(self.children):self.stop_role(role)
            self.emit('supervisor','supervisor_stopped',pid=os.getpid())
            try:PIDFILE.unlink()
            except FileNotFoundError:pass
            self.control.close();lock.close()


def status():
    try:pid=int(PIDFILE.read_text().strip())
    except (OSError,ValueError):pid=None
    data={'running':_alive(pid),'pid':pid,'roles':{},'events':str(EVENTS)}
    try:data['roles']=json.loads(PIDS.read_text()).get('roles',{})
    except (OSError,ValueError):pass
    for row in data['roles'].values():row['alive']=_alive(row.get('pid'))
    print(json.dumps(data,ensure_ascii=False,sort_keys=True));return 0 if data['running'] else 1


def start():
    SUPERVISOR.mkdir(parents=True,exist_ok=True);LOG.parent.mkdir(exist_ok=True)
    try:pid=int(PIDFILE.read_text().strip())
    except (OSError,ValueError):pid=None
    if _alive(pid):print(json.dumps({'running':True,'pid':pid}));return 0
    log=LOG.open('a')
    process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'run'],cwd=ROOT,
        stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    deadline=time.time()+10
    while time.time()<deadline:
        if PIDFILE.exists() and _alive(process.pid):
            print(json.dumps({'running':True,'pid':process.pid,'events':str(EVENTS)}));return 0
        if process.poll() is not None:break
        time.sleep(.1)
    print(json.dumps({'running':False,'pid':process.pid,'error':'startup timeout'}));return 1


def stop():
    try:pid=int(PIDFILE.read_text().strip())
    except (OSError,ValueError):print(json.dumps({'stopped':True,'reason':'not running'}));return 0
    if not _alive(pid):print(json.dumps({'stopped':True,'reason':'stale pid'}));return 0
    os.kill(pid,signal.SIGTERM);deadline=time.time()+30
    while time.time()<deadline and _alive(pid):time.sleep(.2)
    print(json.dumps({'stopped':not _alive(pid),'pid':pid}));return 0 if not _alive(pid) else 1


def watch(role='all',from_start=False):
    EVENTS.parent.mkdir(parents=True,exist_ok=True);EVENTS.touch(exist_ok=True)
    with EVENTS.open() as stream:
        if not from_start:stream.seek(0,2)
        try:
            while True:
                line=stream.readline()
                if not line:time.sleep(.2);continue
                try:row=json.loads(line)
                except ValueError:continue
                if role=='all' or row.get('role')==role:print(json.dumps(row,ensure_ascii=False),flush=True)
        except KeyboardInterrupt:return 0


def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=('start','run','stop','status','watch'))
    ap.add_argument('role',nargs='?',default='all');ap.add_argument('--from-start',action='store_true');args=ap.parse_args()
    if args.command=='start':return start()
    if args.command=='stop':return stop()
    if args.command=='status':return status()
    if args.command=='watch':return watch(args.role,args.from_start)
    Supervisor().run();return 0


if __name__=='__main__':raise SystemExit(main())
