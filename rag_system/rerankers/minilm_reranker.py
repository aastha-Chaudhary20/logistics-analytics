"""
rag_system/rerankers/minilm_reranker.py

A genuinely CPU-cheap cross-encoder reranker (ms-marco-MiniLM-L-6-v2, ~80MB)
for the RAG lane. Unlike the ColBERT/Qwen rerankers (seconds per query on a
MacBook), MiniLM scores 12 candidates in ~100-300ms on CPU, which makes it
viable to re-enable reranking inside the cpu_fast profile.

Interface-compatible with the pipeline's default strategy call:
    reranked = reranker.rerank(query, documents, top_k=8)
where documents are dicts with a 'text' key; returns the same dicts with a
'rerank_score' added, best first.
"""
from typing import Any, Dict, List
import threading

_load_lock = threading.Lock()


class MiniLMReranker:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
                 max_length: int = 512):
        # sentence-transformers CrossEncoder handles tokenization + scoring.
        from sentence_transformers import CrossEncoder
        with _load_lock:
            self.model = CrossEncoder(model_name, max_length=max_length, device="cpu")
        self.model_name = model_name
        print(f"✅ MiniLM cross-encoder reranker ready ({model_name}, CPU)")

    def rerank(self, query: str, documents: List[Dict[str, Any]],
               top_k: int = 8, **_ignored) -> List[Dict[str, Any]]:
        if not documents:
            return []
        pairs = [(query, (d.get("text") or "")[:2000]) for d in documents]
        scores = self.model.predict(pairs, batch_size=16, show_progress_bar=False)
        ranked = sorted(zip(scores, documents), key=lambda t: -float(t[0]))
        out = []
        for score, doc in ranked[: top_k or len(ranked)]:
            d = dict(doc)
            d["rerank_score"] = float(score)
            out.append(d)
        return out
