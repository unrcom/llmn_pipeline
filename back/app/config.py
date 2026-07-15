from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5440
    db_name: str = "llmn_pipeline"
    db_user: str = "llmn_pipeline"
    db_password: str = "llmn_pipeline"
    db_schema: str = "rag"

    api_keys: str = ""  # カンマ区切り。interfaces.md §7.8.5

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def api_key_identifiers(self) -> dict[str, str]:
        """有効キー -> ログ用識別部(secret を除いた部分)。

        既知の(config に列挙された)キーに対してのみ事前計算する。
        リクエストで受信した任意文字列をここで加工することはない。
        secret 部は "_" を含まない形式(token_hex 等)で生成する運用を前提とする。
        """
        keys = (key.strip() for key in self.api_keys.split(","))
        return {key: key.rsplit("_", 1)[0] for key in keys if key}


@lru_cache
def get_settings() -> Settings:
    return Settings()
