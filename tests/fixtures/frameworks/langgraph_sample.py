"""Sample LangGraph tool definitions for adapter testing.

Covers: @tool decorator, @tool("name") decorator, BaseTool subclass,
StructuredTool.from_function call.
"""

# (We intentionally do NOT import langchain_core here — adapter is AST-only.)


# @tool — bare decorator
def tool(*args, **kwargs):  # stub for AST tests
    if len(args) == 1 and callable(args[0]):
        return args[0]
    def _wrap(f):
        return f
    return _wrap


@tool
def search_web(query: str, max_results: int = 5) -> str:
    """Search the web for a query."""
    return ""


@tool("custom_name")
def divide(a: float, b: float) -> float:
    """Divide a by b."""
    return a / b


# BaseTool subclass
class BaseTool:  # stub
    pass


class CalculatorArgs:  # stub for Pydantic-style schema class
    pass


class CalculatorTool(BaseTool):
    """A calculator tool."""
    name: str = "calculator"
    description: str = "Performs arithmetic."
    args_schema = CalculatorArgs

    def _run(self, expression: str) -> float:
        return 0.0


# StructuredTool.from_function
class StructuredTool:  # stub
    @staticmethod
    def from_function(*args, **kwargs):
        return None


_my_summary_tool = StructuredTool.from_function(
    func=lambda x: x,
    name="summarize",
    description="Summarize a piece of text.",
)
