"""Global conversation model settings. Secrets never appear in a response."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.bridge.global_model_profiles import GlobalModelProfiles

router = APIRouter(prefix='/model-profiles', tags=['config'])


class SaveModelProfile(BaseModel):
    id: str = ''
    name: str = Field(min_length=1, max_length=80)
    provider: str = 'custom'
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''))
    context_window: int = Field(default=128000, ge=4096, le=2000000)
    compatibility: Literal['generic', 'qwen_200k', 'gemma_160k'] = 'generic'


@router.get('')
async def list_profiles() -> dict[str, Any]:
    return GlobalModelProfiles().public()


@router.post('')
async def save_profile(payload: SaveModelProfile) -> dict[str, Any]:
    try:
        return GlobalModelProfiles().save(payload.model_dump(exclude={'api_key'}), api_key=payload.api_key.get_secret_value())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
