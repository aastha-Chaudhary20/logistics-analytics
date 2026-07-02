"""
rag_system/indexing/knowledge_store.py

Open Knowledge Format (OKF) store — fixes "no concept of knowledge graphs /
can't add more files to an index".

The old pipeline rebuilt the knowledge graph from ONLY the newly indexed
chunks and wrote it with nx.write_gml(), overwriting everything learned from
earlier uploads. This module makes knowledge CUMULATIVE and stores it in open,
inspectable formats:

  index_store/knowledge/<index_id>/
    triples.jsonl        <- source of truth: one JSON triple per line (append-only, deduped)
    graph.gml            <- merged NetworkX graph (kept for backward compat with GraphRetriever)
    graph.jsonld         <- JSON-LD export (schema.org-ish, for interop with other tools)

Triple record (one per line in triples.jsonl):
  {
    "subject":   "Escorts Kubota Limited",
    "predicate": "negotiated_transport_from",
    "object":    "Polivakkam",
    "subject_type": "Organization",
    "object_type":  "Place",
    "source":    "EVN_3974.pdf",
    "chunk_id":  "abc123",
    "ts":        "2026-07-02T10:00:00Z"
  }

Usage inside IndexingPipeline (replaces the write_gml block):

    from rag_system.indexing.knowledge_store import KnowledgeStore
    ks = KnowledgeStore(base_dir="./index_store/knowledge", index_id=index_id)
    graph_data = self.graph_extractor.extract(all_chunks)   # unchanged
    ks.merge(graph_data, source_docs=[...])                 # ACCUMULATES
    ks.export_gml(graph_path)                               # GraphRetriever keeps working
    ks.export_jsonld()
"""
from typing import Any, Dict, Iterable, List, Optional
import json
import os
from datetime import datetime, timezone

import networkx as nx


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).strip()


def _key(subject: str, predicate: str, obj: str) -> str:
    return f"{subject.lower()}|{predicate.lower()}|{obj.lower()}"


class KnowledgeStore:
    """Cumulative triple store with open-format persistence and merge semantics."""

    def __init__(self, base_dir: str = "./index_store/knowledge", index_id: str = "default"):
        self.dir = os.path.join(base_dir, index_id)
        os.makedirs(self.dir, exist_ok=True)
        self.triples_path = os.path.join(self.dir, "triples.jsonl")
        self.gml_path = os.path.join(self.dir, "graph.gml")
        self.jsonld_path = os.path.join(self.dir, "graph.jsonld")
        self._triples: Dict[str, Dict[str, Any]] = {}
        self._entities: Dict[str, Dict[str, Any]] = {}   # id -> {type, properties}
        self._load()

    # ------------------------------------------------------------------ load
    def _load(self):
        if not os.path.exists(self.triples_path):
            return
        with open(self.triples_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k = _key(t.get("subject", ""), t.get("predicate", ""), t.get("object", ""))
                self._triples[k] = t
                for role, trole in (("subject", "subject_type"), ("object", "object_type")):
                    ent = _norm(t.get(role))
                    if ent:
                        self._entities.setdefault(ent, {"type": t.get(trole) or "Unknown", "properties": {}})
        print(f"📚 KnowledgeStore loaded {len(self._triples)} existing triples, "
              f"{len(self._entities)} entities from {self.triples_path}")

    # ----------------------------------------------------------------- merge
    def merge(self, graph_data: Dict[str, List[Dict[str, Any]]],
              source_docs: Optional[Iterable[str]] = None) -> Dict[str, int]:
        """Merge extractor output ({'entities': [...], 'relationships': [...]})
        into the store. Existing knowledge is NEVER dropped; duplicates are
        deduped on (subject, predicate, object)."""
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        src = ", ".join(source_docs) if source_docs else None
        added_e = added_t = 0

        for ent in graph_data.get("entities", []) or []:
            eid = _norm(ent.get("id") or ent.get("name"))
            if not eid:
                continue
            existing = self._entities.get(eid)
            if existing is None:
                self._entities[eid] = {"type": ent.get("type", "Unknown"),
                                       "properties": ent.get("properties", {}) or {}}
                added_e += 1
            else:
                # enrich, don't overwrite
                if existing.get("type") in (None, "", "Unknown") and ent.get("type"):
                    existing["type"] = ent["type"]
                for k, v in (ent.get("properties") or {}).items():
                    existing["properties"].setdefault(k, v)

        new_lines: List[str] = []
        for rel in graph_data.get("relationships", []) or []:
            s, p, o = _norm(rel.get("source")), _norm(rel.get("label") or rel.get("predicate")), _norm(rel.get("target"))
            if not (s and p and o):
                continue
            k = _key(s, p, o)
            if k in self._triples:
                continue
            triple = {
                "subject": s, "predicate": p, "object": o,
                "subject_type": self._entities.get(s, {}).get("type", "Unknown"),
                "object_type": self._entities.get(o, {}).get("type", "Unknown"),
                "source": rel.get("source_doc") or src,
                "chunk_id": rel.get("chunk_id"),
                "ts": ts,
            }
            self._triples[k] = triple
            new_lines.append(json.dumps(triple, ensure_ascii=False))
            added_t += 1

        if new_lines:  # append-only persistence — old knowledge is untouched
            with open(self.triples_path, "a", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")

        print(f"📚 KnowledgeStore merge: +{added_e} entities, +{added_t} triples "
              f"(total: {len(self._entities)} entities, {len(self._triples)} triples)")
        return {"entities_added": added_e, "triples_added": added_t,
                "entities_total": len(self._entities), "triples_total": len(self._triples)}

    # ---------------------------------------------------------------- export
    def to_networkx(self) -> nx.DiGraph:
        G = nx.DiGraph()
        for eid, meta in self._entities.items():
            G.add_node(eid, type=meta.get("type", "Unknown"),
                       properties=json.dumps(meta.get("properties", {}), ensure_ascii=False))
        for t in self._triples.values():
            G.add_edge(t["subject"], t["object"], label=t["predicate"],
                       source=t.get("source") or "")
        return G

    def export_gml(self, path: Optional[str] = None) -> str:
        """Write the FULL merged graph (old + new) — backward compatible with
        the existing GraphRetriever that reads GML."""
        path = path or self.gml_path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        nx.write_gml(self.to_networkx(), path)
        # keep a copy at the canonical location too
        if path != self.gml_path:
            nx.write_gml(self.to_networkx(), self.gml_path)
        return path

    def export_jsonld(self, path: Optional[str] = None) -> str:
        """JSON-LD export so the knowledge base is portable to other tools."""
        path = path or self.jsonld_path
        nodes = [{"@id": f"okf:{_norm(eid).replace(' ', '_')}",
                  "@type": meta.get("type", "Thing"),
                  "name": eid,
                  **({"properties": meta["properties"]} if meta.get("properties") else {})}
                 for eid, meta in self._entities.items()]
        edges = [{"@type": "Relationship",
                  "subject": {"@id": f"okf:{t['subject'].replace(' ', '_')}"},
                  "predicate": t["predicate"],
                  "object": {"@id": f"okf:{t['object'].replace(' ', '_')}"},
                  **({"source": t["source"]} if t.get("source") else {})}
                 for t in self._triples.values()]
        doc = {"@context": {"okf": "urn:okf:", "name": "http://schema.org/name"},
               "@graph": nodes + edges}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        return path

    # ----------------------------------------------------------------- query
    def neighbors(self, entity: str, max_hops: int = 1) -> List[Dict[str, Any]]:
        """Simple lookup used as a lightweight graph retriever."""
        entity_l = _norm(entity).lower()
        hits = [t for t in self._triples.values()
                if entity_l in t["subject"].lower() or entity_l in t["object"].lower()]
        return hits

    def stats(self) -> Dict[str, int]:
        return {"entities": len(self._entities), "triples": len(self._triples)}
