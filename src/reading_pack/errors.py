"""User-facing exceptions and stable process exit codes."""

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_VALIDATION = 3
EXIT_IO = 4
EXIT_CHECK = 5


class ReadingPackError(Exception):
    """An expected error that can be shown without a traceback."""

    def __init__(self, message: str, exit_code: int = EXIT_VALIDATION):
        super().__init__(message)
        self.exit_code = exit_code
