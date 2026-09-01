from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_source_frontend_is_http_only() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    server_compose = (REPO_ROOT / "docker-compose.server.yml").read_text(
        encoding="utf-8"
    )
    port_overlay = (REPO_ROOT / "docker-compose.frontend-port.yml").read_text(
        encoding="utf-8"
    )
    generate_config = (REPO_ROOT / "nginx/bin/generate-config.sh").read_text(
        encoding="utf-8"
    )

    assert "FRONTEND_USE_HTTPS" not in env_example
    assert "FRONTEND_HTTPS_HOST_PORT" not in env_example
    assert "FRONTEND_SSL_" not in env_example
    assert "FRONTEND_HTTPS_HOST_PORT" not in server_compose
    assert "FRONTEND_HTTPS_HOST_PORT" not in port_overlay
    assert "/etc/nginx/certs" not in server_compose
    assert (
        '"${FRONTEND_HTTP_HOST_BIND:-127.0.0.1}:'
        '${FRONTEND_HTTP_HOST_PORT:-8080}:8080"'
    ) in port_overlay
    assert "default.https.conf.template" not in generate_config
    assert not (REPO_ROOT / "nginx/default.https.conf.template").exists()
