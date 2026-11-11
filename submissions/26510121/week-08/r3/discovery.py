"""Keep public discovery independent of the long-lived negotiation HTTP pool.

Only the Shop's public card is cached, briefly. Quotes, inventory, ACP checkout,
receipts, payments and signing never use this cache.
"""
import asyncio
import copy
import time
from urllib.parse import urlsplit
import httpx
from starlette.responses import JSONResponse


class DiscoveryCache:
    def __init__(self, factory=None, clock=time.monotonic):
        self.factory=factory or (lambda: httpx.AsyncClient(timeout=2,trust_env=False,follow_redirects=False,
            limits=httpx.Limits(max_connections=4,max_keepalive_connections=4)))
        self.clock=clock;self.client=None;self.lock=asyncio.Lock();self.cached=None
        self.upstream=None;self.until=0;self.served=0;self.hits=0;self.errors=0;self.last_seconds=None
        self.retry_at=0

    async def card(self, proxy):
        started=self.clock()
        try:
            async with self.lock:
                if self.cached is None and self.clock()<self.retry_at:
                    return JSONResponse({'error':'shop discovery temporarily unavailable'},status_code=503)
                if self.cached is None or self.upstream!=proxy.upstream or self.clock()>=self.until:
                    if self.client is None:self.client=self.factory()
                    response=await self.client.get(proxy.upstream+'/.well-known/agent-card.json')
                    response.raise_for_status();card=response.json()
                    if (not isinstance(card,dict) or card.get('name')!='Seochanit Shop'
                            or not isinstance(card.get('supportedInterfaces',[]),list)):
                        raise ValueError('unexpected_discovery_identity')
                    self.cached=card;self.upstream=proxy.upstream;self.until=self.clock()+10
                else:self.hits+=1
                # rewrite() also prepares the kit's original interface routing.
                # A tunnel rotation changes only the public rewriting, never
                # the cached original interface addresses.
                rewritten=proxy.rewrite(copy.deepcopy(self.cached))
            self.served+=1;self.last_seconds=self.clock()-started
            return JSONResponse(rewritten,headers={'Cache-Control':'no-store'})
        except (httpx.HTTPError,ValueError,TypeError,KeyError):
            self.errors+=1;self.cached=None;self.until=0;self.retry_at=self.clock()+1;self.last_seconds=self.clock()-started
            return JSONResponse({'error':'shop discovery temporarily unavailable'},status_code=503)

    def summary(self):
        return {'served':self.served,'cache_hits':self.hits,'errors':self.errors,'last_seconds':self.last_seconds,
                'separate_http_pool':True,'metadata_ttl_seconds':10}


def install(kit):
    base=kit.Proxy
    if getattr(base,'r3_priority_discovery',False):return
    class PriorityProxy(base):
        r3_priority_discovery=True
        def __init__(self,*args,**kwargs):
            self.discovery=DiscoveryCache()
            super().__init__(*args,**kwargs)

        async def handle(self,req):
            local=urlsplit(self.upstream or '').hostname in ('127.0.0.1','localhost','::1')
            if (self.acp is not None and local and req.method=='GET'
                    and req.path_params.get('path')=='.well-known/agent-card.json'):
                return await self.discovery.card(self)
            return await super().handle(req)
    kit.Proxy=PriorityProxy
