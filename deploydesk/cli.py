"""CLI: python -m deploydesk.cli ingest | ask "question" """
import argparse
import json
import time

from .config import load_settings
from .generator import answer_with_fallback, make_generator
from .ingest import ingest_dir
from .retriever import Index


def main():
    p = argparse.ArgumentParser(prog="deploydesk")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--mode", default=None)
    args = p.parse_args()
    s = load_settings()
    if args.cmd == "ingest":
        t0 = time.perf_counter()
        chunks, n = ingest_dir(s.docs_dir, s.chunk_size, s.chunk_overlap)
        idx = Index(s.embedding_model)
        idx.build(chunks)
        idx.save(s.index_dir)
        print(json.dumps({"documents": n, "chunks": len(chunks), "seconds": round(time.perf_counter() - t0, 1)}))
    else:
        idx = Index.load(s.index_dir, s.embedding_model)
        hits = idx.search(args.question, s.top_k, args.mode or s.retrieval_mode)
        print(json.dumps(answer_with_fallback(make_generator(s), args.question, [c for c, _ in hits]), indent=2))


if __name__ == "__main__":
    main()
