"""
Shim for Hermes memory provider CLI discovery.

Hermes' ``discover_plugin_cli_commands()`` looks for ``<plugin_dir>/cli.py``
and expects a ``register_cli(subparser)`` function.  Our actual CLI lives
in ``curve_memory/cli.py``, so this shim re-exports it.

See: plugins/memory/__init__.py :: discover_plugin_cli_commands()
"""

from curve_memory.cli import register_cli
