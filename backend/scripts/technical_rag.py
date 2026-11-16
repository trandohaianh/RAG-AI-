"""Operator CLI; does not read the app .env, app DB or active RAG collections."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.technical.layout import approve, extract_layout
from rag.technical.models import LayoutBundle, Scope


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, model):
    Path(path).write_text(model.model_dump_json(indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("extract")
    extract.add_argument("raw")
    extract.add_argument("--scope", required=True)
    extract.add_argument("--source-id", required=True)
    extract.add_argument("--out", required=True)
    approve_cmd = commands.add_parser("approve")
    approve_cmd.add_argument("layout")
    approve_cmd.add_argument("--raw", required=True)
    approve_cmd.add_argument("--reviewer", required=True)
    approve_cmd.add_argument("--out", required=True)
    approve_cmd.add_argument("--confirm-reviewed", action="store_true", required=True)
    for name in ("build", "ask"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--root", required=True)
        cmd.add_argument("--embedding-model", required=True)
        cmd.add_argument("--embedding-id", required=True, help="Include exact model/weights revision")
        cmd.add_argument("--ollama-url", default="http://127.0.0.1:11434")
        if name == "build":
            cmd.add_argument("layouts", nargs="+")
        else:
            cmd.add_argument("question")
            cmd.add_argument("--scope", required=True)
            cmd.add_argument("--policy", required=True)
            cmd.add_argument("--source-id")
            cmd.add_argument("--reranker", choices=["local", "cohere"], default="local")
            cmd.add_argument("--reranker-path")
            cmd.add_argument("--reranker-id", required=True)
            cmd.add_argument("--cohere-model", default="rerank-v5.0-pro")
            cmd.add_argument("--llm", required=True, help="Explicit Gemini model ID available to your account")
            cmd.add_argument("--allow-external-calls", action="store_true")
    args = parser.parse_args()
    if args.command == "extract":
        bundle = extract_layout(Path(args.raw), Scope.model_validate(read_json(args.scope)), args.source_id)
        write(args.out, bundle)
    elif args.command == "approve":
        bundle = LayoutBundle.model_validate(read_json(args.layout))
        write(args.out, approve(bundle, Path(args.raw), args.reviewer))
    else:
        from rag.technical.adapters import OllamaEmbeddings
        from rag.technical.store import build_index
        embeddings = OllamaEmbeddings(args.embedding_model, args.ollama_url)
        if args.command == "build":
            bundles = [LayoutBundle.model_validate(read_json(p)) for p in args.layouts]
            print(build_index(Path(args.root), bundles, embeddings, args.embedding_id))
        else:
            if not args.allow_external_calls:
                parser.error("Gemini (and optional Cohere) requires --allow-external-calls")
            from langchain_google_genai import ChatGoogleGenerativeAI
            from rag.technical.adapters import CohereReranker, LangChainSelector, LocalBGEReranker
            from rag.technical.pipeline import Policy, TechnicalRAG
            if args.reranker == "local":
                if not args.reranker_path:
                    parser.error("--reranker-path must point to reviewed local safetensors weights")
                reranker = LocalBGEReranker(args.reranker_path, args.reranker_id)
            else:
                reranker = CohereReranker(os.environ["COHERE_API_KEY"], args.cohere_model,
                                          identity=args.reranker_id)
            llm = ChatGoogleGenerativeAI(model=args.llm, temperature=0, timeout=60, max_retries=0)
            service = TechnicalRAG(Path(args.root), embeddings, args.embedding_id, reranker,
                                   LangChainSelector(llm), Policy.model_validate(read_json(args.policy)))
            result = service.answer(args.question, Scope.model_validate(read_json(args.scope)), args.source_id)
            print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
