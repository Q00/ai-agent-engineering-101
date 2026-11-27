"""Availability notice execution; policy calculation remains in cash_policy."""
import json

import seller_cash_policy as cash_policy
from store import queue_forum_action


def queue_availability_notice(market_dir, active, hub, catalog, now, *,
                              availability_plan=None, queue_action=None):
    """Queue under the caller's hub lock, then record only successful notices.

    Callables may be supplied to preserve the caller's live monkeypatch boundary.
    The caller remains responsible for checking inventory freshness.
    """
    if availability_plan is None:
        availability_plan = cash_policy.availability_plan
    if queue_action is None:
        queue_action = queue_forum_action
    try:
        feed_path=market_dir/'forum.json'
        feed=json.loads(feed_path.read_text('utf-8')) if 0<=now-feed_path.stat().st_mtime<=120 else []
    except (OSError,ValueError):
        feed=[]
    post=availability_plan(active,hub,catalog,now,feed)
    if post:
        # The outbox append must succeed before reserving this notice's slot.
        queued=queue_action(market_dir,post['action'])
        if queued:
            fs=hub.setdefault('forum_state',{})
            fs['last_shop_post_ts']=int(now)
            announced=fs.setdefault('availability_announced',{})
            announced.update({s:post['demand_ts'] for s in post['skus']})
            fs['availability_announced']=dict(sorted(announced.items(),key=lambda x:x[1])[-500:])
            if post['thread']:
                fs['availability_replied_threads']=(fs.get('availability_replied_threads',[])+[post['thread']])[-300:]
