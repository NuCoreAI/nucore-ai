"""Plugin-authoring tool set for the unified agentic loop: profile
authoring/validation, UOM lookup, testing an already-installed plugin
locally (configure/start/stop/restart/call) -- serving both the plugin
*developer* and, per design/developers/impl_plan.md, a non-technical
customer-facing authoring flow built on the same tools. Enabled via its own
``nucore_runtime.plugin_authoring`` profile in runtime config (``enabled``,
``plugin_output_root``, etc.) -- see
``run_unified_runtime._build_plugin_authoring_tool_set`` and
``UnifiedRuntime``'s ``tool_sets``/``ToolSetBundle`` constructor param
(unified/runtime.py) for how it's wired in alongside the customer-facing
``unified`` tool set, and design/developers/merged-toolsets.md for the
dynamic-switching design.

Distinct from design/developers/ (which documents the IoX/Polyglot *plugin*
architecture itself) -- this package is the runnable chat tool set, built on
nucore + unified.loop.AgenticLoop exactly like the customer-facing path,
just with a different tools/dispatch/prompt.
"""

from pathlib import Path

from . import dispatch
from .evidence_ledger import EvidenceLedger
from .prompt.prompt_builder import build_system_prompt_sections

TOOLS_DIR = Path(__file__).parent / "tools"
