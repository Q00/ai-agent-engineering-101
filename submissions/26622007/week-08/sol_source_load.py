"""Fresh local source loader for seller study admission, never a signature/HMAC.

Trust boundary: this CPython interpreter/stdlib, immutable local source bundle,
and pinned external packages. In-process monkeypatches/host compromise are not
attested. Local project pyc files are never used by this loader.
"""
import hashlib
import importlib.abc
import importlib.machinery
import json
from pathlib import Path
import sys
import uuid

_SELF_CODE = sys._getframe().f_code
_context = None


def sha(data): return hashlib.sha256(data).hexdigest()


class Bundle(importlib.abc.MetaPathFinder):
    def __init__(self):
        self.root = Path(__file__).resolve().parent
        self.frozen = {p.name:p.read_bytes() for p in self.root.glob('*.py') if not p.is_symlink()}
        self.hashes = {n:sha(b) for n,b in self.frozen.items()}
        self.generation = uuid.uuid4().hex
        self.instances, self.codes = {}, {}
        self.invalid = []
        self.self_valid = (_SELF_CODE == compile(self.frozen['sol_source_load.py'],
                           _SELF_CODE.co_filename, 'exec', dont_inherit=True))
        self.claim = dict(load_generation=self.generation, load_proof_sha256=sha(json.dumps(
            dict(bundle=self.hashes, cache_tag=sys.implementation.cache_tag,
                 version=sys.version, generation=self.generation), sort_keys=True).encode()))

    def local(self, module):
        filename = getattr(module, '__file__', None)
        return bool(filename and Path(filename).resolve().parent == self.root)

    def entry(self, code, name):
        filename = Path(code.co_filename).name
        data = self.frozen.get(filename)
        if data is None or code != compile(data, code.co_filename, 'exec', dont_inherit=True):
            self.invalid.append('entry_code_mismatch')
        else:
            self.instances[name] = sys.modules[name]
            self.codes[name] = (filename, sha(data))

    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if (spec is None or not isinstance(spec.loader, importlib.machinery.SourceFileLoader)
                or Path(spec.origin).resolve().parent != self.root):
            return None
        bundle = self
        class Loader(importlib.machinery.SourceFileLoader):
            def get_code(self, name):
                filename = Path(self.path).name
                data = Path(self.path).read_bytes()
                if data != bundle.frozen.get(filename):
                    raise ImportError('seller frozen source changed')
                bundle.codes[name] = (filename, sha(data))
                return compile(data, self.path, 'exec', dont_inherit=True)
            def exec_module(self, module):
                bundle.instances[module.__name__] = module
                super().exec_module(module)
        spec.loader = Loader(fullname, spec.origin)
        return spec

    def verify(self):
        if self.invalid or not self.self_valid:
            return False
        for name, module in tuple(sys.modules.items()):
            if module is sys.modules.get(__name__):
                continue
            if name+'.py' not in self.frozen and not self.local(module):
                continue
            # multiprocessing (imported by uvicorn) aliases the already verified
            # CLI entry. Only that exact instance may reuse its entry code record.
            tracked = ('__main__' if name == '__mp_main__'
                       and module is self.instances.get('__main__') else name)
            if self.instances.get(tracked) is not module or tracked not in self.codes:
                return False  # preimport/cache or a replacement module was not captured
        for filename, digest in self.codes.values():
            path = self.root/filename
            if path.is_symlink() or sha(path.read_bytes()) != digest:
                return False
        return True


def install(entry_code=None, entry_name=None):
    """Call before ANY local dependency import; late installation stays invalid."""
    global _context
    if _context is None:
        _context = Bundle()
        for name,module in tuple(sys.modules.items()):
            if (module is not sys.modules.get(__name__) and name != entry_name
                    and (_context.local(module) or name+'.py' in _context.frozen)):
                _context.invalid.append('preimported_local_module')
        sys.meta_path.insert(0, _context)
    if entry_code is not None:
        _context.entry(entry_code, entry_name)


def proof():
    if _context is None:
        return dict(valid=False, claim=dict(load_generation='uninstalled', load_proof_sha256='0'*64))
    return dict(valid=_context.verify(), claim=dict(_context.claim))


def verifies(root, claim, disk_snapshot):
    return bool(_context and _context.root == Path(root).resolve() and _context.verify()
                and all(_context.hashes.get(n) == value for n,value in disk_snapshot.items())
                and all(claim.get(k) == v for k,v in _context.claim.items()))
