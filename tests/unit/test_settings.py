from procurement_agent.config import Settings


def test_defaults_prefer_local_embeddings() -> None:
    settings = Settings(_env_file=None)
    assert settings.embedding_model == "BAAI/bge-small-en-v1.5"
