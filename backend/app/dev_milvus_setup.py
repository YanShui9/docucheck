"""Explicit, repeatable provisioning for the loopback-only development Milvus."""
import argparse
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key
from pydantic import SecretStr

from .vector_store.config import MilvusConfig, MilvusConfigurationError

ROOT = Path(__file__).resolve().parents[2]
DATABASE = USER = "docucheck_probe"
ROLE = "docucheck_probe_role"
# Explicit RPC privileges instead of an instance administrator role.
PRIVILEGES = (
    "ShowCollections", "CreateCollection", "DropCollection", "DescribeCollection",
    "CreateIndex", "IndexDetail", "Load", "GetLoadState", "GetLoadingProgress",
    "Upsert", "Search", "Query", "Delete",
)


class SetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalSetup:
    config: MilvusConfig
    root_password: SecretStr = field(repr=False)


def prepare(env_path: Path) -> LocalSetup:
    """Save generated secrets before server mutation, so interrupted rotation can retry."""
    if not env_path.is_file() or env_path.is_symlink():
        raise SetupError("milvus_local_env_missing_or_symlink")
    values = dict(dotenv_values(env_path, interpolate=False))
    root_password = values.get("DOCUCHECK_MILVUS_ROOT_PASSWORD") or secrets.token_urlsafe(32)
    token = values.get("DOCUCHECK_MILVUS_TOKEN") or f"{USER}:{secrets.token_urlsafe(32)}"
    additions = {
        "DOCUCHECK_MILVUS_URI": values.get("DOCUCHECK_MILVUS_URI") or "http://127.0.0.1:19530",
        "DOCUCHECK_MILVUS_DATABASE": values.get("DOCUCHECK_MILVUS_DATABASE") or DATABASE,
        "DOCUCHECK_MILVUS_TOKEN": token,
        "DOCUCHECK_MILVUS_ROOT_PASSWORD": root_password,
    }
    try:
        config = MilvusConfig.from_env({**values, **additions})
    except MilvusConfigurationError:
        raise SetupError("milvus_local_setup_configuration") from None
    if (urlsplit(config.uri).hostname not in {"127.0.0.1", "localhost", "::1"}
            or config.database != DATABASE or not token.startswith(f"{USER}:")
            or len(token.split(":", 1)[1]) < 32 or token.count(":") != 1
            or len(root_password) < 32 or ":" in root_password):
        raise SetupError("milvus_local_setup_configuration")
    for key, value in additions.items():
        if values.get(key) != value:
            set_key(env_path, key, value, quote_mode="always")
    return LocalSetup(config, SecretStr(root_password))


def _sdk_factory(**kwargs):
    try:
        from pymilvus import MilvusClient
    except ImportError:
        raise SetupError("milvus_sdk_unavailable") from None
    return MilvusClient(**kwargs)


def _close(client):
    try:
        client.close()
    except Exception:  # noqa: BLE001 -- External errors may include credentials.
        raise SetupError("milvus_setup_close_failed") from None


def provision(setup: LocalSetup, *, bootstrap_default_root: bool = False,
              client_factory: Callable | None = None) -> dict:
    factory = client_factory or _sdk_factory
    common = {"uri": setup.config.uri, "timeout": setup.config.timeout}
    root_token = "root:" + setup.root_password.get_secret_value()

    def root_client(token):
        client = factory(**common, token=token)
        try:
            client.list_users(timeout=setup.config.timeout)
        except Exception:  # noqa: BLE001 -- Close partially authenticated SDK client.
            _close(client)
            raise SetupError("milvus_root_auth_failed") from None
        return client

    try:
        root = root_client(root_token)
    except Exception:  # noqa: BLE001 -- Never expose SDK auth exception details.
        if not bootstrap_default_root:
            raise SetupError("milvus_root_auth_failed") from None
        initial = None
        try:
            initial = root_client("root:Milvus")
            timeout = setup.config.timeout
            if (set(initial.list_users(timeout=timeout)) != {"root"}
                    or set(initial.list_databases(timeout=timeout)) != {"default"}
                    or initial.list_collections(timeout=timeout)):
                raise SetupError("milvus_bootstrap_requires_empty_instance")
            initial.update_password(user_name="root", old_password="Milvus",
                                    new_password=setup.root_password.get_secret_value(),
                                    timeout=timeout)
        except SetupError:
            raise
        except Exception:  # noqa: BLE001 -- Sanitize external bootstrap failures.
            raise SetupError("milvus_root_bootstrap_failed") from None
        finally:
            if initial is not None:
                _close(initial)
        try:
            root = root_client(root_token)
        except Exception:  # noqa: BLE001 -- Saved credentials allow operator recovery.
            raise SetupError("milvus_rotated_root_auth_failed") from None

    timeout = setup.config.timeout
    try:
        if (set(root.list_users(timeout=timeout)) - {"root", USER}
                or set(root.list_databases(timeout=timeout)) - {"default", DATABASE}
                or root.list_collections(timeout=timeout)):
            raise SetupError("milvus_setup_requires_dedicated_instance")
        # Milvus seeds global schema/index/alias reads into the implicit public
        # role. Remove only these known defaults in this dedicated local stack.
        public = root.describe_role(role_name="public", db_name="*", timeout=timeout)
        for grant in public["privileges"]:
            if (grant["db_name"] == "*" and grant["object_name"] == "*"
                    and grant["privilege"] in {"DescribeCollection", "IndexDetail", "ListAliases"}):
                root.revoke_privilege(role_name="public", object_type=grant["object_type"],
                                      object_name="*", db_name="*",
                                      privilege=grant["privilege"], timeout=timeout)
        if DATABASE not in root.list_databases(timeout=timeout):
            root.create_database(db_name=DATABASE, timeout=timeout)
        if USER not in root.list_users(timeout=timeout):
            root.create_user(user_name=USER,
                             password=setup.config.token.get_secret_value().split(":", 1)[1],
                             timeout=timeout)
        if ROLE not in root.list_roles(timeout=timeout):
            root.create_role(role_name=ROLE, timeout=timeout)
        for privilege in PRIVILEGES:
            root.grant_privilege_v2(role_name=ROLE, privilege=privilege,
                                    collection_name="*", db_name=DATABASE, timeout=timeout)
        root.grant_role(user_name=USER, role_name=ROLE, timeout=timeout)
    except SetupError:
        raise
    except Exception:  # noqa: BLE001 -- Stable diagnostics; no secrets from SDK logs.
        raise SetupError("milvus_probe_account_setup_failed") from None
    finally:
        _close(root)
    return {"status": "completed", "database": DATABASE, "user": USER,
            "role": ROLE, "privileges": list(PRIVILEGES), "business_rag_connected": False}


def main(argv: list[str] | None = None, *, env_path: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="本机 Milvus 验收账号；默认仅预览。")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--bootstrap-default-root", action="store_true",
                        help="仅初次空实例：轮换默认管理员凭据")
    args = parser.parse_args(argv)
    if not args.apply:
        print(json.dumps({"mode": "preview", "network_calls": 0,
                          "database": DATABASE, "user": USER,
                          "action": "save local secrets, provision limited probe account"}))
        return 0
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        setup = prepare(env_path or ROOT / ".env")
        result = provision(setup, bootstrap_default_root=args.bootstrap_default_root)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (SetupError, OSError) as error:
        print(json.dumps({"status": "failed",
                          "reason": str(error) if isinstance(error, SetupError)
                          else "milvus_local_env_io_failed",
                          "credentials_location": "local .env; preserve for retry"}))
        return 2
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
