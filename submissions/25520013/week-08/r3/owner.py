"""Own-shop resolution (v1.2). The market refuses to settle a buyer and a shop with the same GitHub owner, so the
buyer must never pick one of our shops and shop-side stats must not count our own buyer.

Priority (first source wins; later sources only add):
  0. v1.4 (CR v1.2 review, new issue 4): shop PUBLIC key. /api/shops rows carry name, url, pubkey, reviews and no
     owner, so the key is the only field that ties a row to us whatever its name or URL. Our keys = policy
     owner.own_shop_pubkeys (lee-sungsoo's key from the 2026-11-01 23:57 KST snapshot) + the public half of the
     kit's shop key file (R3_SHOP_KEY, else ./shop_key.pem in the hook's cwd = the agent folder) + keys of rows
     matched by name/owner (learned, cached). A row whose pubkey is ours is ours (via "pubkey").
  1. policy.json owner.own_shop_names: fixed, lower-cased name list (always applied, needs no network).
  2. GET {market}/api/shops: a shop whose owner login equals owner.github (case-insensitive) is ours, whatever
     its name. The 2026-11-01 23:57 KST response has NO owner field (keys: name, url, pubkey, reviews; see
     SCHEMA.md 8), so this step matches nothing today; it reads owner.login / owner (str) / github / owner_login
     if the market adds one.
  3. The URL of every shop found by 1 or 2, taken from that same /api/shops response (a quick-tunnel URL changes
     on every restart, so no URL is stored in policy.json).
Caching: the resolved set is kept in <R3_STATE>/own_shops.json for owner.shops_api_ttl_s seconds (per market URL).
Fallback when the API fails (network error, non-200, bad JSON, wrong shape): the last cached set of the same
market (any age, source "stale_cache"), else names only (source "policy_only"). A failure never raises.
Only GET is used.
"""
import base64
import json
import os
import re
import time
import unicodedata
import urllib.parse
from pathlib import Path

import httpx

CACHE = "own_shops.json"


def _state(state=None):
    return Path(state or os.environ.get("R3_STATE", ".r3"))


# zero-width / invisible: ZWSP, ZWNJ, ZWJ, word joiner, BOM, soft hyphen, LRM/RLM, Mongolian vowel separator
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f\u180e"), None)
# dash variants (after NFKC): hyphen, non-breaking hyphen U+2011, figure/en/em dash, bar, minus, small/fullwidth
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d\u30fc"), "-")
_GH = re.compile(r"^(?:(?:git\+)?ssh://)?(?:git@)?(?:https?://)?(?:www\.)?github\.com[:/]+([^/?#\s:]+)")


def _clean(x):
    """NFKC (fullwidth -> ASCII), invisible characters dropped, dash variants -> '-', outer spaces, lower case."""
    s = unicodedata.normalize("NFKC", str(x or "")).translate(_INVISIBLE).translate(_DASHES)
    return s.strip().lower()


def norm_name(x):
    """Name key (v1.4): _clean, then runs of separators '-', '_', '.', whitespace -> '-', outer '-' dropped
    ('Lee_Sungsoo ' == 'lee.sungsoo' == 'ｌｅｅ‑sungsoo' == 'lee-sungsoo'). GitHub forms reduce to the login:
    https/http/www github.com/<login>[/repo], git@github.com:<login>/repo.git, ssh://git@github.com/<login>.
    Another URL is a URL key (norm_url). 'leesungsoo' (no separator) stays a different name."""
    s = _clean(x)
    m = _GH.match(s)
    if m:
        s = m.group(1)
        s = s[:-4] if s.endswith(".git") else s
    elif re.match(r"^[a-z][a-z0-9+.-]*://", s):
        return norm_url(s)
    s = s.lstrip("@")
    return re.sub(r"[\s_.\-]+", "-", s).strip("-")


def norm_url(u):
    """URL key (v1.4): _clean, scheme dropped (http == https), 'www.' and default port dropped, trailing '/'
    dropped. Path is kept (market-path shops share a host)."""
    s = _clean(u)
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    host, _, path = s.partition("/")
    host = re.sub(r":(80|443)$", "", host)
    host = host[4:] if host.startswith("www.") else host
    return (host + ("/" + path if path else "")).rstrip("/")


def pubkey_of_file(path):
    """Base64 raw Ed25519 public key (the kit's format, market_kit.pub_b64) of a PEM private key file, or None.
    Only the public half is computed; nothing is printed or stored but the public key."""
    try:
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()
    except Exception:                                       # noqa: BLE001: missing / unreadable -> no key
        return None


def own_pubkeys(pol, extra=()):
    """Our shop public keys: policy owner.own_shop_pubkeys + the kit's shop key file + extra (learned)."""
    o = pol.get("owner") or {}
    keys = {str(k).strip() for k in o.get("own_shop_pubkeys") or [] if str(k).strip()}
    k = pubkey_of_file(os.environ.get("R3_SHOP_KEY") or "shop_key.pem")
    if k:
        keys.add(k)
    return keys | {k for k in extra if k}


def owner_login(shop):
    """Normalised GitHub login of a /api/shops row (owner as string, {login}, github / owner_login fields, or a
    github.com URL), '' when the row has none (today's API)."""
    o = shop.get("owner")
    if isinstance(o, list):                                 # v1.4: a list of owners -> first usable one
        o = next((x for x in o if isinstance(x, (str, dict)) and x), None)
    if isinstance(o, dict):
        o = o.get("login") or o.get("github") or o.get("name")
    for v in (o, shop.get("owner_login"), shop.get("github")):
        if isinstance(v, str) and v.strip():
            return norm_name(v)
    return ""


def fetch_shops(base, timeout=5.0):
    r = httpx.get(base.rstrip("/") + "/api/shops", timeout=timeout)
    r.raise_for_status()
    d = r.json()
    shops = d.get("shops") if isinstance(d, dict) else d
    if not isinstance(shops, list) or not all(isinstance(s, dict) for s in shops):
        raise ValueError("unexpected /api/shops shape")
    return shops


def from_shops(shops, names, github, pubkeys=()):
    """Pure steps 0+2+3: (names, urls, via) from one /api/shops list. `pubkeys` = our shop public keys."""
    names, urls, via = {norm_name(n) for n in names}, set(), {}
    github = norm_name(github)
    pubkeys = set(pubkeys)
    for s in shops:
        n = norm_name(s.get("name"))
        u = str(s.get("url") or "").strip().rstrip("/")
        if isinstance(s.get("pubkey"), str) and s["pubkey"].strip() in pubkeys:
            names.add(n)
            via[n] = "pubkey"
        elif github and owner_login(s) == github:
            names.add(n)
            via[n] = "owner"
        elif n in names:
            via[n] = "name"
        else:
            continue
        if u:
            urls.add(u)
    return names, urls, via


def _read(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resolve(pol, base=None, state=None, now=None, fetch=None):
    """-> {"names": set, "urls": set, "source": api|cache|stale_cache|policy_only, "t", "base", "via", "error"}."""
    o = pol.get("owner") or {}
    github = norm_name(o.get("github"))
    names = {norm_name(n) for n in o.get("own_shop_names") or []} - {""}
    ttl = float(o.get("shops_api_ttl_s", 300))
    now = time.time() if now is None else now
    base = (base if base is not None else os.environ.get("R3_MARKET") or "").rstrip("/")
    p = _state(state) / CACHE
    c = _read(p)
    if c and c.get("base") != base:
        c = None

    def out(src, names_, urls, t, via, err=None, pubkeys=None, mine=None):
        return {"names": set(names_) | names, "urls": set(urls), "source": src, "t": t, "base": base,
                "via": via, "error": err, "pubkeys": dict(pubkeys or {}),
                "own_pubkeys": set(mine) if mine is not None else own_pubkeys(pol)}

    if not base:
        return out("policy_only", names, (), now, {n: "name" for n in names}, "no market url")
    if c and 0 <= now - c.get("t", 0) < ttl:
        return out("cache", c["names"], c["urls"], c["t"], c.get("via", {}), pubkeys=c.get("pubkeys"),
                   mine=own_pubkeys(pol, c.get("own_pubkeys") or ()))
    try:
        shops = (fetch or (lambda b: fetch_shops(b, float(o.get("shops_api_timeout_s", 5)))))(base)
        mine = own_pubkeys(pol, (c or {}).get("own_pubkeys") or ())
        n2, urls, via = from_shops(shops, names, github, mine)
        keys = {norm_url(s.get("url")): s["pubkey"] for s in shops
                if isinstance(s, dict) and s.get("url") and isinstance(s.get("pubkey"), str)}
        nu = {norm_url(u) for u in urls}
        mine |= {k for u, k in keys.items() if u in nu}          # learned: keys of rows matched by name/owner
    except Exception as e:                                  # noqa: BLE001: any failure falls back, never raises
        err = f"{type(e).__name__}: {str(e)[:120]}"
        if c:
            return out("stale_cache", c["names"], c["urls"], c["t"], c.get("via", {}), err, c.get("pubkeys"),
                       own_pubkeys(pol, c.get("own_pubkeys") or ()))
        return out("policy_only", names, (), now, {n: "name" for n in names}, err)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"t": now, "base": base, "names": sorted(n2), "urls": sorted(urls), "via": via,
                               "shops_seen": len(shops), "pubkeys": keys,
                               "own_pubkeys": sorted(mine)}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return out("api", n2, urls, now, via, pubkeys=keys, mine=mine)


def cached(pol, state=None):
    """Names + URLs without any network (for the ledger): policy names plus the last cache of any market."""
    o = pol.get("owner") or {}
    names = {norm_name(n) for n in o.get("own_shop_names") or []} - {""}
    c = _read(_state(state) / CACHE) or {}
    return names | set(c.get("names") or []), set(c.get("urls") or [])


def own_shop_keys(pol, state=None, extra_names=(), market=None):
    """v1.10 (QA review_r3_01ac_v19 5-4): keys that identify OUR shop in a market row's `shop` field, no network.
    {"names": norm_name of policy own_shop_names + cached names (/api/shops matches by name/owner/pubkey) +
    extra_names (the running shop's R3_SHOP_NAME), "urls": norm_url of the cached URLs, "pubkeys": policy +
    cache + kit key file, "market_host": norm_url host of R3_MARKET (for <market>/shop/<name> paths)}."""
    o = pol.get("owner") or {}
    names, urls = cached(pol, state)
    names = {norm_name(n) for n in names} | {norm_name(n) for n in extra_names if n}
    c = _read(_state(state) / CACHE) or {}
    base = market if market is not None else os.environ.get("R3_MARKET") or ""
    return {"names": names - {""}, "urls": {norm_url(u) for u in urls} - {""},
            "pubkeys": own_pubkeys({"owner": o}, c.get("own_pubkeys") or ()),
            "market_host": norm_url(base).partition("/")[0] if base else ""}


def is_own_shop_ref(value, keys):
    """True when a market row's shop field (a name, a URL, or a public key) is one of OUR shops (own_shop_keys).
    Names compare after norm_name ('LSS Shop' == 'lss_shop'; 'lss-annex' differs); URLs after norm_url; a URL
    <market host>/shop/<name> (market-hosted shops, Round 2 result rows) compares its <name> (v1.11: percent-decoded first, so /shop/LSS%20Shop == 'LSS Shop'); a bare public key
    compares exactly. Unknown / empty -> False."""
    if not isinstance(value, str) or not value.strip():
        return False
    v = value.strip()
    if v in keys.get("pubkeys", ()):
        return True
    n = norm_name(v)
    if n in keys.get("names", ()) or n in keys.get("urls", ()):
        return True
    host, _, path = norm_url(v).partition("/")
    if path and keys.get("market_host") and host == keys["market_host"]:
        m = re.match(r"^shop/([^/?#]+)$", path)
        if m and norm_name(urllib.parse.unquote(m.group(1))) in keys.get("names", ()):   # v1.11: %20 etc.
            return True
    return False
