import json
from pathlib import Path

"""
Tool definitions for RAG (Retrieval-Augmented Generation) processing.
Converts tools from a JSON file into RAG chunks suitable for use in AI workflows/embeddings.
The tools are formatted with a title, content, and examples, which can be used to enhance the context for AI models.
"""
from rag.rag_data_struct import RAGData
from rag.rag_formatter import RAGFormatter


class ToolsRAGFormatter(RAGFormatter):
    def __init__(self, indent_str: str = "    ", prefix: str = ""):
        """
        Initialize the formatter with the path to the tools JSON file.
        """

    def format(self, **kwargs):
        """
        Convert the formatted tools into a list of RAG documents.
        Each document contains an ID, category, and content.
        :param tools_path must be provided 
        :return: RAGData object containing the tools documents.
        :raises FileNotFoundError: If the tools_path does not exist.
        :raises ValueError: If the tools_path is not provided.
        :raises Exception: If the tools_path is not a file or if it contains no valid JSON data.
        """
        tools_path=kwargs["tools_path"] if "tools_path" in kwargs else None 
        
        if not Path(tools_path).exists():
            raise FileNotFoundError(f"Tools file not found: {tools_path}")

        with open(tools_path, "r") as f:
            tools_data = json.load(f)

        tools_rag:RAGData = RAGData() 
        for tool_def in tools_data:
            func = tool_def["function"]
            name = func["name"]
            category = func.get("category", "General")
            description = func.get("description", "")
            examples = func.get("examples", [])
            content = f"***Tool: {name}***\n\nCategory: {category}\n\n{description}\n\n***Examples***\n"
            if examples:
                content += "\n".join(f"\n{example}" for example in examples) 
            else:
                content += "No examples provided."
            tools_rag.add_document(content, [], name, {"category": category})

        return tools_rag 

