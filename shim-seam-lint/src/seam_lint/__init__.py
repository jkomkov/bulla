"""seam-lint has been renamed to bulla.

Install the new package:
    pip install bulla

All functionality is identical. Only the name has changed.
"""

import warnings

warnings.warn(
    "seam-lint has been renamed to bulla. "
    "Install the new package: pip install bulla. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything from bulla so existing code keeps working
from bulla import *  # noqa: F401, F403
from bulla import __version__


def main():
    """Shim entry point — prints deprecation notice then runs bulla CLI."""
    import sys
    print(
        "WARNING: seam-lint has been renamed to bulla.\n"
        "  Replace with: pip install bulla\n"
        "  Then use:     bulla " + " ".join(sys.argv[1:]) + "\n",
        file=sys.stderr,
    )
    from bulla.cli import main as bulla_main
    bulla_main()
