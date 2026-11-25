"""One HTTP session per worker. Reuse connections, never prior-response cookies."""
import threading
import httpx

class JevConnectionPool:
    def __init__(self, factory=None):
        self.factory=factory or httpx.Client
        self.local=threading.local()
        self.lock=threading.Lock()
        self.clients=[]

    def post(self, *args, **kwargs):
        client=getattr(self.local,"client",None)
        if client is None:
            client=self.factory()
            self.local.client=client
            with self.lock:self.clients.append(client)
        # Baseline httpx.post starts with an empty cookie jar on every call.
        client.cookies.clear()
        return client.post(*args,**kwargs)

    def close(self):
        with self.lock:
            clients,self.clients=self.clients,[]
        for client in clients:
            client.close()
