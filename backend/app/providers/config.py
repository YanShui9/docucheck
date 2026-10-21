"""Configuration has no default vendor, endpoint, model or credential."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from pydantic import SecretStr

from .base import ProviderError
from .chat_model import ChatModelProvider


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model_id: str
    base_url: str
    context_window: int
    api_key: SecretStr = field(repr=False)
    run_max_input_tokens: int | None = None
    run_max_output_tokens: int | None = None
    context_safety_percent: int = 5

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ProviderConfig":
        env = os.environ if environ is None else environ
        try:
            values = {
                key: env[f"DOCUCHECK_MODEL_{key}"].strip()
                for key in ["PROVIDER", "ID", "BASE_URL", "API_KEY", "CONTEXT_WINDOW"]
            }
            if not all(values.values()) or values["PROVIDER"] != "openai-compatible":
                raise ValueError
            context_window = int(values["CONTEXT_WINDOW"])
            quotas = {}
            for name in ("INPUT", "OUTPUT"):
                key = f"DOCUCHECK_RUN_MAX_{name}_TOKENS"
                raw = env.get(key, "").strip()
                value = int(raw) if raw else None
                if value is not None and not 0 < value <= 2_147_483_647:
                    raise ValueError
                quotas[f"run_max_{name.lower()}_tokens"] = value
            safety_percent = int(env.get("DOCUCHECK_MODEL_CONTEXT_SAFETY_PERCENT", "5").strip())
            if not 0 <= safety_percent < 100:
                raise ValueError
            url = urlsplit(values["BASE_URL"])
            if (
                context_window <= 0
                or url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or (
                    url.scheme == "http"
                    and url.hostname not in {"localhost", "127.0.0.1", "::1"}
                )
            ):
                raise ValueError
            return cls(
                provider=values["PROVIDER"],
                model_id=values["ID"],
                base_url=values["BASE_URL"],
                context_window=context_window,
                api_key=SecretStr(values["API_KEY"]),
                context_safety_percent=safety_percent,
                **quotas,
            )
        except (KeyError, ValueError, AttributeError, TypeError):
            raise ProviderError("configuration_error") from None


def configured_provider(
    *, environ: Mapping[str, str] | None = None
) -> ChatModelProvider:
    config = ProviderConfig.from_env(environ)
    try:
        from langchain_openai import ChatOpenAI

        from .deepseek import DeepSeekChatOpenAI
    except ImportError:
        raise ProviderError("provider_unavailable") from None
    try:
        is_deepseek = urlsplit(config.base_url).hostname == "api.deepseek.com"
        model_class = DeepSeekChatOpenAI if is_deepseek else ChatOpenAI
        options = (
            {"extra_body": {"thinking": {"type": "disabled"}}}
            if is_deepseek
            else {}
        )
        model = model_class(
            model=config.model_id,
            base_url=config.base_url,
            api_key=config.api_key,
            max_retries=0,
            timeout=30,
            stream_usage=True,
            **options,
        )
    except Exception:  # noqa: BLE001 - never expose SDK configuration or credentials
        raise ProviderError("configuration_error") from None
    return ChatModelProvider(model, context_window=config.context_window,
                             run_max_input_tokens=config.run_max_input_tokens,
                             run_max_output_tokens=config.run_max_output_tokens,
                             context_safety_percent=config.context_safety_percent)
