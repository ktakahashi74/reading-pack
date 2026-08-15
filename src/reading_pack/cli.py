"""Small command dispatcher for the Reading Pack core and optional plugins."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .commands import core
from .errors import ReadingPackError
from reading_pack_review import commands as review_commands_module


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        raise ReadingPackError(message, 2)


def parser() -> Parser:
    root = Parser(
        prog="reading-pack",
        description="Build deterministic, author-reviewed Reading Packs without network access.",
    )
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    core.register(commands)

    review = commands.add_parser(
        "review", help="create and apply one human-editable Markdown review"
    )
    review_commands = review.add_subparsers(dest="review_command", required=True)
    review_commands_module.register(commands, review, review_commands)

    # The core remains usable when the producer package is not installed.
    try:
        from reading_pack_producer import commands as producer_commands
    except ModuleNotFoundError as exc:
        if exc.name != "reading_pack_producer":
            raise
    else:
        producer_commands.register(commands, review_commands)
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        return args._handler(args)
    except ReadingPackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
