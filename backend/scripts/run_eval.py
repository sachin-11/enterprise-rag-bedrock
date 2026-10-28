"""Standalone RAG evaluation script — not part of the API.

Loads eval/test_qa.json, calls a running /chat/query endpoint for each
question, and scores the results with ragas (faithfulness, answer
relevancy, context precision, context recall). Prints a per-question
summary table plus overall averages, and saves full results to
eval/results_{timestamp}.json.

The judge LLM/embeddings for ragas default to OpenAI and stay fixed
(--judge-provider) no matter which LLM_PROVIDER / EMBEDDING_PROVIDER /
RERANK_PROVIDER the server under test runs, so scores from an OpenAI run and
a Bedrock run are judged by the same model and can be compared. Pass --label
to tag a run (e.g. "openai" / "bedrock"); it is saved next to the results.

Prerequisites:
  - The FastAPI server is already running (`uvicorn app.main:app`).
  - `poetry install --with eval` (ragas/datasets/requests live in the
    optional `eval` dependency group, not the main API dependencies).
  - The tenant queried (--tenant-id) has relevant documents already
    ingested, or context_precision/context_recall will score near zero.

Usage:
    poetry run python scripts/run_eval.py
    poetry run python scripts/run_eval.py --base-url http://localhost:8000 --tenant-id eval-tenant

A/B a provider change: start the server with the old settings, run with
--label openai; restart it with LLM_PROVIDER=bedrock (etc.), run with
--label bedrock; compare the two overall-averages tables.
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


def _build_judge_embeddings(provider: str):
    from app.core.config import settings

    if provider == "bedrock":
        from langchain_aws import BedrockEmbeddings

        from app.core.aws_clients import get_bedrock_runtime_client

        return BedrockEmbeddings(
            client=get_bedrock_runtime_client(), model_id=settings.bedrock_embedding_model_id
        )

    from langchain_openai import OpenAIEmbeddings

    from app.services.retrieval_service import EMBEDDING_MODEL_ID

    return OpenAIEmbeddings(api_key=settings.openai_api_key, model=EMBEDDING_MODEL_ID)


def run_ragas_evaluation(rows: list[dict[str, Any]], judge_provider: str = "openai"):
    # Imported lazily: these are heavy, optional deps (see the `eval` poetry
    # group) not needed just to load the test set or parse CLI args.
    from datasets import Dataset
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    # The "fast" tier supports arbitrary temperatures, which ragas' judge
    # calls need to set.
    from app.core.llm import get_chat_llm

    judge_llm = LangchainLLMWrapper(get_chat_llm("fast", temperature=0.0, provider=judge_provider))
    judge_embeddings = LangchainEmbeddingsWrapper(_build_judge_embeddings(judge_provider))

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


def save_results(result_df, output_dir: Path, label: str = "", judge_provider: str = "openai") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"results_{timestamp}.json"
    result_df.to_json(output_path, orient="records", indent=2, force_ascii=False)
    # Sidecar, not part of results_*.json: app/services/eval_service.py only
    # reads files matching that exact name and ignores this one.
    meta_path = output_dir / f"results_{timestamp}.meta.json"
    meta = {"label": label, "judge_provider": judge_provider, "ran_at": timestamp}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL of the running FastAPI server")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID, help="X-Tenant-ID to query under")
    parser.add_argument("--test-set", type=Path, default=DEFAULT_TEST_QA_PATH, help="Path to the test_qa.json file")
    parser.add_argument("--label", default="", help="Tag for this run, e.g. the provider setting under test")
    parser.add_argument(
        "--judge-provider",
        choices=["openai", "bedrock"],
        default="openai",
        help="Provider for the ragas judge LLM/embeddings; keep it the same across runs you compare",
    )
    args = parser.parse_args()

    test_set = load_test_set(args.test_set)
    print(f"Loaded {len(test_set)} test case(s) from {args.test_set}")

    rows = collect_eval_rows(test_set, args.base_url, args.tenant_id)

    print("\nRunning ragas evaluation (judge provider: {args.judge_provider})...")
    result = run_ragas_evaluation(rows, args.judge_provider)
    result_df = result.to_pandas()

    print_summary(result_df)

    output_path = save_results(result_df, RESULTS_DIR, args.label, args.judge_provider)
    print(f"\nSaved full results to {output_path}")


if __name__ == "__main__":
    main()
