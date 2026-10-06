"""Local provisioning safety: synthetic credentials, no real SDK or service."""
import pytest
from dotenv import dotenv_values

from backend.app.dev_milvus_setup import SetupError, main, prepare, provision


def local_env(tmp_path, extra=""):
    path = tmp_path / ".env"
    path.write_text("UNRELATED_VALUE=keep-me\n" + extra, encoding="utf-8")
    return path


def test_preview_does_not_create_credentials_or_client(tmp_path, capsys):
    path = tmp_path / ".env"
    assert main([], env_path=path) == 0
    assert not path.exists()
    assert '"network_calls": 0' in capsys.readouterr().out


def test_prepare_preserves_existing_values_and_secrets(tmp_path):
    path = local_env(tmp_path)
    first = prepare(path)
    second = prepare(path)
    assert first == second
    values = dotenv_values(path, interpolate=False)
    assert values["UNRELATED_VALUE"] == "keep-me"
    assert first.config.database == "docucheck_probe"
    assert first.config.token.get_secret_value().startswith("docucheck_probe:")
    assert first.root_password.get_secret_value() not in repr(first)


@pytest.mark.parametrize("extra", [
    "DOCUCHECK_MILVUS_URI=https://example.com:19530\n",
    "DOCUCHECK_MILVUS_DATABASE=business\n",
    "DOCUCHECK_MILVUS_TOKEN=business:existing-secret\n",
    "DOCUCHECK_MILVUS_ROOT_PASSWORD=Milvus\n",
])
def test_unsupported_config_fails_before_any_write(tmp_path, extra):
    path = local_env(tmp_path, extra)
    before = path.read_bytes()
    with pytest.raises(SetupError, match="milvus_local_setup_configuration"):
        prepare(path)
    assert path.read_bytes() == before


class FakeAdmin:
    def __init__(self):
        self.users = ["root"]
        self.databases = ["default"]
        self.roles = []
        self.grants = []
        self.public_grants = [
            {"object_type": "Global", "object_name": "*", "db_name": "*",
             "privilege": "DescribeCollection"},
            {"object_type": "Collection", "object_name": "custom", "db_name": "custom",
             "privilege": "Search"},
        ]
        self.revoked = []
        self.closed = False

    def list_users(self, **kwargs):
        return self.users

    def list_databases(self, **kwargs):
        return self.databases

    def list_collections(self, **kwargs):
        return []

    def create_database(self, db_name, **kwargs):
        self.databases.append(db_name)

    def create_user(self, user_name, password, **kwargs):
        self.users.append(user_name)

    def list_roles(self, **kwargs):
        return self.roles

    def create_role(self, role_name, **kwargs):
        self.roles.append(role_name)

    def grant_privilege_v2(self, **kwargs):
        self.grants.append(kwargs)

    def grant_role(self, **kwargs):
        self.user_role = kwargs

    def describe_role(self, **kwargs):
        return {"privileges": list(self.public_grants)}

    def revoke_privilege(self, **kwargs):
        self.revoked.append(kwargs)
        self.public_grants = [g for g in self.public_grants
                              if g["privilege"] != kwargs["privilege"]]

    def update_password(self, **kwargs):
        self.rotation = kwargs

    def close(self):
        self.closed = True


def test_grants_are_limited_to_probe_database_and_reusable(tmp_path):
    config = prepare(local_env(tmp_path))
    client = FakeAdmin()
    result = provision(config, client_factory=lambda **kwargs: client)
    assert result["database"] == "docucheck_probe" and client.closed
    assert client.grants and all(g["db_name"] == "docucheck_probe" for g in client.grants)
    assert not any(g["privilege"] in {"ClusterAdmin", "CreateOwnership", "CreateDatabase"}
                   for g in client.grants)
    provision(config, client_factory=lambda **kwargs: client)
    assert client.users.count("docucheck_probe") == 1
    assert client.databases.count("docucheck_probe") == 1


def test_default_root_is_never_attempted_without_explicit_bootstrap(tmp_path):
    config = prepare(local_env(tmp_path))
    tokens = []

    def failing(**kwargs):
        tokens.append(kwargs["token"])
        raise RuntimeError("private-secret")

    with pytest.raises(SetupError, match="milvus_root_auth_failed"):
        provision(config, client_factory=failing)
    assert len(tokens) == 1 and tokens[0] != "root:Milvus"


def test_bootstrap_refuses_nonempty_existing_instance(tmp_path):
    config = prepare(local_env(tmp_path))
    client = FakeAdmin()
    client.users.append("existing_business_user")

    def factory(**kwargs):
        if kwargs["token"] == "root:Milvus":
            return client
        raise RuntimeError("auth denied")

    with pytest.raises(SetupError, match="milvus_bootstrap_requires_empty_instance"):
        provision(config, bootstrap_default_root=True, client_factory=factory)
    assert not hasattr(client, "rotation") and client.closed


def test_bootstrap_rotates_using_saved_password_then_reconnects(tmp_path):
    config = prepare(local_env(tmp_path))
    initial, rotated = FakeAdmin(), FakeAdmin()
    seen = []

    def factory(**kwargs):
        seen.append(kwargs["token"])
        if len(seen) == 1:
            raise RuntimeError("auth denied")
        return initial if kwargs["token"] == "root:Milvus" else rotated

    result = provision(config, bootstrap_default_root=True, client_factory=factory)
    assert initial.rotation["new_password"] == config.root_password.get_secret_value()
    assert initial.closed and rotated.closed
    assert result["status"] == "completed"


def test_default_public_metadata_grants_removed_without_touching_custom_grants(tmp_path):
    client = FakeAdmin()
    provision(prepare(local_env(tmp_path)), client_factory=lambda **kwargs: client)
    assert [g["privilege"] for g in client.revoked] == ["DescribeCollection"]
    assert client.revoked[0]["role_name"] == "public"
    assert [g["privilege"] for g in client.public_grants] == ["Search"]


def test_provision_refuses_instance_with_other_users_before_grants(tmp_path):
    client = FakeAdmin()
    client.users.append("business_user")
    with pytest.raises(SetupError, match="milvus_setup_requires_dedicated_instance"):
        provision(prepare(local_env(tmp_path)), client_factory=lambda **kwargs: client)
    assert not client.grants and not client.revoked and client.closed
