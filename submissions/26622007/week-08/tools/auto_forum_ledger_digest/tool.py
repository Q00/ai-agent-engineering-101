"""Join untrusted forum references to cached ledger facts, without endorsing claims."""
import json
import math
import os
import sys
import market_model


def digest(forum, rows, limit=20):
    limit = max(1, min(100, int(limit)))
    if isinstance(forum, dict):
        forum = forum.get('posts', forum.get('items', []))
    forum = forum if isinstance(forum, list) else []
    def stamp(row):
        value = row.get('ts')
        return value if isinstance(value, (int, float)) and math.isfinite(value) else -1
    posts = sorted((p for p in forum if isinstance(p, dict)), key=stamp, reverse=True)[:limit]
    # Only integer ledger identities can be joined. Never trust embedded refs.data/found.
    by_seq = {r['seq']: r for r in rows if isinstance(r, dict)
              and type(r.get('seq')) is int}
    paid = sorted((r for r in by_seq.values() if r.get('k') == 'settle'), key=stamp, reverse=True)[:limit]
    evidence = []
    for post in posts:
        refs = post.get('refs', [])
        if not isinstance(refs, list):
            refs = []
        seen = set()
        for ref in refs[:100]:
            seq = ref.get('seq') if isinstance(ref, dict) else ref
            if type(seq) is not int or seq in seen:
                continue
            seen.add(seq)
            row = by_seq.get(seq)
            evidence.append({'post': post.get('id'), 'seq': seq,
                             'status': 'not_in_cache' if row is None else
                             'paid' if row.get('k') == 'settle' else 'non_payment',
                             'ledger': row})
    return {'basis': 'unique paid recent rows; references joined by seq before recent limit; '
                     'forum claims and embedded refs are untrusted; a matching row does not verify '
                     'the post wording, a break, or a refund; not_in_cache is not falsehood',
            'reference_evidence': evidence,
            'forum_posts': [{k: v for k, v in post.items() if k != 'refs'} for post in posts],
            'ledger_rows': paid}


def main():
    args = json.load(sys.stdin)
    md = os.environ['MARKET_DIR']
    try:
        with open(os.path.join(md, 'forum.json'), encoding='utf-8') as f:
            forum = json.load(f)
    except (OSError, ValueError):
        forum = []
    # only the rows digest can use: the settle rows and the rows a post names by seq (not the 2.4 M rows)
    posts = forum.get('posts', forum.get('items', [])) if isinstance(forum, dict) else forum
    named = set()
    for p in posts if isinstance(posts, list) else []:
        for ref in p.get('refs')[:100] if isinstance(p, dict) and isinstance(p.get('refs'), list) else []:
            seq = ref.get('seq') if isinstance(ref, dict) else ref
            if type(seq) is int:            # other participants' posts: a list or a dict here is no seq
                named.add(seq)
    rows = market_model.load_rows_where(md, lambda r: r.get('k') == 'settle' or r.get('seq') in named)
    print(json.dumps(digest(forum, rows, args.get('limit', 20)), ensure_ascii=False))


if __name__ == '__main__':
    main()
