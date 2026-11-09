"""Read-only, bounded trace of guard failures and the matching peer replies."""
import json
from datetime import datetime, timezone
from pathlib import Path
from r3.state import Journal
from r3.protocol import quote_terms


def main():
    j = Journal()
    since = j.get('buyer_receipt_diagnostics_since', 0)
    guards = [r for r in j.events('buyer_receipt_guard', 100) if r['_ts'] >= since]
    orders = {r['order'] for r in guards}
    wires = [r for r in j.events('buyer_quote_wire', 2000) if r.get('order') in orders]
    executions = [r for r in j.events('buyer_execution', 200)
                  if r.get('contract_revision') == 'contract-memory-v1']
    patched_orders = {r['order'] for r in executions}
    payload = {'captured_at': datetime.now(timezone.utc).isoformat(), 'since': since,
               'guards': guards, 'wires': wires, 'contract_memory_executions': executions,
               'patched_attempts': [r for r in j.events('attempt', 300) if r.get('order') in patched_orders],
               'patched_purchases': [r for r in j.events('purchase', 300) if r.get('order') in patched_orders]}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = Path('logs') / f'contract-strategy-trace-{stamp}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'source': str(path), 'guards': len(guards),
                      'negotiation_replies': sum(r.get('stage') == 'negotiation' for r in wires),
                      'patched_orders': len(executions),
                      'patched_purchases': len(payload['patched_purchases'])}))
    for guard in guards[:6]:
        matching = sorted((r for r in wires if r.get('task') == guard.get('expected_task')),
                          key=lambda r: r['_ts'])
        parsed = []
        for row in matching:
            try:
                terms = quote_terms({'metadata': row.get('metadata', {}),
                                     'status': {'message': row.get('message', {})}})
                terms.pop('declarations_require_checkout_verification', None)
            except ValueError as exc:
                terms = {'error': str(exc)}
            parsed.append({'stage': row['stage'], 'terms': terms})
        print(json.dumps({'guard': guard, 'quote_sequence': parsed}, ensure_ascii=False))


if __name__ == '__main__':
    main()
