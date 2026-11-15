"""Local kit adapter: role-isolated forum outboxes and buyer research worker."""
import importlib.util
import os
import re
import signal
import subprocess
import queue
from pathlib import Path
import sys
import threading
import time
from market_intel import Intel
from buyer_publicist import queue_update


def start_research(state, market):
    def loop():
        intel=Intel(state)
        while True:
            try:
                intel.refresh(market)
                queue_update(intel)
            except Exception as exc:
                print('[research]',type(exc).__name__,flush=True)
            time.sleep(30)
    threading.Thread(target=loop,daemon=True).start()


def dispatch_outbox(original, market, md):
    if 'shop' in market.token:original(market,md)
    if 'buyer' in market.token:original(market,md/'buyer-forum')


def tunnel_url(line):
    # Error logs mention api.trycloudflare.com; it is NOT a public shop tunnel.
    for host in re.findall(r'https://([a-z0-9-]+)\.trycloudflare\.com\b',line):
        if '-' in host and not host.startswith('-') and not host.endswith('-'):
            return 'https://'+host+'.trycloudflare.com'
    return None


def install_parent_exit_handlers():
    """Let the course kit unwind and stop its child process groups on tmux exit.

    The upstream kit handles SIGTERM and KeyboardInterrupt, but a detached
    tmux session is normally closed with SIGHUP.  Leaving the default SIGHUP
    action in place terminates Python before ``cmd_join`` reaches its
    ``finally`` block, orphaning the seller and tunnel.  Converting it to an
    ordinary SystemExit preserves the kit's existing cleanup path without
    adding a supervisor or restart loop.
    """
    if hasattr(signal,'SIGHUP'):
        signal.signal(signal.SIGHUP,lambda *_:sys.exit(0))


def open_tunnel(port):
    # The course kit stops tunnel processes with ``killpg(proc.pid)``.  Give
    # each cloudflared its own process group so a tunnel rotation actually
    # removes the stale process without signalling the market runtime itself.
    group={'start_new_session':True} if os.name!='nt' else {}
    proc=subprocess.Popen(['cloudflared','tunnel','--no-autoupdate','--url',f'http://127.0.0.1:{port}'],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,**group)
    urls=queue.Queue()
    def read():
        for line in proc.stdout:
            url=tunnel_url(line)
            if url:urls.put(url)
    threading.Thread(target=read,daemon=True).start()
    deadline=time.monotonic()+60
    try:
        while time.monotonic()<deadline:
            try:return proc,urls.get(timeout=.2)
            except queue.Empty:
                if proc.poll() is not None:break
        raise RuntimeError('Cloudflare did not create a public tunnel; check network and retry. API URLs are ignored.')
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        raise


def main():
    install_parent_exit_handlers()
    root=Path(__file__).resolve().parents[3]
    spec=importlib.util.spec_from_file_location('course_market_kit',root/'scripts/market_kit.py')
    kit=importlib.util.module_from_spec(spec);spec.loader.exec_module(kit)
    kit.open_tunnel=open_tunnel
    original=kit.outbox
    def outbox(market,md):
        # Buyer must never take a shop post and silently publish it as buyer.
        dispatch_outbox(original,market,md)
    kit.outbox=outbox
    argv=sys.argv[1:]
    if 'join' in argv and '--shop-only' not in argv:
        state=Path(argv[argv.index('--dir')+1] if '--dir' in argv else '.')/'.market'
        market=argv[argv.index('--market')+1]
        start_research(state.resolve(),market.rstrip('/'))
    kit.main()

if __name__=='__main__':main()
