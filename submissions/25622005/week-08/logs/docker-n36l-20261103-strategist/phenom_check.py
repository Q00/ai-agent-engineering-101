"""Run under qemu-x86_64 -cpu phenom: controls first, then every native module the agent loads."""
import ctypes
import mmap
import subprocess
import sys

# mov eax, edi; <instruction>; ret. The emulator must reject these if it models K10.
CONTROLS = {
    "sse4.2 crc32": bytes.fromhex("89f8" "f20f38f1c6" "c3"),
    "ssse3 pshufb": bytes.fromhex("89f8" "660f3800c0" "c3"),
    "baseline add": bytes.fromhex("89f8" "01f0" "c3"),
}


def run_code(name):
    code = CONTROLS[name]
    buf = mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
    buf.write(code)
    addr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
    fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_int)(addr)
    print(fn(2, 3))


def controls():
    ok = True
    for name in CONTROLS:
        r = subprocess.run([sys.executable, __file__, "--control", name], capture_output=True, text=True)
        rejected = r.returncode != 0
        want_rejected = name != "baseline add"
        print(f"control {name}: exit {r.returncode} ({'rejected' if rejected else 'ran'})")
        ok &= rejected == want_rejected
    return ok


def natives():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    loaded = serialization.load_pem_private_key(pem, password=None)
    loaded.public_key().verify(key.sign(b"receipt"), b"receipt")
    h = hashes.Hash(hashes.SHA256()); h.update(b"x"); h.finalize()
    print("cryptography ed25519 sign/verify, pem, sha256: ok")

    import ssl
    ssl.create_default_context()
    print(f"ssl {ssl.OPENSSL_VERSION}: ok")

    import pydantic

    class M(pydantic.BaseModel):
        sku: str
        price: int
    assert M.model_validate({"sku": "a", "price": "3"}).price == 3
    print(f"pydantic {pydantic.VERSION} (pydantic_core native): ok")

    from google.protobuf import struct_pb2
    from google.protobuf.internal import api_implementation
    s = struct_pb2.Struct(); s.update({"a": 1}); struct_pb2.Struct.FromString(s.SerializeToString())
    print(f"protobuf {api_implementation.Type()}: ok")

    import cffi
    cffi.FFI().new("int *", 7)
    import charset_normalizer
    charset_normalizer.from_bytes("가격 협상".encode("utf-8")).best()
    import wrapt

    @wrapt.decorator
    def w(f, i, a, k):
        return f(*a, **k)
    assert w(lambda: 1)() == 1
    print("cffi, charset_normalizer, wrapt: ok")

    import a2a, uvicorn, starlette, sse_starlette, httpx  # noqa: F401
    print("a2a, uvicorn, starlette, sse_starlette, httpx import: ok")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--control"]:
        run_code(sys.argv[2])
        sys.exit(0)
    print(f"python {sys.version.split()[0]} executable {sys.executable}")
    good = controls()
    natives()
    print("CONTROLS OK" if good else "CONTROLS FAILED: the emulator does not model K10")
    sys.exit(0 if good else 1)
