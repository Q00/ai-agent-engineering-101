"""Pure policy tests, no orders or model calls."""
import ast
from pathlib import Path
source = ast.parse(Path(__file__).with_name('market_buyer.py').read_text())
selected = [n for n in source.body if
    isinstance(n, ast.FunctionDef) and n.name in {'experimental_offer', 'experimental_review'}
    or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {'EXPERIMENT_RATE','REFERENCE_PRICES'} for t in n.targets)]
ns = {}
exec(compile(ast.Module(body=selected, type_ignores=[]), '<policy>', 'exec'), ns)
o, r = ns['experimental_offer'], ns['experimental_review']
assert o('lamp', 140, .0999) == 125
assert o('lamp', 140, .10) is None
assert o('lamp', 110, .01) == 110
assert o('lamp', 100, .01) is None
assert o('unknown', 500, .01) is None
assert r({'price':125}, [125], 125, 'lamp', 140)[0] == 2
assert 'I chose a higher offer' in r({'price':125}, [125], 125, 'lamp', 140)[1]
assert r({'price':100}, [125], 125, 'lamp', 140) is None
assert r({'price':150}, [125], 125, 'lamp', 140) is None
assert r({'price':125}, [125], None, 'lamp', 140) is None
assert r(None, [125], 125, 'lamp', 140) is None
assert r({'price':125}, [], 125, 'lamp', 140) is None
print('PASS 10% boundary, budget cap, reference-price guard, experiment disclosure, no-deal and receipt-error preservation')
