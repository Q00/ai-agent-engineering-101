"""Local pinned four-file owner CLI. Apply installs forced OFF; never runs buyer code."""
import argparse
import ast
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import uuid


PREFIX = 'submissions/26622007/week-08/'
FILES = tuple(PREFIX + name for name in (
    'buyer_quote_alignment_policy.py', 'buyer_quote_alignment.py',
    'tools/quote_alignment_measure.py', 'market_buyer.py'))
OFF = b"SOURCE_CONTROL = True\nENABLED = False\nSTART = 0\nSTUDY = ''\n"


def git(repo, *args):
    return subprocess.check_output(
        ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
         '-C', str(repo), *args], stderr=subprocess.PIPE, timeout=15)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def repository(raw):
    path = Path(raw).absolute()
    if path.resolve() != path:
        raise ValueError('repository symlink or noncanonical path')
    if Path(git(path, 'rev-parse', '--show-toplevel').decode().strip()) != path:
        raise ValueError('repository root required')
    return path


def revision(value):
    if not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('full commit hash required')
    return value


def safe_file(root, name):
    path = root / name
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError('symlink or path escape')
    if path.exists() and not path.is_file():
        raise ValueError('regular file required')
    return path


def policy(data):
    if len(data) > 4096:
        raise ValueError('oversized policy')
    tree = ast.parse(data)
    values = {}
    for node in tree.body:
        if (not isinstance(node, ast.Assign) or len(node.targets) != 1
                or not isinstance(node.targets[0], ast.Name)):
            raise ValueError('four literal assignments required')
        name = node.targets[0].id
        if name in values:
            raise ValueError('duplicate policy key')
        values[name] = ast.literal_eval(node.value)
    if set(values) != {'SOURCE_CONTROL', 'ENABLED', 'START', 'STUDY'}:
        raise ValueError('four policy keys required')
    if any(type(values[n]) is not bool for n in ('SOURCE_CONTROL', 'ENABLED')):
        raise ValueError('policy flags must be bool')
    start, study = values['START'], values['STUDY']
    if type(start) is not int or start < 0:
        raise ValueError('START must be nonnegative int')
    if type(study) is not str or not study.isascii() or len(study) > 64:
        raise ValueError('STUDY must be ASCII up to 64 characters')
    if values['ENABLED'] and (not values['SOURCE_CONTROL'] or start == 0 or not study):
        raise ValueError('ON requires source control, positive START and STUDY')
    return values


def validate(args):
    target = repository(args.target)
    head = revision(args.expected_head)
    if git(target, 'rev-parse', 'HEAD').decode().strip() != head:
        raise ValueError('target HEAD changed')
    stage = Path(args.staging).absolute()
    if stage.resolve() != stage or stage.is_relative_to(target) or target.is_relative_to(stage):
        raise ValueError('staging must be separate and nonsymlinked')
    data = safe_file(stage, 'manifest.json').read_bytes()
    if digest(data) != args.manifest_sha256:
        raise ValueError('manifest hash mismatch')
    manifest = json.loads(data)
    if (set(manifest) != {'version', 'commit', 'expected_head', 'target', 'files'}
            or type(manifest['version']) is not int or manifest['version'] != 1
            or manifest['expected_head'] != head or manifest['target'] != str(target)
            or set(manifest['files']) != set(FILES)):
        raise ValueError('manifest contract mismatch')
    revision(manifest['commit'])
    blobs = {}
    for name in FILES:
        blobs[name] = safe_file(stage, name).read_bytes()
        if digest(blobs[name]) != manifest['files'][name]:
            raise ValueError('staged blob hash mismatch')
        path = safe_file(target, name)
        entry = git(target, 'ls-tree', head, '--', name).decode().split()
        if entry:
            if entry[0] != '100644' or not path.exists():
                raise ValueError('target file mode or existence mismatch')
            if path.stat().st_mode & 0o111:
                raise ValueError('allowlisted target mode dirty')
            if path.read_bytes() != git(target, 'show', head + ':' + name):
                raise ValueError('allowlisted target dirty')
        elif path.exists():
            raise ValueError('untracked allowlisted target')
    if git(target, 'diff', '--cached', '--name-only', head, '--', *FILES).strip():
        raise ValueError('allowlisted index dirty')
    policy(blobs[FILES[0]])
    return target, stage, blobs


def plan(args):
    target, stage, blobs = validate(args)
    return dict(status='planned', order=list(FILES), policy='forced-OFF',
                confirm=f'apply:{target}:{args.expected_head}:{args.manifest_sha256}')


@contextmanager
def owner_lock(target):
    """Serialize cooperating apply/off calls without creating a target file."""
    fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def atomic_write(path, data):
    """Replace through nonsymlink directory descriptors on the destination FS."""
    path = Path(path).absolute()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    temp = '.quote-owner-' + uuid.uuid4().hex
    created = False
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        try:
            old = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            old = None
        if old and not stat.S_ISREG(old.st_mode):
            raise ValueError('destination must be regular')
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      stat.S_IMODE(old.st_mode) if old else 0o644, dir_fd=fd)
        created = True
        with os.fdopen(out, 'wb') as stream:
            if old:
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (old.st_uid, old.st_gid):
                    os.fchown(stream.fileno(), old.st_uid, old.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(old.st_mode))
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        created = False
        os.fsync(fd)
    finally:
        if created:
            os.unlink(temp, dir_fd=fd)
        os.close(fd)


def sourced_off(target):
    try:
        return safe_file(target, FILES[0]).read_bytes() == OFF
    except (ValueError, OSError):
        return False


def execute_apply(args, *, write=atomic_write):
    """Public library seam; write injection is for temporary fixture tests only.

    No CLI failure injection, shell, SSH, host or policy activation option exists.
    """
    target = repository(args.target)
    with owner_lock(target):
        target, stage, blobs = validate(args)
        expected = f'apply:{target}:{args.expected_head}:{args.manifest_sha256}'
        if args.confirm != expected:
            raise ValueError('exact plan confirmation required')
        originals = {n: (safe_file(target, n).read_bytes()
                         if safe_file(target, n).exists() else None) for n in FILES}
        if originals[FILES[3]] is None:
            raise ValueError('existing buyer entry required')
        backup = stage / 'backup'
        backup.mkdir(mode=0o700)
        for name, data in originals.items():
            if data is not None:
                p = backup / name
                p.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(p, data)
        atomic_write(backup / 'manifest.json', (json.dumps({
            'head': args.expected_head,
            'files': {n: digest(b) if b is not None else None for n, b in originals.items()}
        }, sort_keys=True) + '\n').encode())
        # Persist directory links as well as backup file contents before touching target.
        for directory in sorted((p for p in backup.rglob('*') if p.is_dir()),
                                key=lambda p: len(p.parts), reverse=True):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        for directory in (backup, stage):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        written = []
        entry_attempted = False
        try:
            for name in FILES:
                if git(target, 'rev-parse', 'HEAD').decode().strip() != args.expected_head:
                    raise ValueError('target HEAD changed during apply')
                p = safe_file(target, name)
                actual = p.read_bytes() if p.exists() else None
                if actual != originals[name]:
                    raise ValueError('target changed during apply')
                if name == FILES[3]:
                    entry_attempted = True
                write(p, OFF if name == FILES[0] else blobs[name])
                written.append(name)
            if git(target, 'rev-parse', 'HEAD').decode().strip() != args.expected_head:
                raise ValueError('target HEAD changed during apply')
            return dict(status='applied', written=written, sourcedOFF=sourced_off(target))
        except Exception as exc:
            errors = []
            try:
                write(safe_file(target, FILES[0]), OFF)
            except Exception as recovery:
                errors.append('OFF: ' + str(recovery))
            if entry_attempted:
                try:
                    write(safe_file(target, FILES[3]), originals[FILES[3]])
                except Exception as recovery:
                    errors.append('entry: ' + str(recovery))
            return dict(status='failed', error=str(exc), written=written,
                        sourcedOFF=sourced_off(target), recovery_errors=errors)


def execute_off(args):
    target = repository(args.target)
    with owner_lock(target):
        head = revision(args.expected_head)
        if git(target, 'rev-parse', 'HEAD').decode().strip() != head:
            raise ValueError('target HEAD changed')
        if args.confirm != f'off:{target}:{head}':
            raise ValueError('exact OFF confirmation required')
        path = safe_file(target, FILES[0])
        try:
            atomic_write(path, OFF)
            return dict(status='off', sourcedOFF=sourced_off(target))
        except (OSError, ValueError) as exc:
            return dict(status='failed', error=str(exc), sourcedOFF=sourced_off(target))


def prepare(args):
    source, target = repository(args.source), repository(args.target)
    commit, head = revision(args.commit), revision(args.expected_head)
    if git(source, 'rev-parse', commit + '^{commit}').decode().strip() != commit:
        raise ValueError('source commit mismatch')
    if git(target, 'rev-parse', 'HEAD').decode().strip() != head:
        raise ValueError('target HEAD changed')
    stage = Path(args.staging).absolute()
    if stage.resolve() != stage or stage.is_relative_to(target) or stage.is_relative_to(source):
        raise ValueError('staging must be separate and nonsymlinked')
    blobs = {}
    for name in FILES:
        entry = git(source, 'ls-tree', commit, '--', name).decode().split()
        if len(entry) != 4 or entry[0] != '100644' or entry[1] != 'blob':
            raise ValueError('source must contain regular allowlisted files')
        blobs[name] = git(source, 'show', commit + ':' + name)
    stage.mkdir(mode=0o700)
    for name, data in blobs.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = dict(version=1, commit=commit, expected_head=head,
                    target=str(target), files={n: digest(b) for n, b in blobs.items()})
    data = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    (stage / 'manifest.json').write_bytes(data)
    return dict(status='prepared', manifest_sha256=digest(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    for name in ('source', 'commit', 'target', 'expected-head', 'staging'):
        p.add_argument('--' + name, required=True)
    for command in ('plan', 'apply'):
        p = commands.add_parser(command)
        for name in ('target', 'expected-head', 'staging', 'manifest-sha256'):
            p.add_argument('--' + name, required=True)
        if command == 'apply':
            p.add_argument('--confirm', required=True)
    p = commands.add_parser('off')
    for name in ('target', 'expected-head', 'confirm'):
        p.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        result = {'prepare': prepare, 'plan': plan, 'apply': execute_apply,
                  'off': execute_off}[args.command](args)
    except (ValueError, SyntaxError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status='rejected', error=str(exc))))
        return 2
    print(json.dumps(result))
    return 2 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
