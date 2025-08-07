import json,requests,re

class Reranker:
    def __init__(self, reranker_url:str):
        """
            Reranks documents based on a query using the BGE reranker model.
        :param reranker_url: The URL of the BGE reranker service (OpenAI based)
        """
        self.__reranker_url__ = reranker_url

    def is_question(self, text):
        return text.strip().endswith("?") or bool(re.match(r"^(who|what|when|where|why|how)\b", text.strip().lower()))

    def compute(self, query:str, documents:list):
        if self.__reranker_url__ is None:
            print("Reranker URL is not set. Please provide a valid URL if you want to use reranking.")
            return None
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

        response = requests.post(self.__reranker_url__, json=payload)

        response.raise_for_status()
        if response.status_code != 200:
            print(f"Error: {response.status_code} - {response.text}")
            return None

        data = response.json()
        if data:
            # Sort the 'results' list based on 'relevance_score' in descending order
            return sorted(data['results'], key=lambda x: x['relevance_score'], reverse=True)
        
        return None