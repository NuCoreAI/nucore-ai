# RAG Embedder
# This module provides functionality to embed documents for retrieval-augmented generation (RAG) tasks.
# It uses sentence-transformers library for local embedding with support for various pre-trained models.

import numpy as np
from typing import Union, List, TYPE_CHECKING

# Lazy import - only load heavy dependencies when actually needed
if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

class LocalEmbedder:
    def __init__(self, model_name: str = 'all-MiniLM-L6-v2', device: str = None, normalize_embeddings: bool = True):
        """
        Initializes the Embedder with a sentence-transformers model.
        
        :param model_name: Name of the sentence-transformers model to use. 
                          Popular options include:
                          - 'all-MiniLM-L6-v2' (default, fast and efficient)
                          - 'all-mpnet-base-v2' (higher quality, slower)
                          - 'paraphrase-multilingual-MiniLM-L12-v2' (multilingual)
        :param device: Device to run the model on ('cuda', 'cpu', or None for auto-detection)
        :param normalize_embeddings: Whether to normalize embeddings to unit length
        """
        # Lazy import - only import when creating instance
        from sentence_transformers import SentenceTransformer
        
        self.model_name = model_name
        self.normalize_embeddings = normalize_embeddings
        # SentenceTransformer automatically caches to ~/.cache/huggingface/
        # First run downloads, subsequent runs load from cache
        self.model = SentenceTransformer(model_name, device=device)
        
    def embed_document(self, document: Union[str, List[str]]):
        """
        Embeds a document or list of documents using the sentence-transformers model.
        
        :param document: The document(s) to be embedded. Can be a single string or list of strings.
        :return: The embedding(s) as a numpy array or list of floats (for single document).
        :raises ValueError: If the document is empty or None.
        """
        if document is None:
            raise ValueError("Document cannot be None")
        
        if isinstance(document, str):
            if not document.strip():
                raise ValueError("Document cannot be empty")
        elif isinstance(document, list):
            if len(document) == 0:
                raise ValueError("Document list cannot be empty")
            if any(not doc.strip() for doc in document):
                raise ValueError("Document list contains empty documents")
        else:
            raise ValueError("Document must be a string or list of strings")
        
        try:
            # Generate embeddings using sentence-transformers
            embeddings = self.model.encode(
                document,
                convert_to_numpy=True,
                normalize_embeddings=self.normalize_embeddings,
                show_progress_bar=False
            )
            
            # If single document, return as list for compatibility
            if isinstance(document, str):
                return embeddings.tolist()
            else:
                return embeddings
                
        except Exception as e:
            print(f"Error occurred during embedding: {e}")
            return None
    
    def get_embedding_dimension(self) -> int:
        """
        Returns the dimensionality of the embeddings produced by the model.
        
        :return: Integer representing the embedding dimension
        """
        return self.model.get_sentence_embedding_dimension()
    
    def encode_batch(self, documents: List[str], batch_size: int = 32):
        """
        Efficiently encodes a large batch of documents.
        
        :param documents: List of documents to embed
        :param batch_size: Batch size for encoding
        :return: Numpy array of embeddings
        """
        if not documents:
            raise ValueError("Documents list cannot be empty")
        
        try:
            embeddings = self.model.encode(
                documents,
                batch_size=batch_size,
                convert_to_numpy=True,
                normalize_embeddings=self.normalize_embeddings,
                show_progress_bar=True
            )
            return embeddings
        except Exception as e:
            print(f"Error occurred during batch embedding: {e}")
            return None


if __name__ == "__main__":
    print("=" * 60)
    print("Testing Embedder with Sentence Transformers")
    print("=" * 60)
    
    # Initialize embedder
    print("\n1. Initializing embedder with default model (all-MiniLM-L6-v2)...")
    embedder = Embedder()
    print(f"   Model loaded: {embedder.model_name}")
    print(f"   Embedding dimension: {embedder.get_embedding_dimension()}")
    
    # Test single document embedding
    print("\n2. Testing single document embedding...")
    test_doc = "This is a test document about machine learning and AI."
    embedding = embedder.embed_document(test_doc)
    if embedding:
        print(f"   Document: '{test_doc}'")
        print(f"   Embedding shape: {len(embedding)} dimensions")
        print(f"   First 5 values: {embedding[:5]}")
        
        # Check if normalized
        norm = np.linalg.norm(embedding)
        print(f"   Embedding norm: {norm:.6f} (should be ~1.0 if normalized)")
    
    # Test multiple documents
    print("\n3. Testing multiple document embedding...")
    test_docs = [
        "The cat sat on the mat.",
        "Dogs are loyal companions.",
        "Python is a programming language."
    ]
    embeddings = embedder.embed_document(test_docs)
    if embeddings is not None:
        print(f"   Number of documents: {len(test_docs)}")
        print(f"   Embeddings shape: {embeddings.shape}")
        
        # Compute similarity between first two documents
        sim = np.dot(embeddings[0], embeddings[1])
        print(f"   Similarity (cat vs dog): {sim:.4f}")
        sim2 = np.dot(embeddings[0], embeddings[2])
        print(f"   Similarity (cat vs python): {sim2:.4f}")
    
    # Test batch encoding
    print("\n4. Testing batch encoding with progress bar...")
    batch_docs = [
        f"This is document number {i} about various topics."
        for i in range(10)
    ]
    batch_embeddings = embedder.encode_batch(batch_docs, batch_size=4)
    if batch_embeddings is not None:
        print(f"   Processed {len(batch_docs)} documents")
        print(f"   Batch embeddings shape: {batch_embeddings.shape}")
    
    # Test error handling
    print("\n5. Testing error handling...")
    try:
        embedder.embed_document("")
        print("   ERROR: Should have raised ValueError for empty string")
    except ValueError as e:
        print(f"   ✓ Correctly raised ValueError: {e}")
    
    try:
        embedder.embed_document(None)
        print("   ERROR: Should have raised ValueError for None")
    except ValueError as e:
        print(f"   ✓ Correctly raised ValueError: {e}")
    
    try:
        embedder.embed_document([])
        print("   ERROR: Should have raised ValueError for empty list")
    except ValueError as e:
        print(f"   ✓ Correctly raised ValueError: {e}")
    
    # Test semantic similarity
    print("\n6. Testing semantic similarity...")
    similar_docs = [
        "The quick brown fox jumps over the lazy dog.",
        "A fast brown fox leaps over a sleepy dog.",
        "Cats are independent animals."
    ]
    sim_embeddings = embedder.embed_document(similar_docs)
    if sim_embeddings is not None:
        sim_1_2 = np.dot(sim_embeddings[0], sim_embeddings[1])
        sim_1_3 = np.dot(sim_embeddings[0], sim_embeddings[2])
        print(f"   Similarity (fox sentence 1 vs 2): {sim_1_2:.4f}")
        print(f"   Similarity (fox sentence 1 vs cat): {sim_1_3:.4f}")
        print(f"   ✓ Similar sentences have higher similarity: {sim_1_2 > sim_1_3}")
    
    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
