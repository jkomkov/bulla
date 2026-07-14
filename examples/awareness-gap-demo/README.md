# Awareness-gap demo: filesystem + GitHub

A small reproducible demonstration of what `bulla` finds that schema
validation misses.

## The task

An agent is asked to read a local file and commit it to a GitHub
repository. The agent has access to two MCP servers: `filesystem` for
local file I/O and `github` for repository operations. Both expose
tools that take a `path: string` field.

## The failure

The agent calls `filesystem.read_file(name="README.md")`. The server
returns the file contents and the absolute path
`/home/user/projects/myrepo/README.md`. The agent passes that
path verbatim to `github.create_file(path=..., content=...)`. The
GitHub API rejects the request with a path-validation error, because
GitHub's `create_file` expects a repository-relative path like
`README.md`, not an absolute filesystem path.

Schema validation passed at every step. Both `path` fields are typed
as `string`. The MCP type system has no concept of "absolute vs
repo-relative." The agent sees a 422, attributes it to a flaky
GitHub call, and retries with the same input. Whoever reviews the
trace later finds the failure and patches the path conversion in
the agent prompt by hand. The next composition with a different
seam fails the same way.

## The diagnosis

`bulla` measures coherence at composition time, before the agent
runs. The two servers and their tools form a graph. `bulla` builds
the coboundary operator `δ₀` over the shared dimensions and computes
`H¹ = ker(δ₁) / im(δ₀)`. The dimension of that quotient is the
**coherence fee**. It counts the minimum number of bridges needed
to repair the composition.

Run from this directory with the bulla package on the path:

```
$ python repro.py --no-fix
```

The bulla diagnosis names the path-format seam directly:

```
Coherence fee: 22 (across 1 convention dimension)

  1. path format
     filesystem__read_file.path ↔ github__create_or_update_file.path
     Some tools use absolute paths like /Users/alice/proj/file.ts;
     others use repository-relative paths like src/file.ts.
     An agent that reads /Users/alice/proj/src/main.ts from the
     filesystem and passes the same path to a GitHub create_file call
     gets 'file not found' or commits the file to the wrong place in
     the repo.

  2. path format
     filesystem__read_text_file.path ↔ github__create_or_update_file.path
     ...
```

The full receipt is content-addressed. Run `bulla scan --json` for
the machine-readable form.

## The fix

The receipt names the dimension. The runtime ships a typed
translator that resolves it. `bulla.translate(dimension, value, ...)`
returns the converted value for any registered translator. The
built-in `path_convention` translator strips the resolved repo root
(read from `BULLA_REPO_ROOT`, then `git rev-parse --show-toplevel`,
then `os.getcwd()` as a contextual fallback). Every translation call
produces a `WitnessReceipt` that chains into the composition's audit
trail.

```
import os
os.environ["BULLA_REPO_ROOT"] = "/home/user/projects/myrepo"

bulla.translate(
    "path_convention",
    value="/home/user/projects/myrepo/README.md",
    from_convention="filesystem-absolute",
    to_convention="repo-relative",
)
# -> value="README.md", equivalence="exact"

github.create_file(path="README.md", ...)
# -> ACCEPTED
```

Schema validation still passes. The agent commits the file to the
right path in the repo.

## What scales

The demo above uses two servers because the failure is most concrete
that way. The math is graph-valued and not pairwise: with three
servers, every pair can have `fee = 0` while the global composition
has `fee > 0`. That's the case where pairwise type-checking literally
cannot find what `bulla` finds. `bulla scan` on a real Cursor or
Claude Code MCP config runs the pairwise comparison alongside the
global diagnosis and surfaces the moat case in plain prose.

## Reproducing

```
git clone https://github.com/jkomkov/res-agentica
cd res-agentica/bulla/examples/awareness-gap-demo
python repro.py
```

No npm install. No live MCP servers. No LLM calls. The script reads
the canned `manifests/filesystem.json` and `manifests/github.json`,
runs the same `bulla.compose_multi` pipeline that `bulla scan` runs,
and demonstrates the failure deterministically. Same inputs always
produce the same fee, the same blind-spot list, the same translation
receipts.

For a live scan against your own MCP config:

```
pip install bulla
bulla scan
```

## What to read next

- [`bulla/README.md`](../../README.md) — the broader product story.
- [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) — how the
  coboundary, the witness Gram matrix, and the receipt model fit
  together.
- The `papers/` directory carries the formal proofs.

## Notes on the example

The simulated `github_create_file_validator` in `repro.py` mirrors
GitHub's actual API behavior — the real `POST
/repos/{owner}/{repo}/contents/{path}` endpoint rejects absolute
filesystem paths because it treats the path as repository-relative.
The simulation lets the demo run without GitHub credentials and
produces a deterministic failure that anyone can reproduce.

The fee count of 22 surfaces because the canned filesystem manifest
includes 11 path-bearing tools and the GitHub manifest includes 2;
each cross-server pair contributes one blind spot per shared
convention. Real Cursor / Claude Code configs typically have 3–5
servers and produce smaller, more focused fees.
