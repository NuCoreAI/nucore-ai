"""
A universal NuCore class for managing and querying device and tool data and make into RAG friendly structure
"""

class RAGData(dict):
    """
    A class to represent a RAG (Retrieval-Augmented Generation) data structure.
    It is a dictionary that can be used to store and retrieve RAG documents.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self["documents"] = []
        self["embeddings"] = []
        self["ids"] = []
        self["metadatas"] = []
        self["distances"] = []
        self["relevance_scores"] = []

    def add_document(self, document: str, embedding: list, id: str, metadata: dict, distance: float = None, relevance_score: float = None):
        """
        Adds a document, its embedding, id, and metadata to the RAG data structure.
        """
        if not isinstance(document, str) or (not isinstance(embedding, list) and embedding is not None)  or not isinstance(id, str) or not isinstance(metadata, dict):
            raise ValueError("Invalid types for document, embedding, id, or metadata")

        self["documents"].append(document)
        self["embeddings"].append(embedding)
        self["ids"].append(id)
        self["metadatas"].append(metadata)
        self["distances"].append(distance)
        self["relevance_scores"].append(relevance_score)
        return self


    def __add__(self, other):
        if not isinstance(other, RAGData):
            return NotImplemented

        self["documents"]  += other["documents"]
        self["embeddings"] += other["embeddings"]
        self["ids"] += other["ids"]
        self["metadatas"] += other["metadatas"]
        self["distances"] += other["distances"]
        self["relevance_scores"] += other["relevance_scores"]
        return self