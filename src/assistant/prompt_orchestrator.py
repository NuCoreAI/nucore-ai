"""
Prompt Orchestrator
Orchestrates the complete prompt system: routing, intent processing, module injection, and tool loading.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
from rag import RAGData
from prompt_mgr import NuCorePrompt, ROUTER_INTENT


class PromptOrchestrator:
    """
    Orchestrates the complete prompt and tool system for NuCore agents.
    
    Responsibilities:
    - Maps intents to agents
    - Resolves prompts with module includes
    - Loads appropriate tools for each intent
    - Manages router configuration
    
    Workflow:
    1. Router determines intent using _get_router_prompt()
    2. Call process_intent(intent) with the determined intent
    3. Get back fully resolved agent prompt + tools for that intent
    """
    
    def __init__(self, config_path: str, max_context_size: int = 64000):
        """
        Initialize the loader with a configuration file.
        
        :param config_path: Path to the *_config.json file (e.g., qwen_config.json)
        """
        self.config_path = Path(config_path)
        self.base_dir = self.config_path.parent
        self.full_rags: RAGData = None
        self.summary_rags: RAGData = None
        self.max_context_size: int = max_context_size
        
        # Load the configuration
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
                # Get LLM provider name and determine tool format
        self.llm_provider = self.config.get('llm', 'unknown').lower()
        
        # Map LLM to tool format suffix
        self.tool_format_map = {
            'openai': 'openai',
            'gpt': 'openai',
            'claude': 'claude',
            'anthropic': 'claude',
            'llama': 'llamacpp',
            'llamacpp': 'llamacpp',
            'qwen': 'llamacpp',  # Qwen uses llama.cpp format
            'grok': 'grok',
            'xai': 'grok'
        }
        
        self.tool_suffix = self.tool_format_map.get(self.llm_provider, 'openai')
                # Cache for loaded file contents
        self._file_cache: Dict[str, str] = {}
        self._module_cache: Dict[str, str] = {}
        self._prompt_cache: Dict[str, NuCorePrompt] = {}  # Cache NuCorePrompt by intent
        
        # Build intent-to-agent mapping
        self._intent_map = self._build_intent_map()
    
    def _build_intent_map(self) -> Dict[str, dict]:
        """
        Build a mapping of intent -> agent configuration.
        
        :return: Dictionary mapping intent names to agent configs
        """
        intent_map = {}
        
        for agent in self.config.get('agents', []):
            for intent_config in agent.get('intents_to_tools', []):
                intent = intent_config['intent']
                intent_map[intent] = {
                    'agent': agent,
                    'tools': intent_config.get('tools', [])
                }
        
        return intent_map
    
    def _load_file(self, relative_path: str) -> str:
        """
        Load a file from the prompts directory.
        
        :param relative_path: Path relative to the config file's directory
        :return: File contents as string
        """
        if relative_path in self._file_cache:
            return self._file_cache[relative_path]
        
        file_path = self.base_dir / relative_path
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        self._file_cache[relative_path] = content
        return content
    
    def _load_module(self, module_name: str) -> str:
        """
        Load a module by name from the modules list.
        
        :param module_name: Name of the module to load
        :return: Module content
        """
        if module_name in self._module_cache:
            return self._module_cache[module_name]
        
        # Find the module in config
        module = None
        for mod in self.config.get('modules', []):
            if mod['name'] == module_name:
                module = mod
                break
        
        if not module:
            raise ValueError(f"Module '{module_name}' not found in config")
        
        content = self._load_file(module['path'])
        self._module_cache[module_name] = content
        return content
    
    def _resolve_agent_prompt(self, agent: dict) -> str:
        """
        Resolve an agent's prompt with all module includes.
        
        :param agent: Agent configuration dictionary
        :return: Fully resolved prompt content
        """
        # Load the main agent prompt
        prompt_path = agent.get('prompt')
        if not prompt_path:
            raise ValueError(f"Agent missing 'prompt' field")
        
        content = self._load_file(prompt_path)
        
        # Get modules list
        modules = self.config.get('modules', [])
        module_dict = {mod['name']: mod for mod in modules}
        
        # Replace each include's template_key with module content
        for include_name in agent.get('include', []):
            module = module_dict.get(include_name)
            if not module:
                raise ValueError(f"Module '{include_name}' not found in config")
            
            module_content = self._load_module(include_name)
            template_key = module.get('template_key')
            
            if not template_key:
                raise ValueError(f"Module '{include_name}' missing 'template_key'")
            
            content = content.replace(template_key, module_content)
        
        return content
    
    def _load_tools(self, tool_configs: List[dict]) -> List[dict]:
        """
        Load tool schemas from their paths, using provider-specific format.
        
        :param tool_configs: List of tool configuration dicts with 'path'
        :return: List of tool schema dictionaries
        """
        tools = []
        
        for tool_config in tool_configs:
            tool_path = tool_config.get('path')
            if not tool_path:
                continue
            
            # Convert path to provider-specific version
            # e.g., "tools/command.json" -> "tools/command_openai.json"
            path_obj = Path(tool_path)
            base_name = path_obj.stem  # filename without extension
            extension = path_obj.suffix  # .json
            parent = path_obj.parent
            
            # Try provider-specific version first
            provider_path = parent / f"{base_name}_{self.tool_suffix}{extension}"
            file_path = self.base_dir / provider_path
            
            # Fall back to original path if provider-specific doesn't exist
            if not file_path.exists():
                file_path = self.base_dir / tool_path
            
            if not file_path.exists():
                raise FileNotFoundError(f"Tool schema not found: {file_path} (also tried {self.base_dir / provider_path})")
            
            with open(file_path, 'r', encoding='utf-8') as f:
                tool_schema = json.load(f)
                tools.append(tool_schema)
        
        return tools
    
    def _get_rags_from_intent(self, devices: List[dict]) -> RAGData:
        """
        Get RAGData for the matched devices in the intent.
        
        :param devices: List of device dictionaries from router result
        :return: RAGData object containing only the matched devices
        """
        if not self.full_rags:
            return RAGData()
        
        matched_device_ids = {d['device_id'] for d in devices}
        full_rags = self.full_rags
        filtered_documents = []

        for idx, id_ in enumerate(full_rags["ids"]):
            if id_ in matched_device_ids:
                filtered_documents.append(full_rags["documents"][idx])

        filtered_rags = RAGData()
        filtered_rags["documents"] = filtered_documents
        
        return filtered_rags

    def set_max_context_size(self, size:int):
        self.max_context_size = size
    
    def set_full_rags(self, rags:RAGData):
        # full device rags with all properties and commands
        self.full_rags = rags

    def set_summary_rags(self, rags:RAGData):
        # device rags
        self.summary_rags = rags
    
    def get_prompt(self, router_result: dict=None) -> NuCorePrompt:
        """
        Gets a NuCorePrompt based on user query or router result.
        if router_result is None, returns the router_prompt, otherwise returns the intent prompt.

        Uses cached prompt/tools if already built for this intent, only updating
        keywords, devices, and message_history.
        
        :param router_result: The JSON object returned by nucore_router_tool, containing:
                             - intent: The determined intent name
                             - keywords: Extracted keywords
                             - devices: Matched devices with scores
        :param message_history: Optional conversation message history
        :return: NuCorePrompt object with prompt, tools, intent, keywords, devices, and message_history
        :raises ValueError: If intent is not found in router_result or config
        """
        if router_result is None:
            return self._get_router_prompt()

        # Extract intent from router result
        intent = router_result.get('intent')
        if not intent:
            raise ValueError("Router result missing 'intent' field")
        
        if intent not in self._intent_map:
            raise ValueError(f"Intent '{intent}' not found. Available: {list(self._intent_map.keys())}")
        
        # Extract keywords and devices from router result
        keywords = router_result.get('keywords', [])
        devices = router_result.get('devices', [])
        
        # Check if we have a cached prompt/tools for this intent
        if intent in self._prompt_cache:
            cached = self._prompt_cache[intent]
            # Update dynamic fields only
            cached.keywords = keywords
            cached.devices = self._get_rags_from_intent(devices)
            return cached
        
        # Not cached - build it
        intent_config = self._intent_map[intent]
        agent = intent_config['agent']
        tool_configs = intent_config['tools']
        
        # Resolve the agent's prompt with all includes
        prompt = self._resolve_agent_prompt(agent)
        
        # Load all tools for this intent
        tools = self._load_tools(tool_configs)
        
        # Create and cache the NuCorePrompt
        nucore_prompt = NuCorePrompt(
            prompt=prompt,
            tools=tools,
            intent=intent,
            keywords=keywords,
            max_context_size=self.max_context_size
        )
        nucore_prompt.set_device_rags(self._get_rags_from_intent(devices))
        
        self._prompt_cache[intent] = nucore_prompt
        return nucore_prompt
    
    def _get_router_prompt(self) -> NuCorePrompt:
        """
        Get the router prompt with all includes resolved as a NuCorePrompt object.
        Uses caching - builds once, reuses on subsequent calls.
        
        :return: NuCorePrompt object for the router
        """
        # Check cache first
        if ROUTER_INTENT in self._prompt_cache:
            cached = self._prompt_cache[ROUTER_INTENT]
            return cached
        
        # Not cached - build it
        router_config = self.config.get('router')
        if not router_config:
            raise ValueError("'router' section not found in config")
        
        # Load router prompt
        prompt_path = router_config.get('path')
        if not prompt_path:
            raise ValueError("Router missing 'path' field")
        
        content = self._load_file(prompt_path)
        
        # Get modules list
        modules = self.config.get('modules', [])
        module_dict = {mod['name']: mod for mod in modules}
        
        # Replace includes
        for include_name in router_config.get('include', []):
            module = module_dict.get(include_name)
            if not module:
                raise ValueError(f"Module '{include_name}' not found in config")
            
            module_content = self._load_module(include_name)
            template_key = module.get('template_key')
            
            if not template_key:
                raise ValueError(f"Module '{include_name}' missing 'template_key'")
            
            content = content.replace(template_key, module_content)
        
        # Load router tools
        tool_configs = router_config.get('tools', [])
        tools = self._load_tools(tool_configs)
        
        # Create and cache the router NuCorePrompt
        router_prompt = NuCorePrompt(
            prompt=content,
            tools=tools,
            intent=ROUTER_INTENT,
            keywords=[],
            max_context_size=self.max_context_size
        )
        router_prompt.set_device_rags(self.summary_rags)
        
        self._prompt_cache[ROUTER_INTENT] = router_prompt
        return router_prompt
    
    def get_available_intents(self) -> List[str]:
        """
        Get list of all available intents.
        
        :return: List of intent names
        """
        return list(self._intent_map.keys())
    
    def get_llm_name(self) -> str:
        """
        Get the LLM name from the configuration.
        
        :return: LLM name (e.g., 'qwen')
        """
        return self.config.get('llm', 'unknown')
    
    def get_tool_format(self) -> str:
        """
        Get the tool format being used for this LLM.
        
        :return: Tool format suffix (e.g., 'openai', 'claude', 'llamacpp', 'grok')
        """
        return self.tool_suffix


# Example usage
if __name__ == "__main__":
    # Initialize the orchestrator with Qwen configuration
    orchestrator = PromptOrchestrator("src/prompts/qwen_config.json")
    
    print(f"LLM: {orchestrator.get_llm_name()}")
    print(f"Tool format: {orchestrator.get_tool_format()}")
    print(f"Available intents: {orchestrator.get_available_intents()}")
    
    print("\n" + "="*80)
    print("ROUTER PHASE:")
    print("="*80)
    router_prompt = orchestrator._get_router_prompt()
    
    # Simulate router tool output
    router_result = {
        "intent": "command_control",
        "keywords": [
            {"keyword": "pool", "reasoning": "Pool device control"},
            {"keyword": "on", "reasoning": "Turn on command"}
        ],
        "devices": [
            {
                "device_id": "ZY003_1",
                "score": 6,
                "matched_terms": ["ZWave Pool", "Pool", "On"],
                "reasoning": "Matches: pool(2+2) + on(2) = 6pts"
            }
        ]
    }
    
    print("\n" + "="*80)
    print("PROCESSING ROUTER RESULT: command_control")
    print("="*80)
    nucore_prompt = orchestrator.process_intent(router_result)
    print(f"Intent: {nucore_prompt.intent}")
    print(f"Prompt length: {len(nucore_prompt.prompt)} chars")
    print(f"Tools loaded: {len(nucore_prompt.tools)}")
    print(f"Tool names: {[t.get('name', 'unnamed') for t in nucore_prompt.tools]}")
    print(f"Keywords: {len(nucore_prompt.keywords)}")
    print(f"Matched devices: {len(nucore_prompt.devices)}")
    print(f"Device IDs: {[d['device_id'] for d in nucore_prompt.devices]}")
    
    # Another example
    router_result_2 = {
        "intent": "routine_automation",
        "keywords": [
            {"keyword": "range", "reasoning": "EV battery range condition"},
            {"keyword": "charging", "reasoning": "Start/stop charging control"}
        ],
        "devices": [
            {
                "device_id": "n003_chargea5rf7219",
                "score": 14,
                "matched_terms": ["Charging Info", "Estimated Range"],
                "reasoning": "Primary device for EV range monitoring"
            }
        ]
    }
    
    print("\n" + "="*80)
    print("PROCESSING ROUTER RESULT: routine_automation")
    print("="*80)
    nucore_prompt = orchestrator.process_intent(router_result_2)
    print(f"Intent: {nucore_prompt.intent}")
    print(f"Prompt length: {len(nucore_prompt.prompt)} chars")
    print(f"Tools loaded: {len(nucore_prompt.tools)}")
    print(f"Tool names: {[t.get('name', 'unnamed') for t in nucore_prompt.tools]}")
    print(f"Keywords: {len(nucore_prompt.keywords)}")
    print(f"Matched devices: {len(nucore_prompt.devices)}")
    print(f"Device IDs: {[d['device_id'] for d in nucore_prompt.devices]}")

