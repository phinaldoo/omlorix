import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_compose_helper_loads_env_files_without_executing_shell(tmp_path):
    repo_root = tmp_path / "repo"
    script_dir = repo_root / "script"
    script_dir.mkdir(parents=True)
    compose_script = script_dir / "compose.sh"
    shutil.copy(REPO_ROOT / "script" / "compose.sh", compose_script)
    compose_script.chmod(0o755)

    validate_script = script_dir / "validate-production-env.sh"
    validate_script.write_text("#!/usr/bin/env sh\nexit 0\n")
    validate_script.chmod(0o755)

    env_pwned = tmp_path / "env-pwned"
    repo_root.joinpath(".env").write_text(
        "\n".join(
            [
                "DOCKER_COMPOSE_BIN=/bin/false",
                f"COMPOSE_PROJECT_NAME=$(printf pwned >{env_pwned})",
                f"printf standalone >{tmp_path / 'standalone-pwned'}",
            ]
        )
        + "\n"
    )
    env = os.environ.copy()
    env["DOCKER_COMPOSE_BIN"] = "/bin/echo"
    result = subprocess.run(
        [str(compose_script), "ps"],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip() == "ps"
    assert not env_pwned.exists()
    assert "ignoring DOCKER_COMPOSE_BIN" in result.stderr
    assert "ignoring invalid env line" in result.stderr
