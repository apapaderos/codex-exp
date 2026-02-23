from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str

    # Azure OpenAI
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-05-01-preview"

    # Azure Speech
    azure_speech_key: str
    azure_speech_region: str = "eastus"
    azure_speech_language: str = "en-US"

    # Azure AD
    azure_tenant_id: str
    azure_client_id: str

    # NEXUS behaviour
    nexus_env: str = "development"
    nexus_log_level: str = "INFO"
    nexus_context_token_limit: int = 80_000
    nexus_contribution_timeout_seconds: int = 8

    # CORS
    allowed_origins: list[str] = ["http://localhost:5173"]

    @property
    def jwks_uri(self) -> str:
        return (
            f"https://login.microsoftonline.com/{self.azure_tenant_id}"
            f"/discovery/v2.0/keys"
        )

    @property
    def token_issuer(self) -> str:
        return f"https://sts.windows.net/{self.azure_tenant_id}/"


settings = Settings()
