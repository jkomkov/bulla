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
