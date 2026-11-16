"""Single-flight Buyer IPC. Children negotiate; only the parent may pay."""
import atexit
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid


class BuyerBridge:
    def __init__(self, root, stop, journal):
        self.root, self.stop, self.journal = root, stop, journal
        self.lock = threading.Lock()
        self.process = None
        self.responses = queue.Queue(maxsize=2)
        atexit.register(self.close)

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            self.stop(process)

    def start(self):
        self.close()
        self.responses = queue.Queue(maxsize=2)
        responses = self.responses
        env = dict(os.environ, R3_PERSISTENT_BUYER='1', PYTHONUTF8='1')
        process = subprocess.Popen([sys.executable, '-u', '-m', 'r3.buyer_worker'],
            cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
        self.process = process
        def read():
            try:
                for line in process.stdout:
                    if len(line) > 2_000_000:
                        break
                    try:
                        response = json.loads(line)
                    except ValueError:
                        break
                    responses.put(response, timeout=1)
            except (OSError, queue.Full):
                pass
            finally:
                try:
                    responses.put({'error': 'worker_stream_closed'}, timeout=.1)
                except queue.Full:
                    pass
        threading.Thread(target=read, name='buyer-worker-output', daemon=True).start()

    def run(self, order, shops, timeout):
        with self.lock:
            if 'mandates' in order or 'nonce' in order:
                raise ValueError('payment_material_must_stay_in_parent')
            started = time.monotonic()
            worker_error = None
            error_origin = []
            request_id = uuid.uuid4().hex
            # Reviews/prose are not candidate inputs; keep them out of worker context.
            routing_shops = [{key: row[key] for key in ('name', 'url', 'offers') if key in row} for row in shops]
            body = json.dumps({'id': request_id, 'order': order, 'shops': routing_shops}, ensure_ascii=False)
            if len(body.encode()) > 2_000_000:
                self.journal.observe('buyer_worker_error', {'order': order.get('order'),
                    'reason': 'input_budget_exceeded', 'payment_started': False}, role='buyer')
                return []  # Before dispatch; parent can submit one truthful failed result.
            try:
                if self.process is None or self.process.poll() is not None:
                    self.start()
                self.process.stdin.write(body + '\n')
                self.process.stdin.flush()
                response = self.responses.get(timeout=max(.1, timeout))
                if not isinstance(response, dict):
                    raise ValueError('buyer_worker_invalid_envelope')
                if response.get('id') != request_id or response.get('order') != order.get('order'):
                    raise ValueError('buyer_worker_response_identity_mismatch')
                if response.get('error'):
                    error = response['error']
                    if isinstance(error,str) and re.fullmatch(r'[A-Za-z_]{1,64}',error):
                        worker_error = error
                    frames = response.get('error_origin')
                    for frame in (frames if isinstance(frames,list) else [])[:3]:
                        if (isinstance(frame,dict) and isinstance(frame.get('module'),str)
                                and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',frame['module'])
                                and isinstance(frame.get('function'),str)
                                and re.fullmatch(r'[A-Za-z0-9_]{1,100}',frame['function'])
                                and type(frame.get('line')) is int):
                            error_origin.append({k:frame[k] for k in ('module','function','line')})
                    raise ValueError('buyer_worker_job_failed')
                result = response.get('result')
                if not isinstance(result, dict):
                    raise ValueError('buyer_worker_invalid_result')
                self.journal.observe('buyer_worker_execution', {'order': order['order'],
                    'seconds': time.monotonic() - started, 'request_id': request_id}, role='buyer')
                return [json.dumps(result, ensure_ascii=False)]
            except (OSError, ValueError, queue.Empty) as exc:
                self.close()
                self.journal.observe('buyer_worker_error', {'order': order.get('order'),
                    'error_type': type(exc).__name__, 'worker_error_type':worker_error,
                    'error_origin':error_origin,
                    'reason':str(exc) if type(exc) is ValueError and str(exc) in {
                        'buyer_worker_invalid_envelope','buyer_worker_response_identity_mismatch',
                        'buyer_worker_job_failed','buyer_worker_invalid_result'} else 'transport_or_worker_failure',
                    'payment_started': False}, role='buyer')
                return []  # Never accept a late/cross-order response or replay payment.
