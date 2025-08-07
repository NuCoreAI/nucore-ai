# rag processor

import json, os
import chromadb
import requests
import numpy as np
from .rag_data_struct import RAGData
from .reranker import Reranker


class RAGProcessor:
    def __init__(self, collection_path, collection_name, reranker_url:str = None):
        """
        Initializes the RAGProcessor with a collection name and database path.
        :param collection_name: The name of the collection to use.
        :param collection_path: The path to the collection file.
        """
        if not collection_name or not isinstance(collection_name, str):
            raise ValueError("Collection name must be a non-empty string")

        if not collection_path or not isinstance(collection_path, str):
            raise ValueError("Collection path must be a non-empty string")
        
        path = os.path.join(collection_path, f"{collection_name}_db")

        self.db = chromadb.PersistentClient(path=path)
        if not self.db:
            raise ValueError(f"Failed to connect to the database at {path}")
        self.collection = self.db.get_or_create_collection(collection_name)
        self.reranker = Reranker(reranker_url=reranker_url)

    def embed_document(self, document:str):
        """
         embeds a document using the embedding model
        """
        if not document:
            raise ValueError("Document cannot be empty")
        
        headers = {
            "Content-Type": "application/json"
        }

        body = {
            "input": document
        }

        try:
            response = requests.post(self.config.getEmbeddingURL(), headers=headers, data=json.dumps(body))
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
        
    def add_update(self, collection_data:RAGData):
        """
            adds to the collection to the collection with its embedding and metadata
        """
        if not collection_data or not isinstance(collection_data, RAGData):
            raise ValueError("Collection data must be a non-empty dictionary")

        if "documents" not in collection_data or "embeddings" not in collection_data or "metadatas" not in collection_data:
            raise ValueError("Collection data must contain 'documents', 'embeddings', and 'metadatas' keys")  

        if len(collection_data["documents"]) == 0 or len(collection_data["embeddings"]) == 0 or len(collection_data["ids"])== 0:
            print("Nothing changed ... ")
            return 


        return self.collection.upsert(
            documents=collection_data["documents"],
            ids=collection_data.get("ids", []),
            embeddings=collection_data["embeddings"],
            metadatas=collection_data["metadatas"]
        )

    def __compare_documents__(self, collection_data:RAGData):
        """
        Compares the collection data with the existing collection.
        Returns a dictionary with the differences.
        """
        if not collection_data or not isinstance(collection_data, RAGData):
            raise ValueError("Collection data must be a non-empty dictionary")

        if "documents" not in collection_data or "ids" not in collection_data or "metadatas" not in collection_data:

            raise ValueError("Collection data must contain 'documents', 'ids', and 'metadatas' keys")   

        existing_collection = self.collection.get(ids=None, include=["documents", "metadatas"])
        existing_ids = existing_collection.get("ids", [])

        
        new_ids = set(collection_data.get("ids", []))
        existing_ids_set = set(existing_ids)
        added_ids = new_ids - existing_ids_set
        unchanged_indexes = []
        removed_ids = existing_ids_set - new_ids

        for existing_id in existing_ids:
            existing_assets = self.collection.get(ids=[existing_id], include=["documents", "metadatas"])
            if not existing_assets or len(existing_assets["documents"]) == 0:
                print(f"ID {existing_id} exists in the collection but has no associated documents")
                continue

            existing_document= existing_assets["documents"][0]

            index=0
            for id in collection_data["ids"]:
                if id == existing_id:
                    if collection_data["documents"][index] == existing_document:
                        unchanged_indexes.append(index)
                        break
                index += 1

        return {
            "added": list(added_ids),
            "unchanged": unchanged_indexes,
            "removed": list(removed_ids)
        }
    
    def compare_documents_update_collection(self, documents:RAGData):
        """
        Compare the documents in the collection with the provided documents.
        Returns a dictionary with added, changed, and removed IDs.
        Those that removed or changed will be updated in the collection.
        """
        if not documents or not isinstance(documents, RAGData):
            raise ValueError("Documents must be a non-empty list")
        
        results = self.__compare_documents__(documents)
        if not results:
            print("No results found in the collection")
            return None
        
        #don't care about added
        # added = results.get("added") 
        unchanged = results.get("unchanged") 
        removed = results.get("removed")

        if removed and len(removed) > 0:
            self.remove(removed)

        result: RAGData = RAGData() 

        for i in range(len(documents["ids"])):
            if i in unchanged:
                continue
            doc_content = documents["documents"][i]
            embedding = self.embed_document(doc_content)
            if embedding is not None:
                result["documents"].append(doc_content)
                result["embeddings"].append(embedding)
                result["metadatas"].append(documents["metadatas"][i])
                result["ids"].append(documents["ids"][i])

        return self.add_update(result)

    def query(self, query_text:str, n_results:int=5, rerank=True):
        """
        Queries the collection with the given text and returns the top n results.
        :param query_text: The text to query the collection with.
        :param n_results: The number of results to return.
        :param rerank: Whether to rerank the results using the Reranker.
        :return: A dictionary with the top n results.
        :raises ValueError: If the query text is empty.
        """
        if not query_text:
            raise ValueError("Query text cannot be empty")
        query_text = f"query: {query_text.strip()}".strip()

        query_embedding = self.embed_document(query_text)
        if not query_embedding:
            print("Failed to embed query text")
            return None

        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=n_results
        )
        
        if not results or "documents" not in results or len(results["documents"]) == 0:
            print("No results found")
            return None

        print(f"\n\n*********************Top {n_results} Query Results (Before Reranker):********************\n")
        for i in range(n_results):
            print(f"{i+1}. {results['ids'][0][i]} - {results['distances'][0][i]} - {results['relevance_scores'][0][i] if 'relevance_scores' in results else None}")
            i+=1
        print("\n\n***************************************************************\n\n")
         

        out = RAGData()
        if rerank:
            reranked_list = self.reranker.compute(query_text, results["documents"][0])
            if reranked_list is None:
                return results
            if len(reranked_list) == 0:
                print("Reranking returned no results")
                return results
            for reranked in reranked_list:
                index = reranked["index"]
                relevance_score = reranked["relevance_score"]
                if index < n_results :
                    out.add_document(
                        results["documents"][0][index],
                        None,
                        results["ids"][0][index],
                        results["metadatas"][0][index],
                        results["distances"][0][index] if "distances" in results else None,
                        relevance_score
                    )
        else:
            for i in range(n_results):
                out.add_document(
                    results["documents"][0][i],
                    None,
                    results["ids"][0][i],
                    results["metadatas"][0][i],
                    results["distances"][0][i] if "distances" in results else None,
                    results["relevance_scores"][0][i] if "relevance_scores" in results else None
                )


        return out

    def process(self, rag_docs: RAGData):
        """
        Process the input data, add/update/remove extra embededing and return the formatted RAG documents.
        :param kwargs: Additional arguments for the formatter.
        :if "dump" in kwargs and kwargs["dump"] == True, dumps the formatted data to screen or file
        :if "dump_file_path" in kwargs, dumps the formatted data to the specified file path.
        :return: RAG documents structured as RAGData. 
        """
        if not rag_docs:
            raise ValueError("RAG documents are not set")

        self.compare_documents_update_collection(rag_docs)
        return rag_docs
    
    def remove(self, ids:list):
        """
        Removes documents from the collection by their IDs.
        :param ids: A list of IDs to remove from the collection.
        :return: The result of the removal operation.
        """
        if not ids or not isinstance(ids, list):
            raise ValueError("IDs must be a non-empty list")
        
        return self.collection.delete(ids=ids)
    
    def dump(self):
        """
        Dumps the collection data to the console.
        :return: None
        """
        if not self.collection:
            print("No collection found")
            return
        
        documents = self.collection.get(include=["documents", "metadatas"])
        if not documents or "documents" not in documents or len(documents["documents"]) == 0:
            print("No documents found in the collection")
            return
        
        for i, doc in enumerate(documents["documents"]):
            print(f"Document {i}: {doc}")
            print(f"Metadata: {documents['metadatas'][i]}")