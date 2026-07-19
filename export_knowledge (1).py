#!/usr/bin/env python3
"""
export_knowledge.py — export your procurement data as open knowledge.

    python export_knowledge.py            # all three formats
    python export_knowledge.py jsonld     # just one

Outputs to reports/knowledge.{jsonld,nt,csv}. The JSON-LD and N-Triples files
are standards-compliant linked data — load them into any RDF/graph tool
(rdflib, GraphDB, Apache Jena) or a JSON-LD consumer.
"""
import sys
sys.path.insert(0, ".")
from rag_system.analytics.okf_export import export_okf

fmts = [sys.argv[1]] if len(sys.argv) > 1 else ["jsonld", "ntriples", "csv"]
for f in fmts:
    res = export_okf(f)
    if res.get("error"):
        print(f"{f}: {res['error']}")
    else:
        print(f"✅ {res['rows']} rows / {res['events']} events -> {res['path']}")
