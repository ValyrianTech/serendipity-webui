"""Tests for the Docker build/push helper script."""

import subprocess

import pytest

import build_docker


@pytest.fixture
def recorded_calls(monkeypatch):
    """Monkeypatch subprocess.check_call to record invocations without executing docker."""
    calls = []

    def fake_check_call(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(build_docker.subprocess, "check_call", fake_check_call)
    return calls


def test_docker_command_uses_list_without_shell(monkeypatch):
    recorded = []

    def fake_check_call(command, **kwargs):
        recorded.append((command, kwargs))

    monkeypatch.setattr(build_docker.subprocess, "check_call", fake_check_call)

    build_docker.docker_command(["docker", "push", "foo:1"])

    assert len(recorded) == 1
    command, kwargs = recorded[0]
    assert isinstance(command, list)
    assert command == ["docker", "push", "foo:1"]
    assert kwargs.get("shell") is not True


def test_build_invokes_build_then_push(recorded_calls, monkeypatch):
    monkeypatch.setattr(build_docker, "username", "valyriantech")

    container = build_docker.build("myimage", "1.0")

    assert container == "valyriantech/myimage:1.0"
    assert len(recorded_calls) == 2

    build_command, build_kwargs = recorded_calls[0]
    push_command, push_kwargs = recorded_calls[1]

    assert build_command == ["docker", "build", "--progress=plain", "-t", "valyriantech/myimage:1.0", build_docker.project_dir]
    assert push_command == ["docker", "push", "valyriantech/myimage:1.0"]
    assert build_kwargs.get("shell") is not True
    assert push_kwargs.get("shell") is not True


def test_build_with_from_docker_includes_build_arg(recorded_calls, monkeypatch):
    monkeypatch.setattr(build_docker, "username", "valyriantech")

    build_docker.build("myimage", "1.0", from_docker="base:1")

    build_command, _ = recorded_calls[0]
    assert "--build-arg" in build_command
    assert "DOCKER_FROM=base:1" in build_command
    assert build_command.index("DOCKER_FROM=base:1") == build_command.index("--build-arg") + 1


def test_tag_uses_list_args(recorded_calls):
    build_docker.tag("a:1", "b:1")

    assert len(recorded_calls) == 2
    assert recorded_calls[0][0] == ["docker", "tag", "a:1", "b:1"]
    assert recorded_calls[1][0] == ["docker", "push", "b:1"]
    for _, kwargs in recorded_calls:
        assert kwargs.get("shell") is not True


def test_build_does_not_split_malicious_tag(recorded_calls, monkeypatch):
    monkeypatch.setattr(build_docker, "username", "valyriantech")

    build_docker.build("img", "x; rm -rf /")

    build_command, build_kwargs = recorded_calls[0]
    assert build_kwargs.get("shell") is not True

    idx = build_command.index("-t")
    assert build_command[idx + 1] == "valyriantech/img:x; rm -rf /"


def test_main_non_latest_path(monkeypatch):
    build_calls = []
    tag_calls = []

    monkeypatch.setattr(build_docker, "build", lambda repo, tag, from_docker=None: build_calls.append((repo, tag)) or "u/r:1")
    monkeypatch.setattr(build_docker, "tag", lambda src, dst: tag_calls.append((src, dst)))

    build_docker.main(["myimage", "--tag", "1.0"])

    assert build_calls == [("myimage", "1.0")]
    assert tag_calls == []


def test_main_latest_path(monkeypatch):
    build_calls = []
    tag_calls = []

    monkeypatch.setattr(build_docker, "build", lambda repo, tag, from_docker=None: build_calls.append((repo, tag)) or "u/r:1")
    monkeypatch.setattr(build_docker, "tag", lambda src, dst: tag_calls.append((src, dst)))

    build_docker.main(["myimage", "--tag", "1.0", "--latest"])

    assert build_calls == [("myimage", "1.0")]
    assert tag_calls == [("u/r:1", "valyriantech/myimage:latest")]


def test_build_propagates_called_process_error(monkeypatch):
    def raise_error(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(build_docker.subprocess, "check_call", raise_error)

    with pytest.raises(subprocess.CalledProcessError):
        build_docker.build("myimage", "1.0")


def test_tag_propagates_called_process_error(monkeypatch):
    def raise_error(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(build_docker.subprocess, "check_call", raise_error)

    with pytest.raises(subprocess.CalledProcessError):
        build_docker.tag("a:1", "b:1")
