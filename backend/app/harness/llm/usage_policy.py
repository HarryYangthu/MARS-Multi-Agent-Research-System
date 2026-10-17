"""Deployment policy for cumulative token accounting, separate from context capacity."""
from __future__ import annotations

import os
from typing import Literal, cast

import yaml

from app.settings import repo_root

TokenUsageMode = Literal['statistics_only', 'limited']
AgentQuotaMode = Literal['statistics_only', 'limited']
TOKEN_COMPONENTS = frozenset({'input_tokens', 'billed_output_tokens'})


def agent_quota_mode() -> AgentQuotaMode:
    """Host policy overrides historical quota ceilings without rewriting ledgers."""
    mode = os.environ.get('MARS_AGENT_QUOTA_MODE')
    if mode is None:
        path = repo_root() / 'configs/model_usage.yaml'
        raw = yaml.safe_load(path.read_text()) if path.exists() else {}
        mode = raw.get('agent_quota_mode', 'limited') if isinstance(raw, dict) else None
    if mode not in ('statistics_only', 'limited'):
        raise ValueError('agent_quota_mode must be statistics_only or limited')
    return cast(AgentQuotaMode, mode)


def token_usage_mode() -> TokenUsageMode:
    path = repo_root() / 'configs/model_usage.yaml'
    raw = yaml.safe_load(path.read_text()) if path.exists() else {'token_usage_mode': 'statistics_only'}
    mode = raw.get('token_usage_mode') if isinstance(raw, dict) else None
    if mode not in ('statistics_only', 'limited'):
        raise ValueError('token_usage_mode must be statistics_only or limited')
    return cast(TokenUsageMode, mode)
