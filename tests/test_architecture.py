from __future__ import annotations

import builtins
import unittest
from unittest import mock

from reading_pack.cli import parser


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_core_cli_builds_without_the_optional_producer_package(self) -> None:
        real_import = builtins.__import__

        def without_producer(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "reading_pack_producer":
                error = ModuleNotFoundError("optional producer is unavailable")
                error.name = "reading_pack_producer"
                raise error
            return real_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=without_producer):
            root = parser()
        command_action = next(
            action
            for action in root._actions
            if getattr(action, "dest", None) == "command"
        )
        choices = set(command_action.choices)
        self.assertIn("build", choices)
        self.assertIn("review", choices)
        self.assertNotIn("candidates", choices)
        self.assertNotIn("catalog", choices)
        self.assertNotIn("agent-skill", choices)


if __name__ == "__main__":
    unittest.main()
