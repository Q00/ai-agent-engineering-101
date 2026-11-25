import json
import os
import sys

import profit_cohorts


def main():
    args = json.load(sys.stdin)
    root = os.path.dirname(os.path.abspath(os.environ['MARKET_DIR']))
    start = args.get('start', 0)
    if not isinstance(start, (int, float)) or isinstance(start, bool) or start < 0:
        raise ValueError('start must be a nonnegative Unix timestamp')
    out = profit_cohorts.build(root, start=start)
    if args.get('sku'):
        out['skus'] = {args['sku']: out['skus'].get(args['sku'])}
    else:
        out['skus'] = dict(sorted(out['skus'].items(),
                                 key=lambda kv: -kv[1]['all']['paid'])[:5])
    print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    main()
