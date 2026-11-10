"""Load the shared official kit read-only and attach supply observability."""
import importlib.util
import json
import re
import sqlite3
import time
from pathlib import Path
import sys
from procurement_transport import instrument
from seller_state import SellerState


def instrument_registration(kit,*,sleep=time.sleep):
    """Keep a newly opened URL alive for bounded retries of card timeouts only.

    Official join regenerates its own signature on each attempt. No checkout,
    supply, authentication refusal or other 4xx is retried by this wrapper.
    """
    if getattr(kit.Market,'_card_retry_installed',False):return
    join=kit.Market.join
    def with_retry(self,role,*args,**kwargs):
        for attempt in range(4):
            try:return join(self,role,*args,**kwargs)
            except SystemExit as error:
                match=re.fullmatch(r'\[market\] join as shop failed: 400 (.+)',str(error))
                try:reason=json.loads(match[1]).get('error','') if match else ''
                except ValueError:reason=''
                transient=(role=='shop' and reason.startswith('cannot read an agent card at ')
                    and reason.endswith(('(TimeoutError)','(ConnectTimeout)','(ReadTimeout)')))
                if not transient or attempt==3:raise
                kit.log(f'[market] card check timed out; retrying the same shop URL in 15 s (attempt {attempt+2}/4)')
                sleep(15)
    kit.Market.join=with_retry;kit.Market._card_retry_installed=True


def instrument_settlement(kit, state):
    """Persist the sold unit before the official kit refreshes the stock snapshot."""
    call = kit.Market.call

    def observe(market, method, path, *args, **kwargs):
        response = call(market, method, path, *args, **kwargs)
        if method == 'POST' and path == '/api/settle' and response.status_code == 200:
            try:
                result = response.json()
                unit = result['mandate_receipt'].get('unit')
                if type(result.get('ledger')) is int and isinstance(unit, str) and unit:
                    state.mark_settled({unit})
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError) as error:
                # An accepted payment stays accepted; the seller also imports the kit log.
                kit.log(f'[acp] settled unit index write failed: {type(error).__name__}')
        return response

    kit.Market.call = observe


def main():
    path=Path(sys.argv[1]).resolve();sys.argv.pop(1)
    spec=importlib.util.spec_from_file_location('observed_official_market_kit',path)
    kit=importlib.util.module_from_spec(spec);sys.modules[spec.name]=kit
    spec.loader.exec_module(kit)
    instrument(kit)
    instrument_registration(kit)
    instrument_settlement(kit, SellerState())
    kit.main()


if __name__=='__main__':main()
