# seam-lint → bulla

**This package has been renamed to [bulla](https://pypi.org/project/bulla/).**

```
pip install bulla
```

This shim package depends on `bulla` and re-exports everything, so existing
`import seam_lint` code will continue to work with a deprecation warning.
New code should use `import bulla` directly.
