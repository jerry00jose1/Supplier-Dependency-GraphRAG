from procurement_agent.config import Settings


def test_approver_user_ids_parses_csv() -> None:
    settings = Settings(slack_approver_user_ids="U111, U222,")
    assert settings.approver_user_ids == {"U111", "U222"}


def test_defaults_prefer_local_embeddings() -> None:
    settings = Settings(_env_file=None)
    assert settings.embedding_model == "BAAI/bge-small-en-v1.5"
