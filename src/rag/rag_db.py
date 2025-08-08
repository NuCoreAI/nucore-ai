# ragdb.py
# SQLite + hnswlib for precomputed embeddings
# Simple API for adding, querying, and managing embeddings with metadata.
# Embeddings and rerankings take place via llama.cpp servers. So, this module
# is focused on storage and retrieval, not computation.
# Supports equality, $in, $ne, and $exists filters on metadata keys.

import os, json, sqlite3, tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np
import hnswlib

_SPACE_MAP = {"cosine": "cosine", "l2": "l2", "ip": "ip"}

def _as_f32_2d(x: Sequence[Sequence[float]]) -> np.ndarray:
    a = np.asarray(x, dtype=np.float32)
    if a.ndim != 2: raise ValueError("embeddings must be 2D [n, dim]")
    return a

class RAGSQLiteDB:
    def __init__(self, path: str):
        """
        :param path: Directory to store the SQLite database and HNSW indices.
        """
        self.path = os.path.abspath(path)
        os.makedirs(self.path, exist_ok=True)

    def get_or_create_collection(
        self,
        name: str,
        embedding_function=None,
        metric: str = "cosine",
        M: int = 16,
        ef_construction: int = 200,
        ef_search: int = 128,
    ):
        if embedding_function is not None:
            raise ValueError("Precomputed embeddings only (embedding_function=None).")
        return RAGSQLiteDBCollection(
            root=self.path, name=name, metric=metric,
            M=M, ef_construction=ef_construction, ef_search=ef_search
        )

class RAGSQLiteDBCollection:
    """
    SQLite (metadata + vectors) + hnswlib (ANN).
    Simple Chroma-esque API for precomputed embeddings.
    Filters: equality + $in + $ne + $exists on top-level metadata keys (Python-side).
    """

    def __init__(self, root: str, name: str, metric="cosine", M=16, ef_construction=200, ef_search=128):
        if metric not in _SPACE_MAP: raise ValueError(f"metric must be one of {list(_SPACE_MAP)}")
        self.name, self.metric, self.space = name, metric, _SPACE_MAP[metric]
        self.M, self.ef_construction, self.ef_search = int(M), int(ef_construction), int(ef_search)
        self.dir = os.path.join(root, f"hnsw_{name}"); os.makedirs(self.dir, exist_ok=True)
        self.db_path = os.path.join(self.dir, "store.sqlite3")
        self.index_path = os.path.join(self.dir, "index.bin")
        self.meta_path = os.path.join(self.dir, "meta.json")

        self._con = sqlite3.connect(self.db_path)
        self._con.execute("PRAGMA journal_mode=WAL;")
        self._con.execute("PRAGMA synchronous=NORMAL;")
        self._ensure_schema()

        self._dim: Optional[int] = self._load_dim()
        self._index: Optional[hnswlib.Index] = None
        self._id_to_idx: Dict[str,int] = {}
        self._idx_to_id: List[Optional[str]] = []
        self._ensure_index_loaded()

    # ---------- schema ----------
    def _ensure_schema(self):
        self._con.execute("""CREATE TABLE IF NOT EXISTS items(
            id TEXT PRIMARY KEY,
            idx INTEGER UNIQUE,
            vector BLOB NOT NULL,
            metadata TEXT,
            document TEXT
        );""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS kv(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );""")
        self._con.commit()

    def _load_dim(self)->Optional[int]:
        row = self._con.execute("SELECT value FROM kv WHERE key='dim'").fetchone()
        return int(row[0]) if row else None

    def _save_dim(self, dim:int):
        self._con.execute("INSERT OR REPLACE INTO kv(key,value) VALUES('dim',?)",(str(dim),))
        self._con.commit()
        with open(self.meta_path,"w") as f:
            json.dump({"dim":dim,"metric":self.metric,"M":self.M,
                       "ef_construction":self.ef_construction,"ef_search":self.ef_search}, f, indent=2)

    # ---------- public API ----------
    def add(self, ids: Sequence[Union[str,int]], embeddings, metadatas=None, documents=None):
        ids = [str(i) for i in ids]
        X = _as_f32_2d(embeddings)
        n, dim = X.shape
        if self._dim is None:
            self._save_dim(dim); self._dim = dim
        elif dim != self._dim:
            raise ValueError(f"Embedding dim mismatch: have {self._dim}, got {dim}")

        if metadatas and len(metadatas)!=n: raise ValueError("metadatas length mismatch")
        if documents and len(documents)!=n: raise ValueError("documents length mismatch")

        cur_max = self._con.execute("SELECT MAX(idx) FROM items").fetchone()[0]
        next_idx = 0 if cur_max is None else int(cur_max)+1

        rows = []
        for i in range(n):
            meta = json.dumps(metadatas[i]) if metadatas else None
            doc  = documents[i] if documents else None
            rows.append((ids[i], next_idx+i, X[i].tobytes(), meta, doc))
        self._con.executemany("INSERT OR REPLACE INTO items(id,idx,vector,metadata,document) VALUES(?,?,?,?,?)", rows)
        self._con.commit()
        self._rebuild_index()

    def upsert(self, ids, embeddings, metadatas=None, documents=None):
        self.delete(ids); self.add(ids, embeddings, metadatas, documents)

    def delete(self, ids: Sequence[Union[str,int]]):
        ids = [str(i) for i in ids]
        self._con.executemany("DELETE FROM items WHERE id=?", [(i,) for i in ids])
        self._con.commit()
        self._rebuild_index()

    def count(self)->int:
        return int(self._con.execute("SELECT COUNT(*) FROM items").fetchone()[0])

    def get(self, ids, include: Optional[Sequence[str]]=None)->Dict[str,Any]:
        include = set(include or [])
        if ids:
            ids = [str(i) for i in ids]
        if not ids: return {"ids": [], "embeddings": [], "metadatas": [], "documents": []}
        q = f"SELECT id, idx, vector, metadata, document FROM items WHERE id IN ({','.join('?'*len(ids))})"
        rows = self._con.execute(q, ids).fetchall()
        out = {"ids": [], "embeddings": [], "metadatas": [], "documents": []}
        for _id,_idx,vec_blob,meta_json,doc in rows:
            out["ids"].append(_id)
            if "embeddings" in include:
                out["embeddings"].append(np.frombuffer(vec_blob, dtype=np.float32).tolist())
            if "metadatas" in include:
                out["metadatas"].append(json.loads(meta_json) if meta_json else None)
            if "documents" in include:
                out["documents"].append(doc)
        return out

    def query(self, query_embeddings, n_results=5, where:Optional[Dict[str,Any]]=None, include:Optional[Sequence[str]]=None):
        include = set(include or [])
        Q = _as_f32_2d(query_embeddings)
        if self._dim is None:  # empty collection
            empty = [[] for _ in range(len(Q))]
            res = {"ids": empty}
            if "distances" in include: res["distances"] = empty
            if "metadatas" in include: res["metadatas"] = empty
            if "documents" in include: res["documents"] = empty
            return res

        self._ensure_index_loaded()
        k = min(max(n_results+50, n_results*5), self._index.get_current_count())
        if k <= 0:
            return {"ids":[[] for _ in range(len(Q))]}

        labels, dists = self._index.knn_query(Q, k=k)

        # Python-side metadata filtering (simple, robust)
        allowed = self._filter_idx(where) if where else None

        out_ids, out_d = [], []
        out_meta, out_doc = [], []

        for i in range(Q.shape[0]):
            pairs = list(zip(labels[i], dists[i]))
            if allowed is not None:
                pairs = [(idx, d) for idx, d in pairs if idx in allowed]
            pairs = pairs[:n_results]

            ids = [self._idx_to_id[idx] for idx,_ in pairs if idx < len(self._idx_to_id)]
            out_ids.append(ids)
            if "distances" in include: out_d.append([float(d) for _,d in pairs])

            if ("metadatas" in include) or ("documents" in include):
                if ids:
                    rows = self._con.execute(
                        f"SELECT id, metadata, document FROM items WHERE id IN ({','.join('?'*len(ids))})", ids
                    ).fetchall()
                    m = {r[0]:(r[1], r[2]) for r in rows}
                else:
                    m = {}
                if "metadatas" in include:
                    out_meta.append([json.loads(m[i][0]) if (i in m and m[i][0]) else None for i in ids])
                if "documents" in include:
                    out_doc.append([m[i][1] if i in m else None for i in ids])

        res = {"ids": out_ids}
        if "distances" in include: res["distances"] = out_d
        if "metadatas" in include: res["metadatas"] = out_meta
        if "documents" in include: res["documents"] = out_doc
        return res

    def persist(self):
        self._con.commit()  # index already saved on rebuild

    # ---------- internals ----------
    def _filter_idx(self, where: Dict[str,Any])->Optional[set]:
        if not where: return None
        rows = self._con.execute("SELECT idx, metadata FROM items").fetchall()
        ok = set()
        for idx, meta in rows:
            d = json.loads(meta) if meta else {}
            if _match_where(d, where): ok.add(int(idx))
        return ok

    def _rebuild_index(self):
        rows = self._con.execute("SELECT id, idx, vector FROM items ORDER BY idx").fetchall()
        if not rows:
            self._index = None; self._id_to_idx = {}; self._idx_to_id = []
            try: os.remove(self.index_path)
            except OSError: pass
            return

        dim = self._dim
        index = hnswlib.Index(space=self.space, dim=dim)
        max_idx = max(int(r[1]) for r in rows)
        index.init_index(max_elements=max_idx+1, ef_construction=self.ef_construction, M=self.M)
        index.set_ef(self.ef_search)

        ids, idxs, vecs = [], [], []
        for _id, _idx, vec_blob in rows:
            ids.append(str(_id)); idxs.append(int(_idx))
            vecs.append(np.frombuffer(vec_blob, dtype=np.float32))
        vecs = np.vstack(vecs)

        index.add_items(vecs, np.array(idxs, dtype=np.int64))
        tmp = tempfile.mktemp(prefix="index_", suffix=".bin", dir=self.dir)
        index.save_index(tmp); os.replace(tmp, self.index_path)

        self._index = index
        self._id_to_idx = {ids[i]: idxs[i] for i in range(len(ids))}
        size = max(idxs)+1 if idxs else 0
        self._idx_to_id = [None]*size
        for i, idx in enumerate(idxs):
            if idx >= len(self._idx_to_id):
                self._idx_to_id.extend([None]*(idx-len(self._idx_to_id)+1))
            self._idx_to_id[idx] = ids[i]

    def _ensure_index_loaded(self):
        if self._index is not None or self._dim is None: return
        if not os.path.exists(self.index_path):
            self._rebuild_index(); return
        rows = self._con.execute("SELECT id, idx FROM items").fetchall()
        if not rows: return
        self._id_to_idx = {str(r[0]): int(r[1]) for r in rows}
        size = max(self._id_to_idx.values())+1
        self._idx_to_id = [None]*size
        for _id, idx in self._id_to_idx.items():
            if idx >= len(self._idx_to_id):
                self._idx_to_id.extend([None]*(idx-len(self._idx_to_id)+1))
            self._idx_to_id[idx] = _id
        self._index = hnswlib.Index(space=self.space, dim=self._dim)
        self._index.load_index(self.index_path, max_elements=len(self._idx_to_id))
        self._index.set_ef(self.ef_search)

def _match_where(meta: Dict[str,Any], where: Dict[str,Any]) -> bool:
    # supports: {"k": v}, {"k":{"$in":[...]}},{"k":{"$ne":v}},{"k":{"$exists":True/False}}
    for k, cond in where.items():
        if isinstance(cond, dict):
            if "$in" in cond:
                if meta.get(k) not in cond["$in"]: return False
            elif "$ne" in cond:
                if meta.get(k) == cond["$ne"]: return False
            elif "$exists" in cond:
                exists = k in meta
                if bool(cond["$exists"]) != exists: return False
            else:
                # unknown operator -> fail safe
                return False
        else:
            if meta.get(k) != cond: return False
    return True
