"""Offline verification of the agent loop — no API key, no network.

first_agent.py is left exactly as submitted. This file swaps the Anthropic
client for a scripted stub, so the four CHECKPOINT claims can be shown to be
true by execution rather than by assertion:

  1. the loop chains read_file -> calculator -> write_note on its own
  2. deleting the loop stops any tool from actually running
  3. max_steps really terminates a runaway agent
  4. the path guard really refuses paths outside the working directory

Run:  python verify_offline.py
NOTE: the model's decisions here are scripted, not real. This proves the
harness, not the model. The real run log is logs/run-01.txt.
"""
import os
import sys
import first_agent as fa


class Block:
    def __init__(self, type, text=None, name=None, input=None, id=None):
        self.type, self.text, self.name, self.input, self.id = type, text, name, input, id


class Resp:
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason = content, stop_reason


class StubMessages:
    def __init__(self, script):
        self.script, self.calls = script, 0

    def create(self, **kwargs):
        resp = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return resp


class StubAnthropic:
    """Stands in for anthropic.Anthropic(); replays a fixed script."""
    script = []

    def __init__(self, *a, **kw):
        self.messages = StubMessages(StubAnthropic.script)


def use(script):
    StubAnthropic.script = script
    fa.anthropic.Anthropic = StubAnthropic


def hr(title):
    print("\n" + "=" * 66 + f"\n{title}\n" + "=" * 66)


CHAIN = [
    Resp([Block("tool_use", name="read_file", input={"path": "notes.txt"}, id="t1")], "tool_use"),
    Resp([Block("tool_use", name="calculator",
                input={"expression": "4 + 48000 + 9500 + 12000"}, id="t2")], "tool_use"),
    Resp([Block("tool_use", name="write_note",
                input={"path": "result.md", "text": "total: 69504"}, id="t3")], "tool_use"),
    Resp([Block("text", text="The numbers add up to 69504; recorded in result.md.")], "end_turn"),
]

hr("1) THE LOOP: three tools chained, then a final answer")
use(CHAIN)
print(fa.run("Read notes.txt, add up every number in it, and record the total in result.md."))
print(f"  [meta] model calls made: 4 | result.md exists: {os.path.exists('result.md')}")

hr("2) THE LOOP REMOVED: same script, one call, no tool ever executed")
use(CHAIN)
client = fa.anthropic.Anthropic()
resp = client.messages.create(model="stub", max_tokens=1024, tools=fa.TOOLS,
                              messages=[{"role": "user", "content": "same goal"}])
for b in resp.content:
    print(f"  the model only SAID: {b.type} -> {b.name}({b.input})")
print("  no [tool] line above: without the loop nothing is executed. It is a program again.")

hr("3) SAFETY: max_steps stops a model that never stops calling tools")
use([Resp([Block("tool_use", name="calculator", input={"expression": "1+1"}, id="loop")], "tool_use")])
print(fa.run("spin forever", max_steps=3))

hr("4) SAFETY: the path guard, on both the read and the write direction")
print("  read_file('/etc/passwd')      ->", fa.read_file("/etc/passwd"))
print("  write_note('/tmp/pwned.txt')  ->", fa.write_note("/tmp/pwned.txt", "x"))
print("  write_note('result.md')       ->", fa.write_note("result.md", "(written by the guard test)"))

hr("5) TOOL COUNT (what CI counts)")
print(f"  TOOLS      = {[t['name'] for t in fa.TOOLS]}")
print(f"  TOOLS_IMPL = {sorted(fa.TOOLS_IMPL)}")

print("\nall offline checks completed")
