"""Free OpenRouter models picked per task from the live list, set aside when they fail."""

import json
import urllib.error

from buddy import free_models
from buddy import openrouter_catalog as oc
from buddy.memory import Memory

CATALOG = {"data": [
    {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "name": "Nemotron 3 Ultra", "context_length": 128000,
     "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "cohere/north-mini-code:free", "name": "North Mini Code 30B", "context_length": 64000,
     "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "nvidia/nemotron-3.5-lightning:free", "name": "Nemotron 3.5 Lightning", "context_length": 1000000,
     "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "google/gemma-4-31b-it:free", "name": "Gemma 4 31B", "context_length": 128000,
     "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "openai/gpt-5.5", "name": "GPT-5.5", "context_length": 400000,
     "pricing": {"prompt": "0.000005", "completion": "0.00002"}},
    {"id": "somebody/image-only:free", "name": "Image", "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"output_modalities": ["image"]}}]}


def test_only_free_text_models_and_the_best_per_task():
    mem = Memory(":memory:")
    got = oc.refresh(mem, fetch=lambda url, headers: CATALOG)
    ids = {m for task in got.values() for m in task}
    assert "openai/gpt-5.5" not in ids and "somebody/image-only:free" not in ids
    assert got["code"][0] == "cohere/north-mini-code:free"
    assert got["long"][0] == "nvidia/nemotron-3.5-lightning:free"
    assert got["reasoning"][0] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert oc.models_for(mem, "code")[0] == "cohere/north-mini-code:free"
    assert not oc.due(mem)


def test_a_failing_model_is_set_aside_and_the_next_is_used():
    mem = Memory(":memory:")
    oc.refresh(mem, fetch=lambda url, headers: CATALOG)
    first, second = oc.models_for(mem, "explain")[:2]
    used = []

    def http(url, body, headers, timeout):
        used.append(body["model"])
        if body["model"] == first:
            raise urllib.error.HTTPError(url, 404, "No endpoints found", {}, None)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    fm = free_models.FreeModels(mem, env={"BUDDY_OPENROUTER_KEY": "k", "BUDDY_FREE_MODELS": "1"}, http=http)
    fm._kind = "explain"
    answer, _ = fm._call("openrouter", "sys", "what is a black hole", 10, 100)
    assert answer == "ok" and used == [first, second] and fm._last_model == second
    assert first not in oc.models_for(mem, "explain")


def test_a_model_set_by_hand_wins():
    mem = Memory(":memory:")
    oc.refresh(mem, fetch=lambda url, headers: CATALOG)
    fm = free_models.FreeModels(mem, env={"BUDDY_OPENROUTER_KEY": "k", "BUDDY_OPENROUTER_MODEL": "my/model:free"})
    assert fm.model_of("openrouter", "explain") == "my/model:free"
    assert free_models.FreeModels(mem, env={"BUDDY_OPENROUTER_KEY": "k"}).model_of("deepseek") == \
        "cohere/north-mini-code:free"
