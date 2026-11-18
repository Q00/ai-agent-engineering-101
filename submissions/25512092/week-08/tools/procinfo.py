"""Read-only process info on Windows x64 (ctypes, no extra package): the process list with parents, a process's
command line, environment and start time, read from its PEB. Nothing is written to any process.
    python tools/procinfo.py env <pid> [NAME ...]      (prints whether each NAME is set, not its value)
"""
import ctypes
import ctypes.wintypes as W
import sys

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
nt = ctypes.WinDLL("ntdll")
k32.OpenProcess.restype = W.HANDLE
k32.CreateToolhelp32Snapshot.restype = W.HANDLE


class _PBI(ctypes.Structure):
    _fields_ = [("r1", ctypes.c_void_p), ("Peb", ctypes.c_void_p), ("r2", ctypes.c_void_p * 2),
                ("pid", ctypes.c_void_p), ("r3", ctypes.c_void_p)]


class _PE(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", W.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def processes():
    """{pid: (parent pid, exe name)}"""
    h = k32.CreateToolhelp32Snapshot(0x2, 0)
    e = _PE()
    e.dwSize = ctypes.sizeof(e)
    out = {}
    ok = k32.Process32FirstW(h, ctypes.byref(e))
    while ok:
        out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
        ok = k32.Process32NextW(h, ctypes.byref(e))
    k32.CloseHandle(h)
    return out


def _open(pid):
    h = k32.OpenProcess(0x0410, False, pid)                # QUERY_INFORMATION | VM_READ
    if not h:
        raise OSError(ctypes.get_last_error(), f"OpenProcess {pid}")
    return h


def _read(h, addr, n):
    b = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), b, n, ctypes.byref(got)):
        raise OSError(ctypes.get_last_error(), "ReadProcessMemory")
    return b.raw[:got.value]


def _params(h):
    p = _PBI()
    nt.NtQueryInformationProcess(h, 0, ctypes.byref(p), ctypes.sizeof(p), None)
    return int.from_bytes(_read(h, p.Peb + 0x20, 8), "little")       # PEB.ProcessParameters


def cmdline(pid):
    h = _open(pid)
    try:
        pp = _params(h)
        n = int.from_bytes(_read(h, pp + 0x70, 2), "little")            # CommandLine UNICODE_STRING
        buf = int.from_bytes(_read(h, pp + 0x78, 8), "little")
        return _read(h, buf, n).decode("utf-16-le", "replace")
    finally:
        k32.CloseHandle(h)


def environ(pid):
    h = _open(pid)
    try:
        pp = _params(h)
        addr = int.from_bytes(_read(h, pp + 0x80, 8), "little")
        size = int.from_bytes(_read(h, pp + 0x3F0, 8), "little")
        raw = _read(h, addr, size).decode("utf-16-le", "replace")
    finally:
        k32.CloseHandle(h)
    return dict(x.split("=", 1) for x in raw.split("\0") if "=" in x and not x.startswith("="))


def started(pid):
    """Start time, epoch seconds."""
    h = _open(pid)
    try:
        t = [W.FILETIME() for _ in range(4)]
        k32.GetProcessTimes(h, *[ctypes.byref(x) for x in t])
        ft = (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
        return ft / 1e7 - 11644473600
    finally:
        k32.CloseHandle(h)


def descendants(root, procs=None):
    procs = procs or processes()
    kids, out, todo = {}, [], [root]
    for pid, (pp, _) in procs.items():
        kids.setdefault(pp, []).append(pid)
    while todo:
        for k in kids.get(todo.pop(), []):
            if k not in out:
                out.append(k)
                todo.append(k)
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["env"]:
        e = environ(int(sys.argv[2]))
        for n in sys.argv[3:]:
            print(n, "set" if n in e else "not set")
