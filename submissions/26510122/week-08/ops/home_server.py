"""Move existing market identities/state, then render Linux user services.

The private bundle is NEVER for Git. Export only after stopping all source
services; restore refuses existing runtimes and never starts a market client.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = ('seller-runtime', 'buyer-runtime')
SELLER_LOGS = {'shop-events.jsonl','seller-forum-events.jsonl','seller-forum-console.log',
               'seller-meta-console.log','seller-meta-events.jsonl','procurement-console.log','seller-r3-kit-console.log','seller-r3-supervisor.err','seller-r3-supervisor.log','seller-r3-tunnel.log'}


def alive(pid):
    if type(pid) is not int or pid <= 0: return False
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True
    return True


def assert_stopped(root):
    for name in RUNTIMES:
        runtime = root/'state'/name
        for status in (runtime/'running.json', runtime/'state/buyer-logdb-status.json',
                       runtime/'state/procurement-scheduler-status.json'):
            if not status.exists(): continue
            data = json.loads(status.read_text())
            for field in ('supervisor_pid', 'kit_pid', 'forum_pid', 'tunnel_pid', 'meta_pid', 'procurement_pid', 'buyer_forum_pid', 'pid'):
                if alive(data.get(field)):
                    raise ValueError(f'Stop source services before export: {name}/{field} is alive')


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_bundle(root, output, owner_file):
    assert_stopped(root)
    output = output.resolve()
    # All private artifacts live below the repository-ignored state directory.
    if not output.is_relative_to((root/'state').resolve()):
        raise ValueError('Write the private archive inside ignored state/ only')
    if any(output.is_relative_to((root/'state'/name).resolve()) for name in RUNTIMES):
        raise ValueError('Archive cannot be inside an exported runtime')
    if output.exists(): raise FileExistsError(output)
    for name in RUNTIMES:
        for key in ('shop_key.pem', 'buyer_key.pem'):
            if not (root/'state'/name/key).is_file(): raise FileNotFoundError(f'{name}/{key}')
    if not owner_file.is_file(): raise FileNotFoundError('Market owner credential is missing')
    output.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(output.parent, 0o700)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd,'wb') as stream, tarfile.open(fileobj=stream, mode='w:gz',dereference=True) as archive:
            databases=[]
            def select(info):
                parts = Path(info.name).parts
                if parts[:3]==('state','seller-runtime','logs') and len(parts)>3:
                    if parts[3] not in SELLER_LOGS and not parts[3].startswith('market-kit.log'):return None
                if any(p.startswith(('release-previous','release-rejected')) or p=='__pycache__' for p in parts): return None
                if Path(info.name).name in ('running.json','buyer-logdb-status.json','known_hosts'): return None
                if info.name.endswith(('.lock','-wal','-shm','.tmp')): return None
                if info.isfile() and info.name.endswith(('.sqlite3','.sqlite','.db')):
                    databases.append((root/info.name,info.name));return None
                if not (info.isfile() or info.isdir()): raise ValueError('Unsupported archive entry')
                info.uid = info.gid = 0;info.uname = info.gname = ''
                info.mode = 0o700 if info.isdir() else 0o600
                return info
            for name in RUNTIMES:
                archive.add(root/'state'/name, arcname='state/'+name, filter=select)
            with tempfile.TemporaryDirectory(dir=root/'state') as backup_dir:
                for index,(source,arcname) in enumerate(databases):
                    backup=Path(backup_dir)/str(index)
                    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as origin, sqlite3.connect(backup) as target:
                        origin.backup(target)
                    info=archive.gettarinfo(backup,arcname=arcname);info.mode=0o600
                    with backup.open('rb') as stream:archive.addfile(info,stream)
            archive.add(owner_file, arcname='credentials/owner-code', filter=select)
            assert_stopped(root)
            metadata={'version':1,'exported_at':time.time(),'source_commit':subprocess.check_output(
                ['git','rev-parse','HEAD'],cwd=root,text=True).strip()}
            with tempfile.TemporaryDirectory(dir=root/'state') as directory:
                manifest=Path(directory)/'handoff.json';manifest.write_text(json.dumps(metadata,indent=2))
                archive.add(manifest,arcname='handoff.json',filter=select)
    except Exception:
        # Keep a partial failed artifact for diagnosis; refuse to reuse its name.
        raise
    print(f'Private handoff archive ready: {output} (mode 0600; never add to Git)')


def configs(root, runtime, role):
    python=str(root/'.venv/bin/python');release=runtime/'release'
    if role=='seller':
        return {'harness':'Frozen seller only; no buyer hooks',
                'seller':{'cmd':shlex.join([python,str(release/'seller_r3.py'),'--port','{port}'])},
                'restock':{'cmd':shlex.join([python,str(release/'procurement_scheduler.py'),'--execute','--state','{state}'])}}
    return {'harness':'Plain issued-order buyer; no roleplay; independent paid-evidence forum worker',
            'market_buyer':{'cmd':shlex.join([python,str(release/'market_buyer_r3.py'),
                '--shops','{shops}','--order','{order}','--own-shop-name','outseop-26510122','--epsilon','0.1'])},
            'review':{'cmd':shlex.join([python,str(release/'review_r3.py'),'--deal','{deal}'])}}


def validate_release(runtime):
    manifest=json.loads((runtime/'release.json').read_text())
    for name,digest in manifest['sha256'].items():
        if Path(name).name != name or file_hash(runtime/'release'/name)!=digest:
            raise ValueError(f'Frozen release integrity mismatch: {name}')


def restore_bundle(root, bundle, owner_file):
    for name in RUNTIMES:
        if (root/'state'/name).exists(): raise FileExistsError(f'Refusing to overwrite {name}')
    (root/'state').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='handoff-restore-',dir=root/'state') as directory:
        stage=Path(directory)
        with tarfile.open(bundle,'r:gz') as archive:
            names=set()
            for entry in archive.getmembers():
                p=Path(entry.name)
                allowed=(p.parts[:2] in (('state','seller-runtime'),('state','buyer-runtime'))
                         or entry.name in ('handoff.json','credentials/owner-code'))
                if (p.is_absolute() or '..' in p.parts or not allowed or entry.name in names
                        or not (entry.isfile() or entry.isdir())):
                    raise ValueError('Unsafe or unsupported private archive member')
                names.add(entry.name)
            # Explicitly checked paths and regular-file-only entries; compatible with Python 3.10+.
            for entry in archive.getmembers():
                destination=stage/entry.name
                if entry.isdir():destination.mkdir(parents=True,exist_ok=True,mode=0o700)
                else:
                    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    with archive.extractfile(entry) as source, destination.open('xb') as target:
                        shutil.copyfileobj(source,target)
                    destination.chmod(0o600)
        meta=json.loads((stage/'handoff.json').read_text())
        if meta.get('version')!=1:raise ValueError('Unsupported handoff version')
        credential=(stage/'credentials/owner-code').read_bytes()
        if owner_file.exists() and owner_file.read_bytes()!=credential:
            raise ValueError('Destination has a different owner credential; refusing overwrite')
        # Validate both identities/releases before moving either one.
        for name in RUNTIMES:
            runtime=stage/'state'/name;validate_release(runtime)
            for key in ('shop_key.pem','buyer_key.pem'):
                from cryptography.hazmat.primitives.serialization import load_pem_private_key
                load_pem_private_key((runtime/key).read_bytes(),password=None)
            (runtime/'agent.json').write_text(json.dumps(configs(root,root/'state'/name,name.split('-')[0]),indent=2))
        seller_logs=stage/'state/seller-runtime/logs'
        logs=root/'logs';logs.mkdir(exist_ok=True)
        for source in seller_logs.iterdir():
            destination=logs/source.name
            if destination.exists() and file_hash(destination)!=file_hash(source):
                raise FileExistsError(f'Destination already has different live log: {source.name}')
        owner_file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not owner_file.exists():
            fd=os.open(owner_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as target:target.write(credential)
        for source in seller_logs.iterdir():
            if not (logs/source.name).exists():shutil.move(str(source),str(logs/source.name))
        shutil.rmtree(seller_logs);seller_logs.symlink_to(logs,target_is_directory=True)
        for name in RUNTIMES:shutil.move(str(stage/'state'/name),str(root/'state'/name))
    print('Restored existing identities, DBs, raw logs, policy state and frozen releases. No services started.')


def unit(root, role, codex_dir):
    def quote(value):return json.dumps(str(value).replace('%','%%'))
    commands={'seller':['ops/seller_service.py'], 'buyer':['ops/buyer_service.py'],
              'buyer-logdb':['buyer_logdb.py','--runtime',str(root/'state/buyer-runtime'),'--loop']}
    command=[str(root/'.venv/bin/python'),str(root/commands[role][0]),*commands[role][1:]]
    path=f'{codex_dir}:{Path.home()/".local/bin"}:/usr/local/bin:/usr/bin:/bin'
    return '\n'.join(['[Unit]',f'Description=Outseop Round 3 {role}',
        'After=network-online.target','Wants=network-online.target','','[Service]','Type=simple',
        'WorkingDirectory='+str(root),'Environment='+quote('PATH='+path),'Environment=TZ=Asia/Seoul',
        'ExecStart='+' '.join(quote(v) for v in command),'Restart=always','RestartSec=5',
        'KillMode=mixed','TimeoutStopSec=45','UMask=0077','','[Install]','WantedBy=default.target',''])


def render(root, output, codex):
    if not codex:raise FileNotFoundError('Install Codex CLI first; it is missing from PATH')
    for name in RUNTIMES:
        runtime=root/'state'/name;validate_release(runtime)
        (runtime/'agent.json').write_text(json.dumps(configs(root,runtime,name.split('-')[0]),indent=2))
    output.mkdir(parents=True,exist_ok=True)
    for role in ('seller','buyer','buyer-logdb'):
        (output/f'outseop-r3-{role}.service').write_text(unit(root,role,Path(codex).parent))
    print(f'Linux user units rendered in {output}; not installed or started.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    export=sub.add_parser('export');export.add_argument('--output',type=Path,required=True)
    restore=sub.add_parser('restore');restore.add_argument('archive',type=Path)
    render_parser=sub.add_parser('render');render_parser.add_argument('--output',type=Path,default=ROOT/'state/systemd')
    parser.add_argument('--owner-file',type=Path,default=Path.home()/'.config/ourolabs-market/owner-code')
    args=parser.parse_args()
    if args.action=='export':export_bundle(ROOT,args.output,args.owner_file)
    elif args.action=='restore':restore_bundle(ROOT,args.archive,args.owner_file)
    else:render(ROOT,args.output,shutil.which('codex'))

if __name__=='__main__':main()
