"""Bounded rolling-memory buyer, also usable by the concurrent shopping runner."""
import argparse
import time

from core import BUYER_PROMPT, decide, emit, last_price, validate_buyer
from wire import Wire, receipt, task_text


def negotiate(url, item, budget, run_id='single', decision=decide):
    if type(budget) is not int or budget < 0:
        raise ValueError('Invalid fixed budget')
    deadline = time.monotonic() + 270
    context = dict(run_id=run_id, item=item)
    emit('buyer_start', **context, url=url, budget=budget)
    wire = Wire(url, deadline, context)
    try:
        card = wire.discover()
        task = wire.send('I want to buy a ' + item)
        task_id = task['id']
        memory, recent, last_offer = '', [], None
        for turn in range(9):
            if task['id'] != task_id:
                raise ValueError('Seller changed task ID')
            status = task['status']['state']
            if status == 'TASK_STATE_COMPLETED':
                result = dict(outcome='bought', receipt=receipt(task, item, budget, last_offer), task_id=task_id)
                break
            if status in ('TASK_STATE_REJECTED', 'TASK_STATE_CANCELED'):
                result = dict(outcome='no_deal', terminal=status, task_id=task_id)
                break
            if status != 'TASK_STATE_INPUT_REQUIRED':
                raise ValueError('Unexpected task state: ' + status)
            if turn == 8:
                wire.cancel(task_id)
                result = dict(outcome='limit', task_id=task_id)
                break
            text = task_text(task)
            state = dict(item=item, budget=budget, task_id=task_id, current_ask=last_price(text),
                         seller_text=text[:3000], last_offer=last_offer, rounds=turn,
                         memory=memory, recent=recent, seller_name=card['name'])
            action = decision(BUYER_PROMPT, state, lambda a: validate_buyer(a, budget),
                              'buyer', deadline, dict(context, task_id=task_id))
            memory = action['summary']
            emit('buyer_action', **context, task_id=task_id, action=action)
            if action['action'] == 'walk':
                canceled = wire.cancel(task_id)
                if canceled['status']['state'] != 'TASK_STATE_CANCELED':
                    raise ValueError('Cancellation not acknowledged')
                result = dict(outcome='walked', task_id=task_id)
                break
            last_offer = action['price']
            recent = (recent + [dict(seller=text[:500], offer=last_offer)])[-2:]
            task = wire.send(str(last_offer), task_id)
        emit('buyer_final', **context, budget=budget, **result)
        return result
    except Exception as exc:
        emit('buyer_error', **context, kind=type(exc).__name__, error=str(exc))
        raise
    finally:
        wire.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--item', required=True)
    parser.add_argument('--budget', required=True, type=int)
    args = parser.parse_args()
    negotiate(args.url, args.item, args.budget)
