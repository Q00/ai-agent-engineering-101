"""Pure, narrow transform; caller must never print credential-bearing input/output."""
import re


def neutral_launcher(text):
    newline = '\r\n' if '\r\n' in text else '\n'
    settings = {'BUYER_ALLIES': '', 'SHOP_ALLIES': '', 'BUYER_ALLY_RATE': '0', 'SHOP_POLICY': 'adaptive'}
    lines, seen = [], set()
    for line in text.splitlines(keepends=True):
        match = re.match(r'^\s*\$env:(BUYER_ALLIES|SHOP_ALLIES|BUYER_ALLY_RATE|SHOP_POLICY)\s*=', line, re.I)
        if match:
            name = match[1].upper()
            if name not in seen:
                lines.append(f'$env:{name} = "{settings[name]}"' + newline)
                seen.add(name)
        else:
            lines.append(line)
    # Prefix missing env controls, before any process launch (not after it).
    prefix = ''.join(f'$env:{name} = "{value}"' + newline for name, value in settings.items() if name not in seen)
    return prefix + ''.join(lines)
