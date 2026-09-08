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

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
