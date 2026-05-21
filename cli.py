"""
Shim for Hermes memory provider CLI discovery.

Hermes' ``discover_plugin_cli_commands()`` looks for ``<plugin_dir>/cli.py``
and expects a ``register_cli(subparser)`` function.  Our actual CLI lives
in ``curve_memory/cli.py``, so this shim re-exports it.

See: plugins/memory/__init__.py :: discover_plugin_cli_commands()
"""

import sys
from pathlib import Path

# Ensure plugin dir is on sys.path so curve_memory.cli is importable
_plugin_dir = str(Path(__file__).resolve().parent)
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from curve_memory.cli import register_cli
