"""Check real MCP transports and errors without calling a model API.

Run normally for stdio. Add --http-url http://127.0.0.1:8000/mcp
to also check a running HTTP server and its required request metadata.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mcp import Client, StdioServerParameters

import tools_server


async def check_transport(target):
    async with Client(target) as client:
        listed = await client.list_tools()
        print("tools/list:", listed.model_dump_json(by_alias=True))
        tools = {t.name: t for t in listed.tools}
        assert set(tools) == {"calculator", "read_file", "write_note"}
        for name in tools:
            assert tools[name].description == getattr(tools_server, name).__doc__
        assert tools["calculator"].input_schema["required"] == ["expression"]
        assert tools["read_file"].input_schema["required"] == ["path"]
        assert tools["write_note"].input_schema["required"] == ["path", "content"]
        assert tools["write_note"].annotations.read_only_hint is False
        assert tools["write_note"].annotations.idempotent_hint is False

        cases = [
            ("calculator", {"expression": "48000 + 9500 + 12000 + 4"}, False, "69504"),
            ("calculator", {"expression": "-(3 + 2) ** 2 / 5"}, False, "-5.0"),
            ("read_file", {"path": "notes.txt"}, False, "Attendees: 4"),
            ("calculator", {"expression": "1 / 0"}, True, None),
            ("calculator", {"expression": "__import__('os')"}, True, None),
            ("calculator", {}, True, None),
            ("read_file", {"path": "../outside.txt"}, True, "denied"),
            ("read_file", {"path": "../lab-sibling/notes.txt"}, True, "denied"),
            ("read_file", {"path": "file-that-does-not-exist.txt"}, True, None),
            ("write_note", {"path": "../outside.txt", "content": "blocked"}, True, "denied"),
            ("write_note", {"path": "notes.txt"}, True, None),
            ("missing_tool", {}, True, None),
        ]
        for name, args, is_error, expected in cases:
            result = await client.call_tool(name, args)
            print("tools/call:", name, args, result.model_dump_json(by_alias=True))
            assert result.is_error == is_error, (name, args, result)
            if expected is not None:
                assert expected in "\n".join(c.text for c in result.content if c.type == "text")
        print(f"PASS: {client.protocol_version}, schema discovery and {len(cases)} calls")
        # A disposable directory avoids appending to any existing user file.
        with tempfile.TemporaryDirectory(prefix="write-check-", dir=tools_server.ROOT) as directory:
            note = Path(directory) / "note.txt"
            relative = str(note.relative_to(tools_server.ROOT))
            for content in ("first", "둘째"):
                result = await client.call_tool("write_note", {"path": relative, "content": content})
                print("tools/call: write_note", content, result.model_dump_json(by_alias=True))
                assert not result.is_error
            result = await client.call_tool("read_file", {"path": relative})
            print("tools/call: read back", result.model_dump_json(by_alias=True))
            assert not result.is_error
            assert result.content[0].text == "first\n둘째\n"
            assert note.read_text(encoding="utf-8") == "first\n둘째\n"
        print("PASS: write_note creates a file and appends without overwriting")


def check_file_boundary():
    original_root = tools_server.ROOT
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        root = base / "root"
        root.mkdir()
        (base / "outside.txt").write_text("synthetic boundary fixture", encoding="utf-8")
        (root / "escape.txt").symlink_to(base / "outside.txt")
        (root / "long.txt").write_text("가" * 4001, encoding="utf-8")
        try:
            tools_server.ROOT = root
            assert tools_server.read_file("long.txt") == "가" * 4000
            try:
                tools_server.read_file("escape.txt")
            except tools_server.ToolError as exc:
                assert "denied" in str(exc)
            else:
                raise AssertionError("symlink escaped the server root")
            for path in ("escape.txt", "../outside.txt", str(base / "outside.txt")):
                try:
                    tools_server.write_note(path, "must not be written")
                except tools_server.ToolError as exc:
                    assert "denied" in str(exc)
                else:
                    raise AssertionError("write_note escaped the server root")
            assert (base / "outside.txt").read_text(encoding="utf-8") == "synthetic boundary fixture"
        finally:
            tools_server.ROOT = original_root
    print("PASS: read/write path and symlink boundaries; 4000-character read limit")


def check_http_metadata(url):
    for missing in (None, "method", "capabilities"):
        meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {}}
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"}
        if missing == "method":
            del headers["Mcp-Method"]
        if missing == "capabilities":
            del meta["io.modelcontextprotocol/clientCapabilities"]
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}}
        request = Request(url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                status, text = response.status, response.read().decode()
        except HTTPError as error:
            status, text = error.code, error.read().decode()
        print(f"HTTP missing={missing}: {status} {text}")
        assert status == (200 if missing is None else 400)
    print("PASS: HTTP metadata validation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-url")
    args = parser.parse_args()
    check_file_boundary()
    asyncio.run(check_transport(StdioServerParameters(
        command=sys.executable, args=[str(tools_server.ROOT / "tools_server.py")],
    )))
    if args.http_url:
        asyncio.run(check_transport(args.http_url))
        check_http_metadata(args.http_url)
