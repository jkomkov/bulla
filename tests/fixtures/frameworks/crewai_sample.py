"""Sample CrewAI tool definitions for adapter testing."""


def tool(*args, **kwargs):
    if len(args) == 1 and callable(args[0]):
        return args[0]
    def _wrap(f):
        return f
    return _wrap


@tool("Web Search Tool")
def web_search(query: str) -> str:
    """Search the web."""
    return ""


@tool
def calculate(expression: str) -> float:
    """Evaluate a math expression."""
    return 0.0


class BaseTool:
    pass


class FileReaderInput:  # stub for Pydantic schema
    pass


class FileReader(BaseTool):
    """Read a file from disk."""
    name: str = "file_reader"
    description: str = "Reads file contents."
    args_schema = FileReaderInput

    def _run(self, path: str) -> str:
        return ""
