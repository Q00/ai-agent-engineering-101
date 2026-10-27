"""Small JSON-RPC client; all business decisions remain outside this module."""
import math
import ipaddress
import time
from urllib.parse import urlparse
import uuid

import httpx

from core import emit

HEADERS = {'A2A-Version': '1.0'}


def local_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.username:
        raise ValueError('This experiment only connects to local HTTP shops')
    return parsed


def market_url(url):
    parsed = urlparse(url)
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query:
        raise ValueError('Invalid shop URL')
    if parsed.scheme == 'http':
        return local_url(url)
    if parsed.scheme != 'https':
        raise ValueError('Public shops require HTTPS')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('Non-public HTTPS address')
    if parsed.hostname == 'localhost' or parsed.hostname.endswith('.localhost'):
        raise ValueError('Use loopback HTTP for local tests')
    return parsed


def origin(parsed):
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


class Wire:
    def __init__(self, url, deadline, context=None, allow_remote=False):
        self.check_url = market_url if allow_remote else local_url
        self.check_url(url)
        self.url = url.rstrip('/') + '/'
        self.deadline = deadline
        self.context = context or {}
        self.client = httpx.Client(headers=HEADERS, trust_env=False)

    def close(self):
        self.client.close()

    def request(self, method, url, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('buyer deadline exhausted')
        emit('http_request', **self.context, method=method, url=url, headers=HEADERS,
             body=kwargs.get('json'))
        response = self.client.request(method, url, timeout=min(180, remaining), **kwargs)
        emit('http_response', **self.context, status=response.status_code, raw=response.text)
        response.raise_for_status()
        return response.json()

    def discover(self):
        card = self.request('GET', self.url + '.well-known/agent-card.json')
        interfaces = [i for i in card.get('supportedInterfaces', [])
                      if i.get('protocolBinding') == 'JSONRPC' and i.get('protocolVersion') == '1.0']
        if not interfaces or not card.get('skills'):
            raise ValueError('Missing JSONRPC 1.0 interface or skills')
        endpoint = interfaces[0]['url']
        old, new = self.check_url(self.url), self.check_url(endpoint)
        if origin(old) != origin(new):
            raise ValueError('AgentCard changed the shop origin')
        self.url = endpoint
        return card

    def rpc(self, method, params):
        request_id = uuid.uuid4().hex
        response = self.request('POST', self.url, json=dict(jsonrpc='2.0', id=request_id,
                                                           method=method, params=params))
        if response.get('id') != request_id or response.get('jsonrpc') != '2.0':
            raise ValueError('Mismatched JSON-RPC response')
        if 'error' in response:
            raise RuntimeError('A2A error: ' + str(response['error']))
        result = response['result']
        return result.get('task', result)

    def send(self, text, task_id=None):
        message = dict(role='ROLE_USER', messageId=uuid.uuid4().hex, parts=[dict(text=text)])
        if task_id is not None:
            message['taskId'] = task_id
        return self.rpc('SendMessage', dict(message=message))

    def cancel(self, task_id):
        return self.rpc('CancelTask', dict(id=task_id))


def task_text(task):
    return '\n'.join(p.get('text', '') for p in task.get('status', {}).get('message', {}).get('parts', []))


class ReceiptError(ValueError):
    def __init__(self, message, received=None):
        super().__init__(message)
        self.received = received


def receipt(task, item, budget, last_offer):
    receipts = [p['data'] for a in task.get('artifacts', []) for p in a.get('parts', []) if 'data' in p]
    if len(receipts) != 1:
        raise ReceiptError('Seller marked completed but supplied %d receipt data Parts; expected exactly one.' % len(receipts))
    result = receipts[0]
    if not isinstance(result, dict):
        raise ReceiptError('Receipt data is not an object.', result)
    price = result.get('price')
    problems = []
    if type(price) not in (int, float) or not math.isfinite(price) or price < 0 or price != int(price):
        problems.append('Receipt has an invalid whole-credit price.')
    else:
        if price > budget:
            problems.append('Receipt price %s exceeds budget %s.' % (price, budget))
        if last_offer is None or price != last_offer:
            problems.append('Receipt price %s differs from my last offer %s.' % (price, last_offer))
    if result.get('item') != item:
        problems.append('Receipt item %r differs from requested %r.' % (result.get('item'), item))
    if 'task' in result and result['task'] != task.get('id'):
        problems.append('Receipt task differs from the negotiation task.')
    if problems:
        raise ReceiptError(' '.join(problems), result)
    return result
