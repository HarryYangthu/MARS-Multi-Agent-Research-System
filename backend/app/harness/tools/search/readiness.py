"""Check host literature access before spending a research model call."""
from __future__ import annotations

from app.settings import Settings, get_settings


class LiteratureAccessError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.reason = {"code": code}


def require_literature_access(settings: Settings | None = None) -> None:
    selected = settings if settings is not None else get_settings()
    if not selected.mars_enable_network_tools:
        raise LiteratureAccessError(
            "本次研究需要外部论文，但联网工具被关闭。请开启 MARS_ENABLE_NETWORK_TOOLS 并重启后端后重试；尚未调用研究模型。",
            "literature_network_disabled",
        )
    if not any(domain.strip() for domain in selected.mars_web_search_allowlist.split(",")):
        raise LiteratureAccessError(
            "本次研究需要读取论文正文，但论文来源域名列表为空。请配置 MARS_WEB_SEARCH_ALLOWLIST，或移除空值以使用默认论文来源；尚未调用研究模型。",
            "literature_source_domains_empty",
        )
