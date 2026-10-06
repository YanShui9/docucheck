"""Validated operator configuration, without logging credentials."""
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from pydantic import SecretStr


class MilvusConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class MilvusConfig:
    uri: str
    database: str
    dimension: int
    timeout: float
    token: SecretStr = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "MilvusConfig":
        env = os.environ if environ is None else environ
        try:
            uri = env.get("DOCUCHECK_MILVUS_URI", "http://127.0.0.1:19530").strip()
            database = env.get("DOCUCHECK_MILVUS_DATABASE", "default").strip()
            dimension = int(env.get("DOCUCHECK_MILVUS_DIMENSION", "1024"))
            timeout = float(env.get("DOCUCHECK_MILVUS_TIMEOUT", "10"))
            token = env.get("DOCUCHECK_MILVUS_TOKEN", "").strip()
            url = urlsplit(uri)
            if (
                url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path not in {"", "/"}
                or (url.port is not None and not 1 <= url.port <= 65535)
                or (url.scheme == "http" and url.hostname not in {
                    "localhost", "127.0.0.1", "::1", "milvus",
                })
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", database)
                or not 2 <= dimension <= 32768
                or not math.isfinite(timeout) or not 0 < timeout <= 30
                or len(token) > 4096
            ):
                raise ValueError
            return cls(uri, database, dimension, timeout, SecretStr(token))
        except (ValueError, AttributeError, TypeError):
            raise MilvusConfigurationError("milvus_configuration_error") from None
