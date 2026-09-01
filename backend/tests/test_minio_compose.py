from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_bundled_minio_uses_mc_for_readiness():
    """Keep the bundled MinIO startup path compatible with its tiny image."""
    source = (REPO_ROOT / "docker-compose.server.yml").read_text(encoding="utf-8")
    minio_service = source.split("  minio:\n", 1)[1].split("  minio_init:\n", 1)[0]
    init_service = source.split("  minio_init:\n", 1)[1].split("  frontend:\n", 1)[0]

    # The pinned MinIO image is UBI Micro and has neither curl nor wget. The
    # minio_init container includes mc and performs the actual API readiness
    # check in its retry loop before creating the bucket.
    assert "healthcheck:" not in minio_service
    assert "condition: service_started" in init_service
    assert "until mc alias set local http://minio:9000" in init_service
