from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcp import Client

import tools_server


class LabServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_server_discovers_all_three_tools_from_decorators(self):
        async with Client(tools_server.mcp) as client:
            listed = await client.list_tools()
        self.assertEqual(
            {tool.name for tool in listed.tools},
            {"calculator", "read_file", "write_note"},
        )
        self.assertTrue(all(tool.description for tool in listed.tools))

    async def test_tools_execute_through_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input.txt").write_text("hello", encoding="utf-8")
            with patch.dict(os.environ, {"MCP_WORKSPACE": directory}):
                async with Client(tools_server.mcp) as client:
                    read = await client.call_tool("read_file", {"path": "input.txt"})
                    calc = await client.call_tool(
                        "calculator", {"expression": "3 * (4 + 5)"}
                    )
                    saved = await client.call_tool(
                        "write_note", {"content": "total: 27"}
                    )
            self.assertEqual(read.content[0].text, "hello")
            self.assertEqual(calc.content[0].text, "27")
            self.assertEqual(saved.content[0].text, "Saved to expense_summary.txt")
            self.assertEqual(
                (root / "expense_summary.txt").read_text(encoding="utf-8"),
                "total: 27\n",
            )

    async def test_read_file_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"MCP_WORKSPACE": directory}):
                async with Client(tools_server.mcp) as client:
                    result = await client.call_tool("read_file", {"path": "../outside"})
            self.assertTrue(result.is_error)
            self.assertIn("outside the server working directory", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
