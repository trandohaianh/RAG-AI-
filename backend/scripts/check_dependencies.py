"""Check declared packages, the installed dependency graph, and RAG import compatibility.

Run with the same interpreter used to start the backend. This command does not
load application configuration, create databases, or call an LLM/embedding API.
"""
from __future__ import annotations

import argparse
from importlib import import_module, metadata
from pathlib import Path
import subprocess
import sys

from packaging.requirements import InvalidRequirement, Requirement


# Include the symbols consumed by the backend, including the adapter's error
# class: a successful resolver alone cannot detect a moved Python API.
IMPORT_CONTRACT = (
    ("fastapi", ("FastAPI",)),
    ("pydantic", ("BaseModel",)),
    ("sqlalchemy", ("create_engine",)),
    ("jwt", ("encode", "decode", "PyJWTError")),
    ("bcrypt", ("hashpw", "checkpw")),
    ("stripe", ("Webhook",)),
    ("google.genai", ("Client", "types")),
    ("langchain_core.documents", ("Document",)),
    ("langchain_community.document_loaders", ("Docx2txtLoader", "PyPDFLoader", "TextLoader")),
    ("langchain_text_splitters", ("RecursiveCharacterTextSplitter",)),
    ("langchain_google_genai", ("GoogleGenerativeAIEmbeddings",)),
    ("langchain_google_genai._common", ("GoogleGenerativeAIError",)),
    ("langchain_chroma", ("Chroma",)),
    ("chromadb", ("PersistentClient",)),
    ("rank_bm25", ("BM25L",)),
    ("psycopg2", ("connect",)),
    ("redis", ("Redis",)),
    ("httpx", ("Client", "AsyncClient")),
)


def check_requirements(path: Path) -> tuple[int, list[str]]:
    errors: list[str] = []
    checked = 0
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            errors.append(f"{path.name}:{number}: unsupported requirement syntax")
            continue
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        checked += 1
        try:
            version = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            errors.append(f"{requirement.name}: not installed")
            continue
        if requirement.specifier and not requirement.specifier.contains(version):
            errors.append(f"{requirement.name}: installed {version}, expected {requirement.specifier}")
    return checked, errors


def check_imports() -> list[str]:
    errors: list[str] = []
    for module_name, symbols in IMPORT_CONTRACT:
        try:
            module = import_module(module_name)
            for symbol in symbols:
                getattr(module, symbol)
        except Exception as exc:
            errors.append(f"{module_name}: {type(exc).__name__}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, default=Path(__file__).resolve().parents[1] / "requirements.txt")
    args = parser.parse_args()
    checked, errors = check_requirements(args.requirements)
    graph = subprocess.run([sys.executable, "-B", "-m", "pip", "check"], capture_output=True, text=True)
    if graph.returncode:
        errors.append(graph.stdout.strip() or graph.stderr.strip() or "pip check failed")
    errors.extend(check_imports())
    if errors:
        print("Dependency validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"Dependency validation passed: {checked} declared packages, pip check, {len(IMPORT_CONTRACT)} import contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
