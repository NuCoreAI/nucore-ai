from abc import ABC, abstractmethod
from .rag_data_struct import RAGData

"""
An abstract method for all RAG formatters
"""

class RAGFormatter(ABC):
    @abstractmethod
    def format(self, **kwargs):
        pass

    def dump(self, rag_docs: RAGData, dump_file_path: str = None): 
        """
        Dumps the formatted data to a file or prints it to the console.
        :param rag_docs: RAGData object containing the formatted documents.
        :param dump_file_path: Optional path to save the formatted data. If None, prints to console.
        """
        if not rag_docs or not isinstance(rag_docs, RAGData):
            raise ValueError("No RAG data to dump")
        if dump_file_path:
            with open(dump_file_path, "w", encoding="utf8") as f:
                for doc in rag_docs["documents"]:
                    f.write(doc)
        else:
            for doc in rag_docs["documents"]:
                print(doc)

