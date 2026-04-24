"""
Simple SQLite Vector Database
No dependencies except SQLite and basic Python. Uses exact search (not approximate).
"""

import sqlite3
import json
import os
from typing import List, Dict, Any, Optional


class RAGSQLiteDB:
    """Simple exact-search vector database using SQLite."""
    
    def __init__(self, db_path: str):
        """
        :param db_path: Path to SQLite database file
        """
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else '.', exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()
    
    def _init_db(self):
        """Create tables if they don't exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                id TEXT PRIMARY KEY,
                vector BLOB NOT NULL,
                metadata TEXT,
                document TEXT
            )
        """)
        self.conn.commit()
    
    def add(self, ids: List[str], embeddings: List[List[float]], 
            documents: Optional[List[str]] = None, 
            metadatas: Optional[List[Dict]] = None):
        """
        Add embeddings to the database.
        
        :param ids: List of unique IDs
        :param embeddings: List of embedding vectors
        :param documents: Optional list of document texts
        :param metadatas: Optional list of metadata dicts
        """
        if not ids or not embeddings:
            raise ValueError("ids and embeddings cannot be empty")
        
        if len(ids) != len(embeddings):
            raise ValueError("ids and embeddings must have same length")
        
        rows = []
        for i, (id_, vec) in enumerate(zip(ids, embeddings)):
            vec_blob = json.dumps(vec)  # Store as JSON for simplicity
            meta = json.dumps(metadatas[i]) if metadatas and i < len(metadatas) else None
            doc = documents[i] if documents and i < len(documents) else None
            rows.append((id_, vec_blob, meta, doc))
        
        self.conn.executemany(
            "INSERT OR REPLACE INTO embeddings (id, vector, metadata, document) VALUES (?, ?, ?, ?)",
            rows
        )
        self.conn.commit()
    
    def upsert(self, ids: List[str], embeddings: List[List[float]], 
               documents: Optional[List[str]] = None, 
               metadatas: Optional[List[Dict]] = None):
        """
        Upsert embeddings (insert or update if exists).
        Alias for add() since SQLite INSERT OR REPLACE handles upsert.
        
        :param ids: List of unique IDs
        :param embeddings: List of embedding vectors
        :param documents: Optional list of document texts
        :param metadatas: Optional list of metadata dicts
        """
        self.add(ids, embeddings, documents, metadatas)

    def refresh(self, ids: List[str], embeddings: List[List[float]], 
                documents: Optional[List[str]] = None, 
                metadatas: Optional[List[Dict]] = None):
        """
        Replace entire collection with new data (removes all existing embeddings first).
        
        :param ids: List of unique IDs
        :param embeddings: List of embedding vectors
        :param documents: Optional list of document texts
        :param metadatas: Optional list of metadata dicts
        """
        # Delete all existing embeddings
        self.conn.execute("DELETE FROM embeddings")
        self.conn.commit()
        
        # Add new embeddings
        self.add(ids, embeddings, documents, metadatas)
    
    def query(self, query_embeddings: List[float], n_results: int = 5, 
              where: Optional[Dict] = None,
              include: Optional[List[str]] = None) -> Dict[str, List]:
        """
        Query for similar embeddings using exact cosine similarity.
        
        :param query_embeddings: Query embedding vector
        :param n_results: Number of results to return
        :param where: Optional metadata filter (simple equality only)
        :param include: What to include in results: ["documents", "metadatas", "distances"]
        :return: Dict with "ids" and optionally "documents", "metadatas", "distances"
        """
        include = set(include or [])
        
        # Normalize query vector for cosine similarity
        query_norm = sum(x * x for x in query_embeddings) ** 0.5
        if query_norm == 0:
            query_norm = 1
        query_vec = [x / query_norm for x in query_embeddings]
        
        # Fetch all embeddings (for exact search)
        cursor = self.conn.execute("SELECT id, vector, metadata, document FROM embeddings")
        
        results = []
        for row in cursor:
            id_, vec_json, meta_json, doc = row
            
            # Parse metadata and apply filter
            metadata = json.loads(meta_json) if meta_json else {}
            if where:
                if not self._matches_filter(metadata, where):
                    continue
            
            # Calculate cosine similarity
            vec = json.loads(vec_json)
            
            # Normalize stored vector
            vec_norm = sum(x * x for x in vec) ** 0.5
            if vec_norm == 0:
                vec_norm = 1
            vec = [x / vec_norm for x in vec]
            
            # Cosine similarity = dot product of normalized embeddings
            similarity = sum(q * v for q, v in zip(query_vec, vec))
            distance = 1.0 - similarity  # Convert to distance (lower is better)
            
            results.append({
                'id': id_,
                'distance': distance,
                'metadata': metadata,
                'document': doc
            })
        
        # Sort by distance (ascending) and take top n
        results.sort(key=lambda x: x['distance'])
        results = results[:n_results]
        
        # Format output
        output = {
            'ids': [[r['id'] for r in results]]  # Wrapped in list for compatibility
        }
        
        if 'distances' in include:
            output['distances'] = [[r['distance'] for r in results]]
        
        if 'metadatas' in include:
            output['metadatas'] = [[r['metadata'] for r in results]]
        
        if 'documents' in include:
            output['documents'] = [[r['document'] for r in results]]
        
        return output
    
    def _matches_filter(self, metadata: Dict, where: Dict) -> bool:
        """Simple metadata filtering - only supports exact equality."""
        for key, value in where.items():
            if metadata.get(key) != value:
                return False
        return True
    
    def get(self, ids: List[str], include: Optional[List[str]] = None) -> Dict[str, List]:
        """
        Get embeddings by IDs.
        
        :param ids: List of IDs to retrieve
        :param include: What to include: ["documents", "metadatas", "embeddings"]
        :return: Dict with requested data
        """
        include = set(include or [])
        
        if not ids:
            return {'ids': [], 'documents': [], 'metadatas': [], 'embeddings': []}
        
        placeholders = ','.join('?' * len(ids))
        cursor = self.conn.execute(
            f"SELECT id, vector, metadata, document FROM embeddings WHERE id IN ({placeholders})",
            ids
        )
        
        output = {'ids': []}
        if 'embeddings' in include:
            output['embeddings'] = []
        if 'metadatas' in include:
            output['metadatas'] = []
        if 'documents' in include:
            output['documents'] = []
        
        for row in cursor:
            id_, vec_json, meta_json, doc = row
            output['ids'].append(id_)
            
            if 'embeddings' in include:
                output['embeddings'].append(json.loads(vec_json))
            
            if 'metadatas' in include:
                output['metadatas'].append(json.loads(meta_json) if meta_json else None)
            
            if 'documents' in include:
                output['documents'].append(doc)
        
        return output
    
    def delete(self, ids: List[str]):
        """Delete embeddings by IDs."""
        placeholders = ','.join('?' * len(ids))
        self.conn.execute(f"DELETE FROM embeddings WHERE id IN ({placeholders})", ids)
        self.conn.commit()
    
    def count(self) -> int:
        """Return total number of embeddings."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM embeddings")
        return cursor.fetchone()[0]
    
    def close(self):
        """Close database connection."""
        self.conn.close()


# Example usage
if __name__ == "__main__":
    # Create database
    db = RAGSQLiteDB("test_embeddings.db")
    
    # Add some embeddings
    db.add(
        ids=["doc1", "doc2", "doc3"],
        embeddings=[
            [0.1, 0.2, 0.3],
            [0.2, 0.3, 0.4],
            [0.9, 0.1, 0.0]
        ],
        documents=["First document", "Second document", "Third document"],
        metadatas=[{"type": "text"}, {"type": "text"}, {"type": "other"}]
    )
    
    print(f"Total embeddings: {db.count()}")
    
    # Query
    results = db.query(
        query_vector=[0.15, 0.25, 0.35],
        n_results=2,
        include=["documents", "distances"]
    )
    
    print("\nQuery results:")
    for i, (id_, doc, dist) in enumerate(zip(
        results['ids'][0],
        results['documents'][0],
        results['distances'][0]
    )):
        print(f"{i+1}. {id_}: {doc} (distance: {dist:.4f})")
    
    # Get specific embeddings
    data = db.get(["doc1", "doc2"], include=["documents", "metadatas"])
    print(f"\nRetrieved: {data}")
    
    db.close()
