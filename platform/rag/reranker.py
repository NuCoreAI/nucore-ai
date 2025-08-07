import json,requests,re
from ai_iox_workflow.config import AIConfig
config= AIConfig()

class Reranker:
    def __init__(self):
        """
            Reranks documents based on a query using the BGE reranker model.
        """

    def is_question(self, text):
        return text.strip().endswith("?") or bool(re.match(r"^(who|what|when|where|why|how)\b", text.strip().lower()))

    def compute(self, query:str, documents:list):
        """
        Computes the relevance of documents based on a query using the BGE reranker model.
        :param query: The query string to evaluate.
        :param documents: A list of document strings to rank.
        :return: A list of ranked documents based on their relevance to the query.
        """
        payload = {
            "model": "bge-reranker",
            "query": "[QUESTION] " + query if self.is_question(query) else "[STATEMENT] " + query,
            "query": query ,
            "documents": documents 
        }
        response = requests.post(config.getRerankerURL(), json=payload)

        response.raise_for_status()
        if response.status_code != 200:
            print(f"Error: {response.status_code} - {response.text}")
            return None

        data = response.json()
        if data:
            # Sort the 'results' list based on 'relevance_score' in descending order
            return sorted(data['results'], key=lambda x: x['relevance_score'], reverse=True)
        
        return None