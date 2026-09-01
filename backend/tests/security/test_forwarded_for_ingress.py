from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_TEMPLATES = (
    REPO_ROOT / "nginx" / "default.http.conf.template" / "default.conf",
)
RENDERER = REPO_ROOT / "nginx" / "bin" / "render-forwarded-for.sh"


def test_public_nginx_templates_require_explicit_forwarded_for_rendering():
    """Every bundled proxy location must use the reviewed ingress placeholder."""
    for template in NGINX_TEMPLATES:
        source = template.read_text(encoding="utf-8")
        proxy_location_count = source.count("proxy_pass ")

        assert "proxy_set_header X-Forwarded-For __X_FORWARDED_FOR_VALUE__;" in source
        assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" not in source
        assert source.count(
            "proxy_set_header X-Forwarded-For __X_FORWARDED_FOR_VALUE__;"
        ) == proxy_location_count
        assert source.count('proxy_set_header Forwarded "";') == proxy_location_count
        assert source.count(
            "proxy_set_header X-Forwarded-Host $host;"
        ) == proxy_location_count
        assert "$binary_remote_addr" not in source
        assert "limit_req_zone $omlorix_client_ip" in source
        assert "limit_conn_zone $omlorix_client_ip" in source
        assert "$omlorix_request_scheme" in source
        assert "$omlorix_external_upstream_trusted" in source
        assert "X-Omlorix-Launcher-Secret \"\"" in source
        assert source.count(
            "proxy_set_header X-Omlorix-Proxy-Verification-Nonce "
            "$http_x_omlorix_verification_nonce;"
        ) == proxy_location_count


def test_forwarded_for_renderer_defaults_to_overwriting_untrusted_headers(tmp_path):
    """The shipped default must ignore a visitor-supplied forwarding chain."""
    template = tmp_path / "default.conf.template"
    output = tmp_path / "default.conf"
    template.write_text(
        "proxy_set_header X-Forwarded-For __X_FORWARDED_FOR_VALUE__;\n",
        encoding="utf-8",
    )

    subprocess.run(
        [str(RENDERER), str(template), str(output)],
        check=True,
        cwd=REPO_ROOT,
        env={**os.environ, "FRONTEND_TRUST_PROXY_HEADERS": "false"},
    )

    assert output.read_text(encoding="utf-8") == (
        "proxy_set_header X-Forwarded-For $omlorix_client_ip;\n"
    )


def test_forwarded_for_renderer_preserves_headers_only_for_explicit_trusted_ingress(
    tmp_path,
):
    """A secured upstream-proxy deployment may deliberately retain its chain."""
    template = tmp_path / "default.conf.template"
    output = tmp_path / "default.conf"
    template.write_text(
        "proxy_set_header X-Forwarded-For __X_FORWARDED_FOR_VALUE__;\n",
        encoding="utf-8",
    )

    subprocess.run(
        [str(RENDERER), str(template), str(output)],
        check=True,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "FRONTEND_TRUST_PROXY_HEADERS": "true",
            "OMLORIX_LAUNCHER_PROXY_SECRET": "a" * 64,
        },
    )

    assert output.read_text(encoding="utf-8") == (
        "proxy_set_header X-Forwarded-For $omlorix_client_ip;\n"
    )


def test_forwarded_for_renderer_fails_closed_without_launcher_secret(tmp_path):
    template = tmp_path / "default.conf.template"
    output = tmp_path / "default.conf"
    template.write_text("__LAUNCHER_PROXY_SECRET__\n", encoding="utf-8")

    result = subprocess.run(
        [str(RENDERER), str(template), str(output)],
        check=False,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "FRONTEND_TRUST_PROXY_HEADERS": "true",
            "OMLORIX_LAUNCHER_PROXY_SECRET": "",
        },
    )

    assert result.returncode != 0
    assert not output.exists()


def test_prebuilt_frontend_renders_the_security_mode_from_compose_at_startup():
    """Release images must not bake the unsafe forwarding choice into nginx."""
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    assert "20-render-omlorix-nginx.sh" in dockerfile
    assert "omlorix-default.conf.template" in dockerfile
    for compose_name in ("docker-compose.server.yml", "docker-compose.managed-cloud.yml"):
        compose = (REPO_ROOT / compose_name).read_text(encoding="utf-8")
        assert (
            "FRONTEND_TRUST_PROXY_HEADERS: "
            "${FRONTEND_TRUST_PROXY_HEADERS:-false}"
        ) in compose
        assert "OMLORIX_LAUNCHER_PROXY_SECRET" in compose
        assert "FRONTEND_TRUSTED_UPSTREAMS" in compose


def test_forwarded_for_renderer_builds_a_fail_closed_external_proxy_allowlist(
    tmp_path,
):
    template = tmp_path / "default.conf.template"
    output = tmp_path / "default.conf"
    template.write_text(
        "geo $trusted { default 0; __TRUSTED_EXTERNAL_UPSTREAMS__ }\n",
        encoding="utf-8",
    )

    subprocess.run(
        [str(RENDERER), str(template), str(output)],
        check=True,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "FRONTEND_TRUST_PROXY_HEADERS": "true",
            "OMLORIX_LAUNCHER_PROXY_SECRET": "b" * 64,
            "FRONTEND_TRUSTED_UPSTREAMS": "192.0.2.10/32,2001:db8::/64",
        },
    )

    rendered = output.read_text(encoding="utf-8")
    assert "192.0.2.10/32 1;" in rendered
    assert "2001:db8::/64 1;" in rendered


def test_forwarded_for_renderer_ignores_a_stale_allowlist_when_trust_is_off(
    tmp_path,
):
    template = tmp_path / "default.conf.template"
    output = tmp_path / "default.conf"
    template.write_text("__TRUSTED_EXTERNAL_UPSTREAMS__\n", encoding="utf-8")

    subprocess.run(
        [str(RENDERER), str(template), str(output)],
        check=True,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "FRONTEND_TRUST_PROXY_HEADERS": "false",
            "FRONTEND_TRUSTED_UPSTREAMS": "192.0.2.10/32",
        },
    )

    assert "192.0.2.10" not in output.read_text(encoding="utf-8")


def test_forwarded_for_renderer_rejects_external_allowlist_injection(tmp_path):
    template = tmp_path / "default.conf.template"
    output = tmp_path / "default.conf"
    template.write_text("__TRUSTED_EXTERNAL_UPSTREAMS__\n", encoding="utf-8")

    result = subprocess.run(
        [str(RENDERER), str(template), str(output)],
        check=False,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "FRONTEND_TRUST_PROXY_HEADERS": "true",
            "OMLORIX_LAUNCHER_PROXY_SECRET": "c" * 64,
            "FRONTEND_TRUSTED_UPSTREAMS": "192.0.2.10; include /tmp/evil",
        },
    )

    assert result.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize("trusted_upstream", ["0.0.0.0/0", "::/0"])
def test_forwarded_for_renderer_rejects_trust_all_networks(
    tmp_path, trusted_upstream
):
    """A public nginx listener must never treat every remote peer as a proxy."""
    template = tmp_path / "default.conf.template"
    output = tmp_path / "default.conf"
    template.write_text("__TRUSTED_EXTERNAL_UPSTREAMS__\n", encoding="utf-8")

    result = subprocess.run(
        [str(RENDERER), str(template), str(output)],
        check=False,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "FRONTEND_TRUST_PROXY_HEADERS": "true",
            "OMLORIX_LAUNCHER_PROXY_SECRET": "c" * 64,
            "FRONTEND_TRUSTED_UPSTREAMS": trusted_upstream,
        },
    )

    assert result.returncode != 0
    assert not output.exists()
