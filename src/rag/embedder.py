# RAG Embedder
# This module provides functionality to embed documents for retrieval-augmented generation (RAG) tasks.
# It uses llama.cpp server for embedding rather than local embedding. This way, we are free 
# to use any model using APIs rather depending on monstrous local embedding tools such as chromadb.
import requests
import json

class Embedder:
    def __init__(self, embedder_url: str):
        """
        Initializes the RAGEmbedder with the given server URL.
        :param embedder_url: URL of the llama.cpp server for embedding.
        """
        self.embedder_url = embedder_url

    def embed_document(self, document:str):
        """
         embeds a document using the embedding model
         :param document: The document to be embedded.
         :return: The embedding of the document as a list of floats.
         :raises ValueError: If the document is empty or if the embedding fails.
         :raises Exception: If there is an error during the embedding process.
        """
        if not document:
            raise ValueError("Document cannot be empty")
        if not self.embedder_url:
            raise ValueError("Embedder URL is not set. Please provide a valid URL to use embedding.")
        
        headers = {
            "Content-Type": "application/json"
        }

        body = {
            "input": document
        }

        try:
            response = requests.post(self.embedder_url, headers=headers, data=json.dumps(body))
            response.raise_for_status()
            if response.status_code != 200:
                print(f"Error: {response.status_code} - {response.text} - data size = {len(document)}")
                return None
            result = response.json()
            if "data" not in result:
                print("No embedding found in response")
                return None
            data = result["data"][0]
            if "embedding" not in data:
                print("No embedding key in data")
                return None
            embedding = data["embedding"]
            if not isinstance(embedding, list):
                print("No embedding key in data")
                return None
            if len(embedding) == 0:
                print("Embedding is empty")
                return None

            return embedding 
            # Convert to float if necessary
            #embedding_result = [float(x) for x in embedding]
            # Normalize the embedding
            embedding_result = np.array(embedding) #, dtype=np.float32)
            normalized_embedding = embedding_result / np.linalg.norm(embedding_result)
            if normalized_embedding is None: 
                print("Embedding is zero, returning original embedding")
                return embedding_result.tolist()
            norm = round(np.linalg.norm(normalized_embedding), 5)
            if norm < 0.98 or norm > 1.02:
                print("Normalization failed - norm is not close to 1.0, norm: {norm}")

            return normalized_embedding.tolist() 
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            return None
        
        except Exception as e:
            print(f"Error occurred: {e}")
            return None
        

