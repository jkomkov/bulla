# Clean OCI replay image

The Dockerfile pins the multi-platform Python 3.12 slim manifest by digest and runs the zero-import Golden v0.2 verifier. It is a clean, reproducible captive replay surface—not the required independent clean-room implementation.

Build from the repository root:

```sh
docker build -f bulla/bench/golden/v0.2/container/Dockerfile -t bulla-golden-v02:captive .
docker run --rm bulla-golden-v02:captive
```

The base digest was resolved on 2026-07-18. Changing it creates a different environment observation and requires a fresh build record.
