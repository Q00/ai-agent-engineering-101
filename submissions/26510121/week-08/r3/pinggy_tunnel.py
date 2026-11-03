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
    tunnel = pinggy.start_tunnel(forwardto=f'127.0.0.1:{args.port}', type='http',
                                 httpsonly=True, autoreconnect=False)
    try:
        deadline = time.monotonic() + 70
        while time.monotonic() < deadline:
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
