"""The week-07 official-SDK transport, with no market/model credentials."""
import argparse
import time
import httpx
import pinggy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    card = httpx.get(f'http://127.0.0.1:{args.port}/.well-known/agent-card.json', timeout=5)
    card.raise_for_status()
    if card.json().get('name') != 'Seochanit Shop':
        raise RuntimeError('Refusing to expose an unexpected local service')
    print('PINGGY_STAGE starting',flush=True)
    tunnel=pinggy.Tunnel()
    tunnel.token=''
    tunnel.force=False
    tunnel.reverseproxy=True
    tunnel.add_forwarding(f'127.0.0.1:{args.port}')
    tunnel.httpsonly=True
    tunnel.auto_reconnect=False
    tunnel.webdebugger=False
    # start_tunnel() waits inside native code, before our former timeout even
    # began. The installed SDK explicitly supports a bounded asynchronous start.
    tunnel.start(thread=True,block_until_ready=False)
    try:
        deadline = time.monotonic() + 70
        previous=None
        while time.monotonic() < deadline:
            if tunnel.state.name!=previous:
                previous=tunnel.state.name
                print('PINGGY_STAGE '+previous,flush=True)
            url = next((u for u in tunnel.urls if u.startswith('https://')), None)
            if url:
                print('TUNNEL_URL ' + url, flush=True)
                break
            if tunnel.state.name in {'Stopped', 'Ended', 'Invalid'}:
                raise RuntimeError('Pinggy ended before provisioning')
            time.sleep(.5)
        else:
            raise RuntimeError('Pinggy did not provision an HTTPS URL within 70 seconds')
        while tunnel.state.name not in {'Stopped', 'Ended', 'Invalid'}:
            time.sleep(1)
    finally:
        if tunnel.state.name not in {'Stopped', 'Ended', 'Invalid'}:
            tunnel.stop()


if __name__ == '__main__':
    main()
