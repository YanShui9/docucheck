"""Offline deployment guards for recovery of the existing local Milvus volume."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def deployment():
    return yaml.safe_load((ROOT / "compose.milvus.yaml").read_text(encoding="utf-8"))


def test_external_etcd_readiness_is_required_without_publishing_metadata_ports():
    services = deployment()["services"]
    milvus, etcd = services["milvus"], services["etcd"]
    assert milvus["environment"]["ETCD_USE_EMBED"] == "false"
    assert milvus["environment"]["ETCD_ENDPOINTS"] == "etcd:2379"
    assert milvus["environment"]["COMMON_SECURITY_ENABLEPUBLICPRIVILEGE"] == "false"
    assert milvus["depends_on"]["etcd"]["condition"] == "service_healthy"
    assert "endpoint" in etcd["healthcheck"]["test"] and "health" in etcd["healthcheck"]["test"]
    assert not etcd.get("ports")
    assert re.fullmatch(r"quay.io/coreos/etcd:v3\.5\.23@sha256:[a-f0-9]{64}", etcd["image"])


def test_existing_metadata_subdirectory_and_milvus_storage_are_reused():
    config = deployment()
    etcd = config["services"]["etcd"]
    mount = etcd["volumes"][0]
    assert mount["type"] == "volume" and mount["source"] == "milvus_data"
    assert mount["target"] == "/etcd" and mount["volume"]["subpath"] == "etcd"
    assert "--data-dir=/etcd" in etcd["command"]
    assert "milvus_data:/var/lib/milvus" in config["services"]["milvus"]["volumes"]
    assert set(config["volumes"]) == {"milvus_data"}
    # Milvus authorization stays enabled when switching only its metadata client.
    security = yaml.safe_load((ROOT / "deploy/milvus/user.yaml").read_text(encoding="utf-8"))
    assert security["common"]["security"]["authorizationEnabled"] is True
    assert security["common"]["security"]["enablePublicPrivilege"] is False


def test_new_volume_gets_only_an_idempotent_metadata_directory_before_subpath_mount():
    services = deployment()["services"]
    init = services["storage-init"]
    assert init["network_mode"] == "none" and init["restart"] == "no"
    assert init["command"] == ["mkdir -p /var/lib/milvus/etcd"]
    assert init["volumes"] == ["milvus_data:/var/lib/milvus"]
    assert services["etcd"]["depends_on"]["storage-init"]["condition"] == "service_completed_successfully"


def test_local_smoke_bounds_both_services_and_does_not_loop_restarts():
    services = yaml.safe_load((ROOT / "compose.milvus-smoke.yaml").read_text(encoding="utf-8"))["services"]
    for name, memory in (("milvus", "2g"), ("etcd", "256m")):
        assert services[name]["restart"] == "no"
        assert services[name]["mem_limit"] == memory
        assert services[name]["memswap_limit"] == memory
