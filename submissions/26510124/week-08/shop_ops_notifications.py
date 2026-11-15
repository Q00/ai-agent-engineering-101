"""Operator-only durable outbox. No market forum or external service is called."""
import uuid
from shop_ops_policy import immutable_record


class LocalOutbox:
    def __init__(self, root):
        self.root = root

    def notify(self, *, request_id, decision_id, problem, evidence, action, review_at):
        message = {'schema': 1, 'notification_id': uuid.uuid4().hex, 'request_id': request_id,
                   'decision_id': decision_id, 'problem': problem, 'evidence': evidence,
                   'action': action, 'review_at': review_at, 'delivery': 'local_only'}
        immutable_record(self.root, 'outbox', message['notification_id'], message)
        return message


def deliver(message, adapter):
    """Explicit caller-controlled transport adapter. Never run by the daemon.

    Adapter.send(message) can later implement an authorized channel. A delivery
    error has no connection to policy application or transaction processing.
    """
    return adapter.send(dict(message))
