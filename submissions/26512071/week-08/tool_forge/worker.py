"""Tool Forge worker: a trusted, manual interpreter for a tiny Python subset.

Runs as a child process (python -I -S worker.py). Protocol: one JSON request
on stdin, one JSON response on stdout.

    {"op": "test",   "name": n, "code": c, "test": t}  -> {"ok": true}
    {"op": "invoke", "name": n, "code": c, "args": [...]} -> {"ok": true, "value": v}
    any failure                                          -> {"ok": false, "error": "<code>"}

Generated code is never exec'd, eval'd, compiled to bytecode or imported.
ast.parse only builds a syntax tree; this module validates that tree against
an allow-list and walks it by hand. A separate process is NOT an OS sandbox.

Accepted tool code: exactly one `def <name>(a, b, ...)` with plain positional
args and a body of exactly one `return <expr>`. Expressions may use constants,
the args, + - * / // % ** (bounded), unary - + not, and/or, comparisons,
if-expressions, list/dict literals, subscripts, and abs/len/max/min/sorted/sum.
Everything else (imports, attributes, loops, comprehensions, lambdas, slices,
calls to anything else, recursion) is denied.

Accepted test code: one or more `assert <expr>` statements; the expressions
use the same subset and may call the tool by name.
"""
import ast
import json
import keyword
import math
import re
import sys

MAX_SOURCE_BYTES = 4096
MAX_TEST_BYTES = 4096
MAX_REQUEST_BYTES = 32768
MAX_CODE_NODES = 200
MAX_TEST_NODES = 400
MAX_AST_DEPTH = 32
MAX_ARGS = 8
MAX_ASSERTS = 32
MAX_STEPS = 5000          # deterministic evaluation budget per request
MAX_INT_BITS = 256
MAX_STR_LEN = 1024
MAX_ITEMS = 256           # total container elements inside one value
MAX_VALUE_DEPTH = 8
MAX_VALUE_BYTES = 16384   # upper bound on the JSON encoding of one value

NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,47}", re.ASCII)
RESERVED_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)
BUILTINS = frozenset(["abs", "len", "max", "min", "sorted", "sum"])

BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
UNARYOPS = (ast.UAdd, ast.USub, ast.Not)
CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)


class ForgeError(Exception):
    """A typed, fixed-vocabulary failure code (never echoes untrusted text)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- names/values

def check_identifier(name, code):
    soft = getattr(keyword, "issoftkeyword", lambda _: False)
    if (type(name) is not str or not NAME_RE.fullmatch(name)
            or keyword.iskeyword(name) or soft(name)
            or name.upper() in RESERVED_DEVICE_NAMES or name in BUILTINS):
        raise ForgeError(code)
    return name


def check_value(value):
    """Raise ForgeError unless value is strict, finite, bounded JSON data.

    Runs before any json.dumps so serialization size is bounded up front.
    """
    state = [0, 0]  # container items seen, estimated JSON bytes
    _check(value, 0, state)
    return value


def _check(v, depth, state):
    if depth > MAX_VALUE_DEPTH:
        raise ForgeError("value:too_deep")
    t = type(v)
    if v is None or t is bool:
        cost = 5
    elif t is int:
        if v.bit_length() > MAX_INT_BITS:
            raise ForgeError("value:int_too_large")
        cost = 80  # 256 bits is at most 78 digits plus sign
    elif t is float:
        if not math.isfinite(v):
            raise ForgeError("value:float_not_finite")
        cost = 32
    elif t is str:
        if len(v) > MAX_STR_LEN:
            raise ForgeError("value:str_too_long")
        cost = 12 * len(v) + 2  # worst case: surrogate-pair \uXXXX escapes
    elif t is list or t is dict:
        cost = 2
        pairs = v.items() if t is dict else ((None, item) for item in v)
        for key, item in pairs:
            state[0] += 1
            if state[0] > MAX_ITEMS:
                raise ForgeError("value:too_many_items")
            if t is dict:
                if type(key) is not str:
                    raise ForgeError("value:dict_key_not_str")
                _check(key, depth + 1, state)
            _check(item, depth + 1, state)
            cost += 4  # separators
    else:
        raise ForgeError("value:unsupported_type")
    state[1] += cost
    if state[1] > MAX_VALUE_BYTES:
        raise ForgeError("value:too_large")


# ------------------------------------------------------------ static checking

def _parse(source, max_bytes, max_nodes, label):
    if type(source) is not str:
        raise ForgeError(label + ":not_text")
    try:
        size = len(source.encode("utf-8"))
    except UnicodeEncodeError:
        raise ForgeError(label + ":encoding")
    if size > max_bytes:
        raise ForgeError(label + ":too_many_bytes")
    if "\x00" in source:
        raise ForgeError(label + ":nul_byte")
    try:
        tree = ast.parse(source, mode="exec")  # syntax tree only, no bytecode
    except SyntaxError:
        raise ForgeError(label + ":syntax_error")
    except (ValueError, RecursionError, MemoryError):
        raise ForgeError(label + ":parse_rejected")
    count = 0
    for _ in ast.walk(tree):
        count += 1
        if count > max_nodes:
            raise ForgeError(label + ":too_many_nodes")
    return tree


def _validate_expr(node, names, callables, depth):
    if depth > MAX_AST_DEPTH:
        raise ForgeError("ast:too_deep")
    d = depth + 1
    t = type(node)
    if t is ast.Constant:
        if type(node.value) not in (int, float, str, bool, type(None)):
            raise ForgeError("ast:constant_type_denied")
        check_value(node.value)
    elif t is ast.Name:
        if type(node.ctx) is not ast.Load or node.id not in names:
            raise ForgeError("ast:name_denied")
    elif t is ast.BinOp:
        if not isinstance(node.op, BINOPS):
            raise ForgeError("ast:operator_denied")
        _validate_expr(node.left, names, callables, d)
        _validate_expr(node.right, names, callables, d)
    elif t is ast.UnaryOp:
        if not isinstance(node.op, UNARYOPS):
            raise ForgeError("ast:operator_denied")
        _validate_expr(node.operand, names, callables, d)
    elif t is ast.BoolOp:
        for value in node.values:
            _validate_expr(value, names, callables, d)
    elif t is ast.Compare:
        if not all(isinstance(op, CMPOPS) for op in node.ops):
            raise ForgeError("ast:operator_denied")
        _validate_expr(node.left, names, callables, d)
        for comp in node.comparators:
            _validate_expr(comp, names, callables, d)
    elif t is ast.IfExp:
        for part in (node.test, node.body, node.orelse):
            _validate_expr(part, names, callables, d)
    elif t is ast.List:
        if type(node.ctx) is not ast.Load:
            raise ForgeError("ast:context_denied")
        for elt in node.elts:
            _validate_expr(elt, names, callables, d)
    elif t is ast.Dict:
        for key, value in zip(node.keys, node.values):
            if key is None:  # **unpacking
                raise ForgeError("ast:node_denied:unpack")
            _validate_expr(key, names, callables, d)
            _validate_expr(value, names, callables, d)
    elif t is ast.Subscript:
        if type(node.ctx) is not ast.Load:
            raise ForgeError("ast:context_denied")
        _validate_expr(node.value, names, callables, d)
        _validate_expr(node.slice, names, callables, d)  # ast.Slice is denied
    elif t is ast.Call:
        if type(node.func) is not ast.Name or node.func.id not in callables:
            raise ForgeError("ast:call_denied")
        if node.keywords:
            raise ForgeError("ast:keywords_denied")
        for arg in node.args:
            _validate_expr(arg, names, callables, d)
    else:
        raise ForgeError("ast:node_denied:" + t.__name__.lower())


def parse_tool(name, code):
    """Return (params, return_expr) for an allowed single-def tool."""
    tree = _parse(code, MAX_SOURCE_BYTES, MAX_CODE_NODES, "code")
    if len(tree.body) != 1 or type(tree.body[0]) is not ast.FunctionDef:
        raise ForgeError("code:need_exactly_one_def")
    fn = tree.body[0]
    if fn.name != name:
        raise ForgeError("code:name_mismatch")
    if (fn.decorator_list or fn.returns is not None
            or getattr(fn, "type_comment", None) or getattr(fn, "type_params", None)):
        raise ForgeError("code:def_extras_denied")
    a = fn.args
    if (a.posonlyargs or a.vararg or a.kwonlyargs or a.kw_defaults
            or a.kwarg or a.defaults):
        raise ForgeError("code:signature_denied")
    if len(a.args) > MAX_ARGS:
        raise ForgeError("code:too_many_args")
    params = []
    for arg in a.args:
        if arg.annotation is not None or getattr(arg, "type_comment", None):
            raise ForgeError("code:annotation_denied")
        check_identifier(arg.arg, "code:arg_name_invalid")
        if arg.arg in params:
            raise ForgeError("code:duplicate_arg")
        params.append(arg.arg)
    if len(fn.body) != 1 or type(fn.body[0]) is not ast.Return or fn.body[0].value is None:
        raise ForgeError("code:body_must_be_single_return")
    body = fn.body[0].value
    # Body may call builtins only, so recursion is impossible by construction.
    _validate_expr(body, frozenset(params), BUILTINS, 0)
    return params, body


def parse_test(name, test):
    """Return the list of assert expressions in an allowed test."""
    tree = _parse(test, MAX_TEST_BYTES, MAX_TEST_NODES, "test")
    if not tree.body or len(tree.body) > MAX_ASSERTS:
        raise ForgeError("test:assert_count")
    callables = BUILTINS | {name}
    for stmt in tree.body:
        if type(stmt) is not ast.Assert or stmt.msg is not None:
            raise ForgeError("test:only_plain_assert_allowed")
        _validate_expr(stmt.test, frozenset(), callables, 0)
    calls_tool = any(type(n) is ast.Call and n.func.id == name for n in ast.walk(tree))
    if not calls_tool:
        raise ForgeError("test:must_call_tool")
    return [stmt.test for stmt in tree.body]


# ---------------------------------------------------------------- evaluation

def _is_num(v):
    return type(v) in (int, float, bool)


def _is_int(v):
    return type(v) in (int, bool)


def _binop(op, a, b):
    if op is ast.Add:
        if _is_num(a) and _is_num(b):
            return a + b
        if type(a) is str and type(b) is str:
            if len(a) + len(b) > MAX_STR_LEN:
                raise ForgeError("limit:str_len")
            return a + b
        if type(a) is list and type(b) is list:
            if len(a) + len(b) > MAX_ITEMS:
                raise ForgeError("limit:items")
            return a + b
        raise ForgeError("eval:type_error")
    if op is ast.Mult:
        if _is_num(a) and _is_num(b):
            if _is_int(a) and _is_int(b) and a.bit_length() + b.bit_length() > MAX_INT_BITS + 1:
                raise ForgeError("limit:int_bits")
            return a * b
        if type(b) is int and type(a) in (str, list):
            seq, n = a, b
        elif type(a) is int and type(b) in (str, list):
            seq, n = b, a
        else:
            raise ForgeError("eval:type_error")
        limit = MAX_STR_LEN if type(seq) is str else MAX_ITEMS
        if n > 0 and len(seq) * n > limit:  # checked before allocating
            raise ForgeError("limit:repeat")
        return seq * n
    if not (_is_num(a) and _is_num(b)):
        raise ForgeError("eval:type_error")
    if op is ast.Sub:
        return a - b
    if op in (ast.Div, ast.FloorDiv, ast.Mod) and b == 0:
        raise ForgeError("eval:zero_division")
    if op is ast.Div:
        return a / b
    if op is ast.FloorDiv:
        return a // b
    if op is ast.Mod:
        return a % b
    # ast.Pow: bound the result size before computing it.
    if _is_int(b):
        if b < 0 and a == 0:
            raise ForgeError("eval:zero_division")
        if b > 0 and _is_int(a) and abs(a) > 1 and (abs(a).bit_length() - 1) * b > MAX_INT_BITS:
            raise ForgeError("limit:int_bits")
    result = a ** b
    if type(result) is complex:
        raise ForgeError("eval:complex_result")
    return result


def _compare(op, a, b):
    if op is ast.Eq:
        return a == b
    if op is ast.NotEq:
        return a != b
    if op in (ast.In, ast.NotIn):
        if type(b) is str and type(a) is not str:
            raise ForgeError("eval:type_error")
        if type(b) is dict and type(a) is not str:
            raise ForgeError("eval:type_error")
        if type(b) not in (str, list, dict):
            raise ForgeError("eval:type_error")
        return (a in b) if op is ast.In else (a not in b)
    if not ((_is_num(a) and _is_num(b)) or (type(a) is type(b) and type(a) in (str, list))):
        raise ForgeError("eval:type_error")
    if op is ast.Lt:
        return a < b
    if op is ast.LtE:
        return a <= b
    if op is ast.Gt:
        return a > b
    return a >= b


def _ordered_items(seq):
    if all(_is_num(x) for x in seq) or all(type(x) is str for x in seq):
        return seq
    raise ForgeError("eval:type_error")


def _builtin(fname, args):
    if fname in ("min", "max"):
        if len(args) == 1:
            if type(args[0]) is not list:
                raise ForgeError("eval:type_error")
            seq = args[0]
        else:
            seq = args
        if not seq:
            raise ForgeError("eval:empty_sequence")
        seq = _ordered_items(seq)
        return min(seq) if fname == "min" else max(seq)
    if len(args) != 1:
        raise ForgeError("eval:arity")
    x = args[0]
    if fname == "abs":
        if not _is_num(x):
            raise ForgeError("eval:type_error")
        return abs(x)
    if fname == "len":
        if type(x) not in (str, list, dict):
            raise ForgeError("eval:type_error")
        return len(x)
    if type(x) is not list:
        raise ForgeError("eval:type_error")
    if fname == "sum":
        if not all(_is_num(i) for i in x):
            raise ForgeError("eval:type_error")
        return sum(x)
    return sorted(_ordered_items(x))  # "sorted"


class Interpreter:
    """Walks a validated tree by hand under a deterministic step budget."""

    def __init__(self, name, params, body):
        self.name = name
        self.params = params
        self.body = body
        self.steps = 0
        self.tool_calls = 0  # counts real call_tool executions, not Call nodes

    def call_tool(self, args):
        self.tool_calls += 1
        if len(args) != len(self.params):
            raise ForgeError("call:arity")
        for arg in args:
            check_value(arg)
        return check_value(self.eval(self.body, dict(zip(self.params, args))))

    def eval(self, node, env):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise ForgeError("limit:steps")
        try:
            return check_value(self._eval(node, env))
        except ZeroDivisionError:
            raise ForgeError("eval:zero_division")
        except OverflowError:
            raise ForgeError("eval:overflow")
        except TypeError:
            raise ForgeError("eval:type_error")

    def _eval(self, node, env):
        t = type(node)
        if t is ast.Constant:
            return node.value
        if t is ast.Name:
            return env[node.id]
        if t is ast.BinOp:
            return _binop(type(node.op), self.eval(node.left, env), self.eval(node.right, env))
        if t is ast.UnaryOp:
            v = self.eval(node.operand, env)
            if type(node.op) is ast.Not:
                return not v
            if not _is_num(v):
                raise ForgeError("eval:type_error")
            return -v if type(node.op) is ast.USub else +v
        if t is ast.BoolOp:
            is_and = type(node.op) is ast.And
            for value in node.values:
                v = self.eval(value, env)
                if bool(v) != is_and:
                    return v
            return v
        if t is ast.Compare:
            left = self.eval(node.left, env)
            for op, comp in zip(node.ops, node.comparators):
                right = self.eval(comp, env)
                if not _compare(type(op), left, right):
                    return False
                left = right
            return True
        if t is ast.IfExp:
            branch = node.body if self.eval(node.test, env) else node.orelse
            return self.eval(branch, env)
        if t is ast.List:
            return [self.eval(elt, env) for elt in node.elts]
        if t is ast.Dict:
            out = {}
            for k, v in zip(node.keys, node.values):
                key = self.eval(k, env)
                if type(key) is not str:
                    raise ForgeError("eval:dict_key_not_str")
                out[key] = self.eval(v, env)
            return out
        if t is ast.Subscript:
            container = self.eval(node.value, env)
            index = self.eval(node.slice, env)
            if type(container) in (list, str) and type(index) is int:
                if not -len(container) <= index < len(container):
                    raise ForgeError("eval:index_error")
                return container[index]
            if type(container) is dict and type(index) is str:
                if index not in container:
                    raise ForgeError("eval:key_error")
                return container[index]
            raise ForgeError("eval:type_error")
        if t is ast.Call:
            args = [self.eval(arg, env) for arg in node.args]
            if node.func.id in BUILTINS:
                return _builtin(node.func.id, args)
            return self.call_tool(args)  # only reachable from test asserts
        raise ForgeError("ast:node_denied")  # unreachable after validation


# ------------------------------------------------------------------ protocol

def _reject_constant(_):
    raise ValueError("non-finite number")


def run_test(name, code, test):
    params, body = parse_tool(name, code)
    asserts = parse_test(name, test)
    interp = Interpreter(name, params, body)
    for i, expr in enumerate(asserts, 1):
        if not interp.eval(expr, {}):
            raise ForgeError("assertion_failed:%d" % i)
    # parse_test's static Call check is not enough: `assert True or f(0)`
    # mentions the tool but short-circuits before ever running it.
    if interp.tool_calls == 0:
        raise ForgeError("test:must_execute_tool")


def run_invoke(name, code, args):
    if type(args) is not list:
        raise ForgeError("request:args_not_list")
    check_value(args)
    params, body = parse_tool(name, code)
    return Interpreter(name, params, body).call_tool(args)


def handle(raw):
    if len(raw) > MAX_REQUEST_BYTES:
        raise ForgeError("request:too_large")
    try:
        req = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ForgeError("request:invalid_json")
    if type(req) is not dict:
        raise ForgeError("request:not_object")
    name = check_identifier(req.get("name"), "name:invalid")
    op = req.get("op")
    if op == "test":
        run_test(name, req.get("code"), req.get("test"))
        return {"ok": True}
    if op == "invoke":
        return {"ok": True, "value": run_invoke(name, req.get("code"), req.get("args"))}
    raise ForgeError("request:unknown_op")


def main():
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    try:
        resp = handle(raw)
    except ForgeError as e:
        resp = {"ok": False, "error": e.code}
    except RecursionError:
        resp = {"ok": False, "error": "limit:recursion"}
    except MemoryError:
        resp = {"ok": False, "error": "limit:memory"}
    except Exception as e:  # never echo messages, only the exception type
        resp = {"ok": False, "error": "internal:" + type(e).__name__}
    sys.stdout.write(json.dumps(resp, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
