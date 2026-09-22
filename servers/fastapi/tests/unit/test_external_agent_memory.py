import asyncio
import uuid

import pytest

from services import mem0_oss_memory as memory
from services.chat.chat_memory_store import ChatMemoryStore
from services.mem0_presentation_memory_service import Mem0PresentationMemoryService


@pytest.mark.parametrize("setting", [None, "true", "false"])
@pytest.mark.parametrize("cached", [False, True])
def test_external_mode_blocks_memory_initialization_and_cached_clients(
    monkeypatch, setting, cached
):
    # Also cover stores constructed before the external-mode guard takes effect.
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "false")
    monkeypatch.setenv("MEM0_ENABLED", "true")
    chat = ChatMemoryStore()
    presentation = Mem0PresentationMemoryService()
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    if setting is None:
        monkeypatch.delenv("MEM0_ENABLED")
    else:
        monkeypatch.setenv("MEM0_ENABLED", setting)
    monkeypatch.setattr(memory, "_shared_client", object() if cached else None)
    monkeypatch.setattr(memory, "_init_attempted", False)

    def forbidden(*args, **kwargs):
        pytest.fail("External mode must not initialize memory or download models")

    for name in ("_spacy_model_available", "_oss_config_from_env", "memory_from_config"):
        monkeypatch.setattr(memory, name, forbidden)
    assert memory.get_shared_mem0_client() is None
    assert memory._init_attempted is False

    async def run():
        document_id, conversation_id = uuid.uuid4(), uuid.uuid4()
        for store in (presentation, Mem0PresentationMemoryService()):
            await store.store_generation_context(
                document_id, "system", "user", "source", "content", "instructions"
            )
            assert await store.retrieve_context(document_id, "query") == ""
        for store in (chat, ChatMemoryStore()):
            scope = dict(presentation_id=document_id, conversation_id=conversation_id)
            await store.store_chat_turn(
                **scope, user_message="question", assistant_message="answer"
            )
            assert await store.retrieve_context(**scope, query="query") == ""
            assert await store.load_history(**scope) == []

    asyncio.run(run())


def test_external_build_skips_embedding_warmup(monkeypatch, tmp_path):
    from scripts import warm_fastembed_cache as warmup

    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("MEM0_ENABLED", "true")
    for name in ("HF_HOME", "PRESENTON_FASTEMBED_ICON_CACHE_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))

    def forbidden(*args, **kwargs):
        pytest.fail("External builds must not warm embedding models")

    monkeypatch.setattr(warmup.ICON_FINDER_SERVICE, "ensure_initialized", forbidden)
    monkeypatch.setattr(warmup, "_warm_mem0_default_fastembed", forbidden)
    warmup.main()
    assert (tmp_path / "HF_HOME").is_dir()
    assert (tmp_path / "PRESENTON_FASTEMBED_ICON_CACHE_DIR").is_dir()
    assert not list(tmp_path.rglob("*.onnx"))
