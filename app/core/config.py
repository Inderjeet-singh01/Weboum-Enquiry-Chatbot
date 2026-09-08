from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    GROQ_API_KEY: str = ""
    LLM_MODEL: str = "openai/gpt-oss-120b"
    APP_ENV: str = "development"
    CORS_ORIGINS: str = "http://localhost:3000"
    BREVO_API_KEY: str = ""
    ENQUIRY_EMAIL_TO: str = ""
    BREVO_SENDER_EMAIL: str = ""
    BREVO_SENDER_NAME: str = "Website Enquiry Bot"

    # RAG / retrieval settings. These are project-relative paths and
    # configurable through environment variables so the same code works
    # locally and on Render without hardcoding machine-specific paths.
    KNOWLEDGE_PATH: str = "company-docs/weboum_knowledge.json"
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RAG_INDEX_PATH: str = "data/embeddings.pkl"
    RAG_INITIAL_TOP_K: int = 10
    RAG_FINAL_TOP_K: int = 5
    RAG_MAX_CONTEXT_CHARS: int = 6000

    # Conversation history settings
    CHAT_HISTORY_CONTEXT_MESSAGES: int = 6
    MAX_SESSION_HISTORY_MESSAGES: int = 20

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.CORS_ORIGINS:
            return []
        cleaned: list[str] = []
        for origin in self.CORS_ORIGINS.split(","):
            item = origin.strip().strip("'\"").rstrip("/")
            if item:
                cleaned.append(item)
        return cleaned

    @property
    def knowledge_path(self) -> Path:
        """Resolve the knowledge JSON path relative to the project root."""
        raw = Path(self.KNOWLEDGE_PATH)
        if raw.is_absolute():
            return raw
        project_root = Path(__file__).resolve().parent.parent.parent
        return (project_root / raw).resolve()

    @property
    def rag_index_path(self) -> Path:
        """Resolve the retrieval index path relative to the project root.

        `RAG_INDEX_PATH` may be an absolute path or a project-relative path;
        relative paths are anchored to the repository root (the parent of the
        `app` package) so they behave consistently on a developer machine and
        on Render.
        """
        raw = Path(self.RAG_INDEX_PATH)
        if raw.is_absolute():
            return raw
        project_root = Path(__file__).resolve().parent.parent.parent
        return (project_root / raw).resolve()


settings = Settings()
