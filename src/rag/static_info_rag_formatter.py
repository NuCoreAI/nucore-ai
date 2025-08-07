from pathlib import Path

"""
Tool definitions for Static Information Retrieval
Converts static information from text files into RAG chunks suitable for use in AI workflows/embeddings.
The static information is formatted with a title, category, content, and exmaples. They are separated by "---end chuck---".
"""
from .rag_data_struct import RAGData
from .rag_formatter import RAGFormatter


class StaticInfoRAGFormatter(RAGFormatter):
    def __init__(self, indent_str: str = "    ", prefix: str = ""):
        """
        Initialize the formatter with the path to the tools JSON file.
        """

    def format(self, **kwargs):
        """
        Convert the formatted tools into a list of RAG documents.
        Each document contains an ID, category, and content.
        :param static_info_path is mandatory and should point to the directory containing static information files.
        :return: RAGData object containing the static information documents.
        :raises FileNotFoundError: If the static_info_path does not exist.
        :raises ValueError: If the static_info_path is not provided.
        :raises Exception: If the static_info_path is not a directory or if it contains no files.
        """
        if "static_info_path" not in kwargs:
            raise ValueError("static_info_path is required to format static information.")

        static_info_path = kwargs["static_info_path"]

        # Check if the static_info_path exists
        if not Path(static_info_path).exists():
            raise FileNotFoundError(f"Static info file not found: {static_info_path}")
        
        #now, go through the static info directory, read each file, and then convert into RAGData
        static_info_path = Path(static_info_path)

        # If it's a directory, read all files in the directory
        static_rag = RAGData() 
        for file in static_info_path.glob("*.rag"):
            with open(file, "r") as f:
                content = f.read()
                # Split the content by "---end chuck---" to separate different chunks
                chunks = content.split("---end chuck---")
                for chunk in chunks:
                    if chunk.strip():
                        # get the type and category from the chunk
                        lines = chunk.strip().split("\n")
                        if len(lines) >= 2:
                            name = lines[0].strip()
                            doc_type = lines[1].strip()
                            category = lines[2].strip()
                            static_rag.add_document(chunk.strip(), [], name, {"type": doc_type, "category": category})

        return static_rag 

