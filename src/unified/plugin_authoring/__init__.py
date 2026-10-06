"""Plugin-authoring tool set for the unified agentic loop: profile
authoring/validation, UOM lookup, testing an already-installed plugin
locally (configure/start/stop/restart/call) -- serving both the plugin
*developer* and, per design/developers/impl_plan.md, a non-technical
customer-facing authoring flow built on the same tools. Selected via
run_unified_runtime.py's ``--tool-set plugin_authoring`` flag -- see that
flag's help and ``UnifiedRuntime``'s ``tool_spec_paths``/``dispatch``/
``system_prompt_builder`` constructor params (unified/runtime.py) for how a
tool set other than the customer-facing default gets wired in.

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
