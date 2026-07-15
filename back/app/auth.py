import logging
from typing import Annotated

from fastapi import Depends, Header

from app.config import Settings, get_settings
from app.exceptions import AppError

logger = logging.getLogger("app.auth")

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def verify_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    identifiers = settings.api_key_identifiers
    if x_api_key is None or x_api_key not in identifiers:
        logger.warning("api key auth failed")
        raise AppError(
            code="invalid_api_key",
            message="Invalid or missing API key",
            status_code=401,
        )
    logger.info("api key auth ok: key=%s", identifiers[x_api_key])
    return x_api_key
