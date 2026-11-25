"""Separate optional allocation ceilings from actual obligations and market law."""


def capital_caps(config):return config.get('shop_internal_capital_caps_enabled',True)


def fraction(config,key,default):
    return config.get(key,default) if capital_caps(config) else 1.
