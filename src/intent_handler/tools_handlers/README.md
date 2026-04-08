# tools_handlers

Standalone tool-call orchestration package with provider-specific syntax adapters.

Supported providers:

- openai
- llama.cpp
- claude
- gemini
- grok

Architecture:

- `ToolRegistry` is the canonical list of tools and validators.
- `BaseToolsAdapter` normalizes provider syntax differences.
- `ToolLoopEngine` runs the shared tool loop:
  - send messages + tool schemas
  - parse tool call(s)
  - execute tool(s)
  - append tool results in provider-specific format
  - repeat until final text or max steps

The package is standalone and does not modify the existing assistant/router runtime.

# Usig Tools
Tool specs are in Claude format. To convert:
```python
    spec = ToolLoader.convert_for_provider_from_file("openai", "tools/my_tool.json")   # reads Claude format and converts to openai
``` 