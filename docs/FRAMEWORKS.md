# Framework Adapters

Bulla can ingest tool definitions from programmatic frameworks
(LangGraph, CrewAI, Anthropic Messages, etc.) by AST-parsing source
files. Adapters normalize the framework's tool format into a list of
`ToolDef` and emit a Bulla manifest.

## Built-in adapters

| Name                  | Display                | Source format                                       | AST patterns                                   |
|-----------------------|------------------------|-----------------------------------------------------|------------------------------------------------|
| `anthropic-messages`  | Anthropic Messages API | JSON (`{tools: [...]}` or array) or `.py` literal   | top-level `tools = [...]`                      |
| `langgraph`           | LangGraph / LangChain  | Python source (file or directory)                   | `@tool`, `@tool("name")`, `BaseTool` subclass, `StructuredTool.from_function(...)` |
| `crewai`              | CrewAI                 | Python source (file or directory)                   | `@tool`, `@tool("name")`, `BaseTool` subclass with `name`/`description`/`args_schema` |

## CLI

```bash
bulla frameworks list                           # show registered adapters
bulla import langgraph workflow.py              # emit Bulla manifest JSON to stdout
bulla import langgraph workflow.py --out m.json # write to file
bulla import crewai agents/ --audit             # parse → audit, no intermediate file
bulla import anthropic-messages tools.json --audit
```

## Parse modes

```python
class ParseMode(Enum):
    STATIC = "static"    # AST/JSON parse — implemented now
    RUNTIME = "runtime"  # Live import, sandboxed — reserved for future sprint
```

Today every adapter supports `STATIC`. `RUNTIME` is intentionally part
of the protocol so a future sprint can add subprocess-sandboxed live
imports without changing the adapter interface or CLI signature. Each
adapter raises `NotImplementedError` for `RUNTIME`; the CLI surfaces a
clear "future sprint" message.

## What static parsing catches and what it doesn't

**Catches:**
- Top-level `@tool`/`@tool("name")` decorated functions with type-annotated
  arguments (LangGraph, CrewAI)
- `BaseTool` subclasses with `name`, `description`, and `args_schema` class
  attributes (LangGraph, CrewAI)
- `StructuredTool.from_function(name=..., description=...)` calls (LangGraph)
- JSON arrays of `{name, description, input_schema}` (Anthropic)
- Top-level `tools = [...]` Python literals (Anthropic)

**Misses (future `RUNTIME` mode will close these):**
- Tools registered in for-loops or via metaprogramming
- Decorators applied via metaclasses
- Tools assembled from runtime configuration
- Pydantic schema field structure (recorded by class name only)

## Adding a new framework

Drop a new module under `src/bulla/frameworks/<name>.py`:

```python
from bulla.frameworks import (
    FrameworkError, ParseMode, ToolDef, register
)
from pathlib import Path

class MyFrameworkAdapter:
    name = "my-framework"
    display_name = "My Framework"

    def supports(self, mode: ParseMode) -> bool:
        return mode is ParseMode.STATIC

    def parse(self, source, mode: ParseMode = ParseMode.STATIC) -> list[ToolDef]:
        if mode is ParseMode.RUNTIME:
            raise NotImplementedError("Reserved for future sprint")
        # ...AST parse, return list of ToolDef
        return []

register(MyFrameworkAdapter())
```

Then add an import line to `_load_builtin_frameworks()` in
`src/bulla/frameworks/__init__.py`, add fixtures under
`tests/fixtures/frameworks/`, and add tests in `tests/test_frameworks.py`.

## Optional dependencies

The core `pip install bulla` has no framework dependencies. Static
adapters use stdlib only. To enable future runtime support and
schema-validation helpers, install extras:

```bash
pip install bulla[langgraph]   # adds langchain-core
pip install bulla[crewai]      # adds crewai
pip install bulla[all]         # everything (also adds ots + discover)
```

## Pipeline integration

`bulla import` emits a JSON file in the MCP `tools/list` shape:

```json
{
  "tools": [
    {"name": "...", "description": "...", "inputSchema": {...}},
    ...
  ]
}
```

This feeds straight into `bulla audit --manifests <dir>` after running
through `bulla manifest --from-json`, or via `bulla import ... --audit`
which does the whole pipeline in one shot.

## Runtime integration (live objects)

The static adapters above parse `.py` source files. The runtime
adapters take live framework objects — a constructed `StateGraph`, a
constructed `Crew` — and snapshot them into a `bulla.Session` whose
`.fee` is immediately available, whose `.diagnose()` returns a full
`WitnessReceipt`, and whose execution can be observed via a callback
handler attached to the framework's invocation lifecycle.

### LangGraph

Install: `pip install bulla[langgraph]` (pulls in `langgraph>=1.1`,
`langchain-core>=0.3`).

```python
from typing import TypedDict
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode
from langchain_core.tools import tool

import bulla
from bulla.langgraph import bind, BullaCallbackHandler

class S(TypedDict):
    currency: str
    amount: float

@tool
def get_quote(currency: str, amount: float) -> dict:
    """Get FX quote."""
    return {"currency": currency, "amount": amount}

@tool
def settle(currency: str, amount: float) -> dict:
    """Settle payment."""
    return {"currency": currency, "amount": amount}

g = StateGraph(S)
g.add_node("quote", ToolNode([get_quote]))
g.add_node("settle", ToolNode([settle]))
g.add_edge("quote", "settle")

# 1. Pre-execution diagnosis
session = bind(g, name="fx-flow")
print(session.fee)              # 0 in this example (currency observable both sides)
receipt = session.diagnose()    # full WitnessReceipt

# 2. Live observation during invocation
handler = BullaCallbackHandler(session)
g.compile().invoke({"currency": "USD", "amount": 100},
                   config={"callbacks": [handler]})
print(handler.terminal_receipt.receipt_hash)
print(handler.invocations)      # list of (run_id, tool, ts, duration_ms)
```

`bind()` keyword arguments:

- `name`: Session name. Defaults to `"langgraph"`.
- `policy`: `PolicyProfile`. Defaults to `bulla.DEFAULT_POLICY_PROFILE`.
- `output_schemas`: `dict[tool_name, jsonschema_dict]`. LangChain's
  `BaseTool.args_schema` covers inputs but no standardized output
  schema exists; supply this when output-side fee detection matters.
  Without it, a one-line warning is logged per node missing output
  hints.
- `on_unknown_branch`: `"fan_out"` (default) or `"skip"`. Conditional
  edges declared without a `path_map` are conservatively treated as
  edges to every node (matching LangGraph's runtime behavior). Pass
  `"skip"` to record nothing.

### CrewAI

Install: `pip install bulla[crewai]` (pulls in `crewai>=0.80`).

```python
from crewai import Agent, Crew, Process, Task
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

import bulla
from bulla.crewai import bind, BullaCrewCallback

class SearchInput(BaseModel):
    query: str = Field(description="Search query")

class SearchTool(BaseTool):
    name: str = "search"
    description: str = "Search the web"
    args_schema: type = SearchInput
    def _run(self, query: str) -> str: ...

researcher = Agent(role="researcher", goal="find", backstory="b",
                   tools=[SearchTool()])
writer = Agent(role="writer", goal="write", backstory="b")

t1 = Task(description="Research", agent=researcher,
          expected_output="notes")
t2 = Task(description="Write", agent=writer,
          expected_output="summary", context=[t1])

crew = Crew(agents=[researcher, writer], tasks=[t1, t2],
            process=Process.sequential)

session = bind(crew, name="research-flow")
print(session.fee)

handler = BullaCrewCallback(session)
crew.kickoff(
    inputs={"topic": "X"},
    # CrewAI exposes step_callback / task_callback at construction time:
)
# Or pass handler.on_step / handler.on_task_complete to Crew(...)
# at construction time depending on your CrewAI version.
final = handler.finalize()
print(final.receipt_hash)
```

CrewAI tool naming: every tool is namespaced as `"{agent.role}.{tool.name}"`
so two agents that both expose a `search` tool are kept distinct in the
composition.

### Versions and risks

- `langgraph>=1.1`. The integration reads `graph.nodes`, `graph.edges`,
  `graph.branches`, `graph.channels` directly. These are public but
  pre-1.0 LangGraph refactored them; the integration uses defensive
  `getattr` fallbacks where possible. Pin tighter (`<2`) if you hit
  drift.
- `crewai>=0.80`. The integration reads `crew.agents`, `crew.tasks`,
  `task.context`, `task.tools`, `agent.tools`, `crew.process`,
  `crew.manager_agent`. CrewAI is also pre-1.0; same caution applies.
- **Output-schema gap.** Both LangChain `BaseTool` and CrewAI `BaseTool`
  expose `args_schema` for inputs but no standardized output schema.
  When a fee includes only input-side seams, the answer is honest. When
  a real seam exists on the output side and no `output_schemas={...}`
  was supplied, the integration silently under-reports. Pass
  `output_schemas` for any tool whose outputs you care about, or accept
  the warning logged for each unschematized node.

### Imports are lazy

`import bulla.langgraph` and `import bulla.crewai` succeed even when
the framework extras aren't installed. The `bind()` and callback
classes only fail at call time with a clear `FrameworkError` (or an
`ImportError` from the framework itself). This keeps the bulla import
graph clean for users who only want the static adapters.
