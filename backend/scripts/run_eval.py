"""Standalone RAG evaluation script — not part of the API.

Loads eval/test_qa.json, calls a running /chat/query endpoint for each
question, and scores the results with ragas (faithfulness, answer
relevancy, context precision, context recall). Prints a per-question
summary table plus overall averages, and saves full results to
eval/results_{timestamp}.json.

The judge LLM/embeddings for ragas are OpenAI, matching the rest of this
project's /chat/query pipeline (see app/api/chat.py), instead of ragas'
default model choices.

Prerequisites:
  - The FastAPI server is already running (`uvicorn app.main:app`).
  - `poetry install --with eval` (ragas/datasets/requests live in the
    optional `eval` dependency group, not the main API dependencies).
  - The tenant queried (--tenant-id) has relevant documents already
    ingested, or context_precision/context_recall will score near zero.

Usage:
    poetry run python scripts/run_eval.py
    poetry run python scripts/run_eval.py --base-url http://localhost:8000 --tenant-id eval-tenant
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Makes `app.*` importable whether this is run via `poetry run python scripts/run_eval.py`
# (where `app` is an installed package) or as a plain script (`python scripts/run_eval.py`)
# with no package install step, which is what happens outside Poetry.
sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_TEST_QA_PATH = BACKEND_DIR / "eval" / "test_qa.json"
RESULTS_DIR = BACKEND_DIR / "eval"

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TENANT_ID = "eval-tenant"
REQUEST_TIMEOUT_SECONDS = 60
METRIC_COLUMNS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]


def load_test_set(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def query_chat_endpoint(base_url: str, tenant_id: str, question: str) -> dict[str, Any]:
    response = requests.post(
        f"{base_url}/chat/query",
        json={"query": question, "chat_history": []},
        headers={"X-Tenant-ID": tenant_id},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def collect_eval_rows(test_set: list[dict[str, Any]], base_url: str, tenant_id: str) -> list[dict[str, Any]]:
    rows = []
    for item in test_set:
        question = item["question"]
        print(f"Querying: {question}")
        try:
            response_body = query_chat_endpoint(base_url, tenant_id, question)
        except requests.RequestException as exc:
            print(f"  ! request failed, recording an empty answer: {exc}", file=sys.stderr)
            rows.append(
                {
                    "question": question,
                    "answer": "",
                    "contexts": [""],
                    "ground_truth": item["ground_truth_answer"],
                }
            )
            continue

        contexts = [source["excerpt"] for source in response_body.get("sources", [])]
        rows.append(
            {
                "question": question,
                "answer": response_body.get("answer", ""),
                "contexts": contexts or [""],  # ragas requires a non-empty contexts list per row
                "ground_truth": item["ground_truth_answer"],
            }
        )
    return rows


def run_ragas_evaluation(rows: list[dict[str, Any]]):
    # Imported lazily: these are heavy, optional deps (see the `eval` poetry
    # group) not needed just to load the test set or parse CLI args.
    from datasets import Dataset
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    # gpt-5.5 (app.api.chat.OPENAI_ANSWER_MODEL_ID) only accepts the default
    # temperature — ragas' judge calls need to set their own, so we reuse
    # query_service's model instead, which supports arbitrary temperatures.
    from app.core.config import settings
    from app.services.query_service import OPENAI_MODEL_ID as JUDGE_MODEL_ID
    from app.services.retrieval_service import EMBEDDING_MODEL_ID

    judge_llm = LangchainLLMWrapper(ChatOpenAI(api_key=settings.openai_api_key, model=JUDGE_MODEL_ID))
    judge_embeddings = LangchainEmbeddingsWrapper(
        OpenAIEmbeddings(api_key=settings.openai_api_key, model=EMBEDDING_MODEL_ID)
    )

    dataset = Dataset.from_dict(
        {
            "question": [r["question"] for r in rows],
            "answer": [r["answer"] for r in rows],
            "contexts": [r["contexts"] for r in rows],
            "ground_truth": [r["ground_truth"] for r in rows],
        }
    )

    return evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=judge_llm,
        embeddings=judge_embeddings,
    )


def print_summary(result_df) -> None:
    # This ragas version's to_pandas() names the question column "user_input"
    # (older versions used "question"); rename it back for a readable table.
    display_df = result_df[["user_input", *METRIC_COLUMNS]].rename(columns={"user_input": "question"}).copy()
    for col in METRIC_COLUMNS:
        display_df[col] = display_df[col].round(3)

    print("\n=== Per-question scores ===")
    print(display_df.to_string(index=False))

    print("\n=== Overall averages ===")
    for col in METRIC_COLUMNS:
        print(f"{col:20s}: {result_df[col].mean():.3f}")


def save_results(result_df, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"results_{timestamp}.json"
    result_df.to_json(output_path, orient="records", indent=2, force_ascii=False)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL of the running FastAPI server")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID, help="X-Tenant-ID to query under")
    parser.add_argument("--test-set", type=Path, default=DEFAULT_TEST_QA_PATH, help="Path to the test_qa.json file")
    args = parser.parse_args()

    test_set = load_test_set(args.test_set)
    print(f"Loaded {len(test_set)} test case(s) from {args.test_set}")

    rows = collect_eval_rows(test_set, args.base_url, args.tenant_id)

    print("\nRunning ragas evaluation (this calls Bedrock as the judge LLM)...")
    result = run_ragas_evaluation(rows)
    result_df = result.to_pandas()

    print_summary(result_df)

    output_path = save_results(result_df, RESULTS_DIR)
    print(f"\nSaved full results to {output_path}")


if __name__ == "__main__":
    main()
