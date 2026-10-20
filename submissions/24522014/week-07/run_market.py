"""Run the official market kit with Windows trusted CAs and this week's tools."""

import os
from pathlib import Path
import runpy
import ssl
import sys

import certifi


def prevent_automatic_sleep():
    if os.name != "nt":
        return None
    import ctypes
    kernel = ctypes.windll.kernel32
    if kernel.SetThreadExecutionState(0x80000001):
        print("[host] Automatic sleep prevention active while market kit runs.", flush=True)
        return kernel
    print("[host] Could not request automatic sleep prevention.", flush=True)
    return None


def main():
    submission = Path(__file__).resolve().parent
    repository = submission.parents[2]
    tools = submission / "tools"
    tools.mkdir(exist_ok=True)
    # httpx defaults to certifi, which lacks this machine's locally trusted CAs.
    # Add Windows' existing trust store while keeping TLS verification enabled.
    context = ssl.create_default_context()
    bundle = tools / "windows-ca-bundle.pem"
    bundle.write_text(
        Path(certifi.where()).read_text(encoding="ascii") + "\n"
        + "".join(ssl.DER_cert_to_PEM_cert(cert) for cert in context.get_ca_certs(binary_form=True)),
        encoding="ascii",
    )
    os.environ["SSL_CERT_FILE"] = str(bundle)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), str(tools), os.environ.get("PATH", "")]
    )
    os.environ["VIRTUAL_ENV"] = str(submission / ".venv")
    kit = repository / "scripts" / "market_kit.py"
    sys.argv = [str(kit), "join", "--dir", str(submission),
                "--market", "https://market.ourolabs.ai", "--name", "kcwarni",
                *sys.argv[1:]]
    power_request = prevent_automatic_sleep()
    try:
        runpy.run_path(str(kit), run_name="__main__")
    finally:
        if power_request is not None:
            power_request.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
