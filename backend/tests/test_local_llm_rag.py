"""Tests for local LLM discovery, quota fallback, and local RAG."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import numpy as np
from eli.llm import _detect_ollama_model, LLM
from eli.agents.memory_agent import OllamaEmbedder, DIM


def test_detect_ollama_model_fallback():
    model = _detect_ollama_model("http://127.0.0.1:99999")
    assert model == "gemma2"


def test_ollama_embedder_dimension_adaptation():
    embedder = OllamaEmbedder(host="http://127.0.0.1:99999", model="nomic-embed-text")
    vec = embedder.embed("test query for local rag")
    assert isinstance(vec, np.ndarray)
    assert len(vec) == DIM
    assert np.isclose(np.linalg.norm(vec), 1.0, atol=1e-4)


@pytest.mark.asyncio
async def test_llm_fallback_local_or_offline():
    llm = LLM()
    resp = await llm._fallback_local_or_offline(
        system=("You are Zephyr, an event coordinator", ""),
        turns=[{"role": "user", "parts": [{"type": "text", "text": "hello"}]}],
        tools=[]
    )
    assert resp is not None
    assert resp.text
    assert any(k in resp.text.lower() for k in ("zephyr", "event", "milestone"))
