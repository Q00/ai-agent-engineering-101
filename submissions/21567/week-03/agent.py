"""Week 1 style: model -> tool -> observation, with a bounded loop."""
import ast
import json
import math
import operator
import re

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv}


def calculate(expression):
    if not isinstance(expression, str) or len(expression) > 200:
        raise ValueError('invalid expression')
    def visit(n):
        if isinstance(n, ast.Constant) and type(n.value) in (int, float):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -visit(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](visit(n.left), visit(n.right))
        raise ValueError('unsupported arithmetic')
    value = visit(ast.parse(expression, mode='eval').body)
    if not math.isfinite(value):
        raise ValueError('nonfinite result')
    return value


def extract_dates(text):
    if not isinstance(text, str):
        raise ValueError('text must be a string')
    return re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text)


def sort_numbers(numbers):
    if not isinstance(numbers, list) or any(type(n) not in (int, float) or not math.isfinite(n) for n in numbers):
        raise ValueError('numbers must be finite numbers')
    return sorted(numbers)


FUNCTIONS = {'calculate': calculate, 'extract_dates': extract_dates, 'sort_numbers': sort_numbers}
TOOLS = [
    {'type': 'function', 'function': {'name': name, 'description': desc,
     'parameters': {'type': 'object', 'properties': {arg: schema},
                    'required': [arg], 'additionalProperties': False}}}
    for name, desc, arg, schema in [
        ('calculate', 'Calculate arithmetic with +, -, *, / and parentheses.', 'expression', {'type': 'string'}),
        ('extract_dates', 'Extract YYYY-MM-DD strings in appearance order.', 'text', {'type': 'string'}),
        ('sort_numbers', 'Sort numbers ascending, preserving duplicates.', 'numbers', {'type': 'array', 'items': {'type': 'number'}})
    ]
]


def execute(chat, role, task, emit, max_steps=6):
    messages = [{'role': 'system', 'content': role + '\nPerform the awarded task. Use the supplied tool to verify your answer. Return only the final JSON value, without markdown. For a numeric task output a bare number such as 42. For a list task output a bare array such as [1, 2]. Never wrap the answer in an object or a result key.'},
                {'role': 'user', 'content': json.dumps({'id': task['id'], 'desc': task['desc']})}]
    for step in range(1, max_steps + 1):
        msg = chat(messages, tools=TOOLS)
        messages.append(msg)
        calls = msg.get('tool_calls') or []
        if not calls:
            answer = msg.get('content') or ''
            try:
                parsed = json.loads(answer)
                if isinstance(parsed, dict):
                    raise ValueError('Expected a bare number or array, not an object')
            except ValueError:
                emit('invalid_answer', task=task['id'], answer=answer, steps=step)
                messages.append({'role': 'user', 'content': 'Invalid output format. Return only the JSON value requested by the task, without explanation or markdown.'})
                continue
            emit('answer', task=task['id'], answer=answer, steps=step)
            return {'status': 'completed', 'answer': answer, 'steps': step}
        for call in calls:
            fn = call['function']
            try:
                args = json.loads(fn['arguments'])
                out = FUNCTIONS[fn['name']](**args)
                observation = json.dumps({'result': out}, allow_nan=False)
            except (ValueError, TypeError, KeyError, ArithmeticError, SyntaxError) as exc:
                observation = json.dumps({'error': type(exc).__name__})
            emit('tool', task=task['id'], name=fn['name'], arguments=fn['arguments'], observation=observation)
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': observation})
    emit('execution_failed', task=task['id'], reason='max_steps')
    return {'status': 'failed', 'answer': '', 'steps': max_steps}
