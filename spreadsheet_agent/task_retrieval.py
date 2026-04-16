"""Embedding and nearest-neighbor helpers for SpreadsheetBench tasks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"
VLLM_QUERY_TASK_PROMPT = "Given an spreadsheet task, find similar spreadsheet tasks to it"


def extract_prompt_text(instance) -> str:
    """Extract the text used for retrieval from a benchmark instance."""
    return str(instance.instruction)


def build_vllm_query_text(query: str, task_description: str = VLLM_QUERY_TASK_PROMPT) -> str:
    """Format a query with the instruction template recommended for Qwen embeddings."""
    return f"Instruct: {task_description}\nQuery:{query}"


def build_embedding_cache_key(
    *,
    dataset_path: str,
    split_name: str,
    index_range: dict[str, int | None],
    embedding_model: str,
    prompts_by_id: dict[str, str],
) -> str:
    """Build a deterministic cache key from retrieval inputs."""
    prompt_hashes = {
        str(task_id): hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        for task_id, prompt in sorted(prompts_by_id.items())
    }
    payload = {
        "dataset_path": dataset_path,
        "split_name": split_name,
        "index_range": index_range,
        "embedding_model": embedding_model,
        "prompt_hashes": prompt_hashes,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_embedding_cache(cache_dir: str | Path, cache_key: str) -> dict | None:
    """Load cached embeddings for a key if present."""
    path = Path(cache_dir) / f"{cache_key}.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_embedding_cache(cache_dir: str | Path, cache_key: str, payload: dict) -> Path:
    """Persist cached embeddings for later reuse."""
    path = Path(cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{cache_key}.json"
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return target


def save_embedding_artifact(
    *,
    output_path: str | Path,
    train_dataset_path: str,
    test_dataset_path: str,
    embedding_model: str,
    train_range: dict[str, int | None],
    test_range: dict[str, int | None],
    train_payload: dict,
    test_payload: dict,
) -> Path:
    """Persist train/test embeddings for later runtime retrieval."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "train_dataset_path": train_dataset_path,
        "test_dataset_path": test_dataset_path,
        "embedding_model": embedding_model,
        "train_range": train_range,
        "test_range": test_range,
        "train_ids": list(train_payload["ids"]),
        "train_embeddings": train_payload["embeddings"],
        "test_ids": list(test_payload["ids"]),
        "test_embeddings": test_payload["embeddings"],
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def select_nearest_train_ids(
    *,
    test_ids: Sequence[str],
    test_embeddings: Sequence[Sequence[float]],
    train_ids: Sequence[str],
    train_embeddings: Sequence[Sequence[float]],
) -> dict[str, str]:
    """Map each test id to its nearest train id using cosine similarity."""
    if not train_ids:
        raise ValueError("train_ids must not be empty")
    if len(train_ids) != len(train_embeddings):
        raise ValueError("train_ids and train_embeddings must have matching lengths")
    if len(test_ids) != len(test_embeddings):
        raise ValueError("test_ids and test_embeddings must have matching lengths")

    train_matrix = _normalize_embeddings(np.asarray(train_embeddings, dtype=float))
    test_matrix = _normalize_embeddings(np.asarray(test_embeddings, dtype=float))
    similarity = test_matrix @ train_matrix.T
    best_indices = np.argmax(similarity, axis=1)
    return {test_id: train_ids[index] for test_id, index in zip(test_ids, best_indices, strict=True)}


def select_top_k_similarities(
    *,
    test_ids: Sequence[str],
    test_embeddings: Sequence[Sequence[float]],
    train_ids: Sequence[str],
    train_embeddings: Sequence[Sequence[float]],
    top_k: int,
) -> dict[str, list[dict[str, float | str]]]:
    """Return ranked cosine-similarity matches for each test task."""
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if not train_ids:
        raise ValueError("train_ids must not be empty")
    if len(train_ids) != len(train_embeddings):
        raise ValueError("train_ids and train_embeddings must have matching lengths")
    if len(test_ids) != len(test_embeddings):
        raise ValueError("test_ids and test_embeddings must have matching lengths")

    train_matrix = _normalize_embeddings(np.asarray(train_embeddings, dtype=float))
    test_matrix = _normalize_embeddings(np.asarray(test_embeddings, dtype=float))
    similarity = test_matrix @ train_matrix.T
    limit = min(top_k, len(train_ids))
    ranked_indices = np.argsort(-similarity, axis=1, kind="stable")[:, :limit]

    results: dict[str, list[dict[str, float | str]]] = {}
    for row_index, test_id in enumerate(test_ids):
        results[test_id] = [
            {
                "train_id": str(train_ids[column_index]),
                "similarity": float(similarity[row_index, column_index]),
            }
            for column_index in ranked_indices[row_index]
        ]
    return results


def load_top_k_similarities(
    *,
    artifact: dict,
    train_instances: Sequence,
    test_instances: Sequence,
    memory_records: dict[str, dict],
    top_k: int,
) -> dict[str, list[dict[str, float | str]]]:
    """Validate the embedding artifact against selected instances and resolve top-k matches."""
    artifact_train_ids = [str(task_id) for task_id in artifact["train_ids"]]
    artifact_test_ids = [str(task_id) for task_id in artifact["test_ids"]]
    expected_train_ids = [str(instance.id) for instance in train_instances]
    expected_test_ids = [str(instance.id) for instance in test_instances]

    if artifact_train_ids != expected_train_ids:
        raise ValueError("embedding artifact train_ids do not match selected train instances")
    test_embedding_by_id = {
        str(task_id): embedding
        for task_id, embedding in zip(artifact_test_ids, artifact["test_embeddings"], strict=True)
    }
    missing_test_ids = [task_id for task_id in expected_test_ids if task_id not in test_embedding_by_id]
    if missing_test_ids:
        raise ValueError(
            "embedding artifact is missing test embeddings for selected test ids: "
            + ", ".join(missing_test_ids)
        )
    selected_test_embeddings = [test_embedding_by_id[task_id] for task_id in expected_test_ids]

    retrievals = select_top_k_similarities(
        test_ids=expected_test_ids,
        test_embeddings=selected_test_embeddings,
        train_ids=artifact_train_ids,
        train_embeddings=artifact["train_embeddings"],
        top_k=len(artifact_train_ids),
    )
    filtered_retrievals: dict[str, list[dict[str, float | str]]] = {}
    insufficient_test_ids = []
    for test_id, matches in retrievals.items():
        available_matches = []
        for item in matches:
            if item["train_id"] in memory_records:
                available_matches.append(item)
                if len(available_matches) == top_k:
                    break
        if not available_matches:
            insufficient_test_ids.append(test_id)
        filtered_retrievals[test_id] = available_matches

    if insufficient_test_ids:
        raise ValueError(
            "no retrieved train memories are available for selected test ids: "
            + ", ".join(insufficient_test_ids)
        )
    return filtered_retrievals


def embed_or_load_cached(
    *,
    cache_dir: str | Path | None,
    dataset_path: str,
    split_name: str,
    index_range: dict[str, int | None],
    prompts_by_id: dict[str, str],
    embedder,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> dict:
    """Return cached embeddings or compute and persist them."""
    cache_key = build_embedding_cache_key(
        dataset_path=dataset_path,
        split_name=split_name,
        index_range=index_range,
        embedding_model=embedding_model,
        prompts_by_id=prompts_by_id,
    )
    if cache_dir is not None:
        cached = load_embedding_cache(cache_dir, cache_key)
        if cached is not None:
            return cached

    ordered_ids = list(prompts_by_id)
    embeddings = embedder.embed_texts([prompts_by_id[task_id] for task_id in ordered_ids])
    payload = {
        "ids": ordered_ids,
        "embeddings": embeddings,
        "embedding_model": embedding_model,
        "cache_key": cache_key,
    }
    if cache_dir is not None:
        save_embedding_cache(cache_dir, cache_key, payload)
    return payload


class QwenEmbeddingAdapter:
    """Thin embedding adapter isolated for easier stubbing in tests."""

    def __init__(
        self,
        model: str = DEFAULT_EMBEDDING_MODEL,
        base_url: str | None = None,
        api_key: str | None = None,
        model_path: str | None = None,
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.model_path = model_path

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        if self.model_path is not None:
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as exc:
                raise ImportError(
                    "Local embedding model loading requires a working sentence_transformers installation"
                ) from exc

            model = SentenceTransformer(self.model_path, trust_remote_code=True)
            embeddings = model.encode(list(texts), normalize_embeddings=False)
            return [list(item) for item in embeddings]

        from openai import OpenAI

        client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        response = client.embeddings.create(model=self.model, input=list(texts))
        return [list(item.embedding) for item in response.data]


class VLLMEmbeddingAdapter:
    """Embedding adapter backed by in-process vLLM embedding inference."""

    def __init__(self, model: str, tensor_parallel_size: int = 1):
        self.model = model
        self.tensor_parallel_size = tensor_parallel_size

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        try:
            from vllm import LLM
        except Exception as exc:
            raise ImportError("vLLM embedding mode requires a working vllm installation") from exc

        model = LLM(model=self.model, runner="pooling", tensor_parallel_size=self.tensor_parallel_size)
        outputs = model.encode(list(texts), pooling_task="embed")
        return [_coerce_embedding_vector(item.outputs.data) for item in outputs]


def _normalize_embeddings(matrix: np.ndarray) -> np.ndarray:
    if matrix.ndim != 2:
        raise ValueError("embeddings must be a 2D array")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _coerce_embedding_vector(vector) -> list[float]:
    """Convert model-specific embedding outputs into plain Python floats."""
    if hasattr(vector, "detach"):
        vector = vector.detach()
    if hasattr(vector, "cpu"):
        vector = vector.cpu()
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]
