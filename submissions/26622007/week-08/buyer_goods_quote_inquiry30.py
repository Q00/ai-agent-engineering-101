"""Finite goods opening-inquiry strategy. A request is neither ACK nor payment."""
import hashlib
import json
import os
import threading
import time

from buyer_goods_risk_sku30 import arm, ident, number

SWITCH = 'buyer_goods_quote_inquiry30.json'
KEY = '_goods_quote_inquiry30'
PREFIX = '.goods-quote-inquiry30-'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def config(md, now=None, *, strict_io=False):
    now = time.time() if now is None else now
    try:
        with open(os.path.join(md,SWITCH),encoding='utf-8') as src:
            raw=src.read(16385);mtime=os.fstat(src.fileno()).st_mtime
        c=json.loads(raw)
        if (len(raw)>16384 or not isinstance(c,dict) or c.get('enabled') is not True or
                not all(ident(c.get(k)) for k in ('study','salt')) or
                not all(number(v) for v in (now,c.get('start'),c.get('end'))) or
                not 0<c['start']<c['end']==c['start']+1800 or not mtime<=c['start']<=now<c['end']):
            return None
        return {k:c[k] for k in ('study','salt','start','end')} | {'sha256':hashlib.sha256(raw.encode()).hexdigest()}
    except FileNotFoundError:
        return None
    except OSError:
        if strict_io:
            raise
        return None
    except (ValueError,TypeError,RecursionError):
        return None


def switch_on(md, *, study, salt, start, end):
    """Operator-only helper, exclusive future file creation; never called by buyer."""
    now=time.time()
    if not (ident(study) and ident(salt) and all(number(v) for v in (now,start,end)) and
            0<now<start<end==start+1800):
        raise ValueError('future exact 1800-second window required')
    value=dict(enabled=True,study=study,salt=salt,start=start,end=end)
    with open(os.path.join(md,SWITCH),'x',encoding='utf-8') as dst:
        json.dump(value,dst,allow_nan=False);dst.flush();os.fsync(dst.fileno())
    return value


def _unknown(ctx):
    ctx['note']['trace']='unknown';ctx['disabled']=True


def assign(md, order, now=None):
    """ITT before selection, including early give-up. Missing switch costs no writes."""
    if not isinstance(order,dict) or order.get('kind')!='goods' or KEY in order:
        return
    now=time.time() if now is None else now
    c=config(md,now);started=order.get('_buyer_started_at');oid=order.get('order')
    if c is None or not ident(oid) or not number(started) or not c['start']<=started<=now<c['end']:
        return
    ref=digest([c['study'],oid]);path=os.path.join(md,PREFIX+ref)
    ctx=dict(md=md,cfg=c,oid=oid,started=started,path=path,lock=threading.Lock(),tickets={},
             note=dict(study=c['study'],arm=arm(c,oid),applied=False,ref=ref))
    order[KEY]=ctx
    value=dict(config=c['sha256'],order=digest(oid),started=started,arm=ctx['note']['arm'])
    try:
        os.mkdir(path+'.seen')
        with open(path+'.json','x',encoding='utf-8') as dst:
            json.dump(value,dst,sort_keys=True,separators=(',',':'));dst.flush();os.fsync(dst.fileno())
        ctx['assignment']=digest(value)
    except (OSError,ValueError,TypeError,RecursionError):
        _unknown(ctx)


def fields(order):
    ctx=order.get(KEY) if isinstance(order,dict) else None
    if not isinstance(ctx,dict) or not isinstance(ctx.get('note'),dict):
        return {}
    with ctx['lock']:
        return {'goods_quote_inquiry30':dict(ctx['note'])}


def applied_b(row):
    n=row.get('goods_quote_inquiry30') if isinstance(row,dict) else None
    return isinstance(n,dict) and n.get('arm')=='B' and n.get('applied') is True


def _active(ctx, order):
    if (ctx.get('disabled') or ctx['note']['arm']!='B' or order.get('kind')!='goods' or
            order.get('order')!=ctx['oid'] or order.get('_buyer_started_at')!=ctx['started'] or
            config(ctx['md'],strict_io=True)!=ctx['cfg']):
        return False
    with open(ctx['path']+'.json',encoding='utf-8') as src:
        return hashlib.sha256(src.read(16385).encode()).hexdigest()==ctx['assignment']


def _source(order):
    path=order.get('_stock_shops_path')
    if not path:
        return None
    with open(path,'rb') as src:
        data=src.read(4_000_001)
    if len(data)>4_000_000:
        raise ValueError('listing too large')
    return hashlib.sha256(data).hexdigest()


def _shape(shop, offer, budget):
    from urllib.parse import urlsplit
    url=shop.get('url');u=urlsplit(url) if isinstance(url,str) else None
    offers=shop.get('usable')
    if (not isinstance(shop.get('name'),str) or not shop['name'] or u is None or
            u.scheme not in ('http','https') or not u.hostname or u.username or u.password or
            not isinstance(offers,list) or not 0<len(offers)<=512 or offer not in offers or
            not all(isinstance(o,dict) and ident(o.get('sku')) for o in offers) or
            len({o['sku'] for o in offers})!=len(offers) or type(budget) is not int or budget<=0 or
            type(offer.get('list_price')) is not int or offer['list_price']<=0 or
            not number(shop.get('_b')) or not 0<=shop['_b']<=1):
        raise ValueError('invalid current offer')
    return digest([shop['name'],url,offers,offer,budget,shop['_b']])


def plan(order, pool, k, budget, selected):
    """Freeze the existing filtered pool without changing its membership or order."""
    ctx=order.get(KEY)
    if not isinstance(ctx,dict) or ctx.get('pool') is not None:
        return
    try:
        if not _active(ctx,order):
            return
        if len({s['name'] for s in pool})!=len(pool) or len({s['url'] for s in pool})!=len(pool):
            raise ValueError('ambiguous pool')
        for s in pool:
            _shape(dict(s,_b=0),selected(s,budget),budget)
        fingerprint=digest([[[s['name'],s['url'],s['usable'],selected(s,budget)] for s in pool],order.get('_sw')])
        ctx.update(pool=list(pool),pool_hash=fingerprint,source=_source(order),k=k,budget=budget,selected=selected)
    except (OSError,ValueError,TypeError,KeyError,OverflowError,RecursionError):
        _unknown(ctx)


def _current_pool(ctx, order):
    return ('pool' in ctx and _source(order)==ctx['source'] and
        order.get('_stock_k')==ctx['k'] and
        digest([[[s['name'],s['url'],s['usable'],ctx['selected'](s,ctx['budget'])] for s in ctx['pool']],order.get('_sw')])==ctx['pool_hash'])


def prepare(order, shop, offer, budget, cap, conflict):
    """Freeze only; called after stock reset/canonical SKU and before Thread.start."""
    ctx=order.get(KEY)
    if not isinstance(ctx,dict):
        return
    try:
        if not _active(ctx,order) or conflict() or not _current_pool(ctx,order):
            return
        ceiling=cap()
        if type(ceiling) is not int or not 0<ceiling<=min(budget,offer['list_price']):
            return
        if budget!=ctx['budget'] or not any(s is shop for s in ctx['pool']):
            return
        fingerprint=_shape(shop,offer,budget)
        value=dict(assignment=ctx['assignment'],pool=ctx['pool_hash'],fingerprint=fingerprint,source=ctx['source'],cap=ceiling)
        path=ctx['path']+'.'+digest(shop['name'])+'.plan.json'
        with open(path,'x',encoding='utf-8') as dst:
            json.dump(value,dst,sort_keys=True,separators=(',',':'));dst.flush();os.fsync(dst.fileno())
        ctx['tickets'][shop['name']]=dict(shop=shop,offer=offer,budget=budget,cap=cap,conflict=conflict,
            value=value,path=path,hash=digest(value),used=False)
    except (OSError,ValueError,TypeError,KeyError,OverflowError,RecursionError):
        _unknown(ctx)


def send(order, shop, original, rpc_url, sender, ctl=None):
    """Revalidate immediately at the existing first request invocation, never accepts.

    applied counts a changed sender invocation, even a failed/no-response request;
    the existing quote/task/settlement fields separately describe ACK and payment.
    """
    ctx=order.get(KEY);request=original
    if isinstance(ctx,dict):
        ticket=ctx['tickets'].get(shop.get('name'))
        if ticket is not None:
            try:
                with open(ticket['path'],encoding='utf-8') as src:
                    unchanged=hashlib.sha256(src.read(16385).encode()).hexdigest()==ticket['hash']
                valid=(_current_pool(ctx,order) and unchanged and not ticket['conflict']() and
                    ticket['shop'] is shop and ticket['offer'] is shop.get('_offer') and
                    _shape(shop,shop['_offer'],ticket['budget'])==ticket['value']['fingerprint'] and
                    _source(order)==ticket['value']['source'] and
                    ticket['cap']()==ticket['value']['cap'] and _active(ctx,order))
                with ctx['lock']:
                    # Validation IO/callbacks and lock acquisition may cross either cutoff.
                    now=time.time()
                    qd=order.get('_quote_deadline')
                    deadline=qd.get('at') if isinstance(qd,dict) else ctx['started']+13
                    if (valid and not ticket['used'] and number(now) and number(deadline) and
                            ctx['started']<=now<min(deadline,ctx['started']+13,ctx['cfg']['end']) and
                            not (ctl and ctl.get('done'))):
                        ticket['used']=True
                        request=(original+f". Quote inquiry only: please quote your best price at most {ticket['value']['cap']} "
                            "for this exact SKU and warranty. This is not an offer or acceptance; do not complete a purchase.")
                        ctx['note']['applied']=True
                        ctx['note']['requests']=ctx['note'].get('requests',0)+1
            except (OSError,ValueError,TypeError,KeyError,OverflowError,RecursionError):
                _unknown(ctx)
    return sender(rpc_url,request)
