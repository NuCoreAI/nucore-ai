"""setup_github_ssh -- the shell is faked throughout (no real ssh/ssh-keygen/ssh-keyscan
ever runs); a scripted fake simulates the few filesystem effects ssh-keygen has, and the
home directory is redirected to tmp_path. The handler's gate, key/config/known_hosts
plumbing and ssh-test classification are what's under test.
"""

from __future__ import annotations

import pytest

from unified.plugin_authoring.handlers import github_ssh
from unified.plugin_authoring.handlers.github_ssh import parse_github_remote, setup_github_ssh

EMAIL = "dev@example.com"
REMOTE = "https://github.com/acme/pool-plugin.git"
FINGERPRINT = github_ssh._GITHUB_ED25519_FINGERPRINT
HOST_LINE = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItestkey dev@example.com"
AUTH_TEXT = "Hi {who}! You've successfully authenticated, but GitHub does not provide shell access."


class FakeShell:
    """Routes each command to a canned result by substring; records every command run."""

    def __init__(self, home, *, ssh_results=None, fingerprint=FINGERPRINT, known_host=False, missing=""):
        self.home = home
        self.commands: list[str] = []
        self.ssh_results = list(ssh_results or [])
        self.fingerprint = fingerprint
        self.known_host = known_host
        self.missing = missing

    async def __call__(self, nucore_interface, args):
        command = args["command"]
        self.commands.append(command)
        if "command -v" in command:
            return {"exit_code": 0, "stdout": self.missing, "stderr": ""}
        if "ssh-keygen -t ed25519" in command:
            key = command.split("-f ", 1)[1].split(" ", 1)[0].strip("'")
            from pathlib import Path

            Path(key).write_text("PRIVATE")
            Path(f"{key}.pub").write_text(PUBLIC_KEY + "\n")
            return {"exit_code": 0, "stdout": "", "stderr": ""}
        if "ssh-keygen -y" in command:
            return {"exit_code": 0, "stdout": PUBLIC_KEY, "stderr": ""}
        if "ssh-keygen -F" in command:
            if self.known_host:
                return {"exit_code": 0, "stdout": "# Host github.com found\n" + HOST_LINE, "stderr": ""}
            return {"exit_code": 1, "stdout": "", "stderr": ""}
        if "ssh-keyscan" in command:
            return {"exit_code": 0, "stdout": HOST_LINE + "\n", "stderr": "# github.com:22 SSH-2.0-x\n"}
        if "ssh-keygen -lf" in command:
            return {"exit_code": 0, "stdout": f"256 {self.fingerprint} github.com (ED25519)\n", "stderr": ""}
        if " -T " in command:
            return self.ssh_results.pop(0)
        raise AssertionError(f"unexpected command: {command}")

    def ran(self, fragment):
        return [c for c in self.commands if fragment in c and "command -v" not in c]


def _ok(who):
    return {"exit_code": 1, "stdout": "", "stderr": AUTH_TEXT.format(who=who)}


DENIED = {"exit_code": 255, "stdout": "", "stderr": "git@github.com: Permission denied (publickey)."}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(github_ssh, "_home_dir", lambda: str(tmp_path))
    return tmp_path


def _install(monkeypatch, fake):
    monkeypatch.setattr(github_ssh.shell, "run_shell_command", fake)


def _logged_in(email=EMAIL):
    return lambda: email


# --- gate ---


@pytest.mark.asyncio
async def test_requires_github_email(home, monkeypatch):
    _install(monkeypatch, FakeShell(home))
    assert "error" in await setup_github_ssh(None, {"remote_url": REMOTE})
    assert "error" in await setup_github_ssh(None, {"github_email": "nope", "remote_url": REMOTE})


@pytest.mark.asyncio
async def test_not_logged_in_refuses_with_instructions_and_runs_nothing(home, monkeypatch):
    fake = FakeShell(home)
    _install(monkeypatch, fake)
    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE}, get_user_id=lambda: None)
    assert "not logged in" in result["error"]
    assert "Log in" in result["instructions"]
    assert fake.commands == []


@pytest.mark.asyncio
async def test_email_mismatch_refuses_with_instructions_and_runs_nothing(home, monkeypatch):
    fake = FakeShell(home)
    _install(monkeypatch, fake)
    result = await setup_github_ssh(
        None, {"github_email": "other@example.com", "remote_url": REMOTE}, get_user_id=_logged_in()
    )
    assert "does not match" in result["error"]
    assert EMAIL in result["instructions"]
    assert fake.commands == []
    assert not (home / ".ssh").exists()


@pytest.mark.asyncio
async def test_email_compare_is_case_insensitive(home, monkeypatch):
    _install(monkeypatch, FakeShell(home, ssh_results=[DENIED]))
    result = await setup_github_ssh(
        None, {"github_email": "DEV@Example.com", "remote_url": REMOTE}, get_user_id=_logged_in()
    )
    assert result["status"] == "needs_key_upload"


@pytest.mark.asyncio
async def test_no_login_concept_skips_comparison(home, monkeypatch):
    _install(monkeypatch, FakeShell(home, ssh_results=[DENIED]))
    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE}, get_user_id=None)
    assert result["status"] == "needs_key_upload"


# --- argument validation ---


@pytest.mark.asyncio
async def test_deploy_requires_parseable_remote_url(home, monkeypatch):
    _install(monkeypatch, FakeShell(home))
    assert "remote_url is required" in (await setup_github_ssh(None, {"github_email": EMAIL}))["error"]
    bad = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": "https://example.com/a/b"})
    assert "not a recognizable" in bad["error"]
    dots = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": "https://github.com/acme/.."})
    assert "not a recognizable" in dots["error"]


@pytest.mark.asyncio
async def test_rejects_unknown_key_scope(home, monkeypatch):
    _install(monkeypatch, FakeShell(home))
    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE, "key_scope": "bogus"})
    assert "key_scope" in result["error"]


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/pool-plugin",
        "https://github.com/acme/pool-plugin.git",
        "git@github.com:acme/pool-plugin.git",
        "git@github.com-acme-pool-plugin:acme/pool-plugin.git",
        "ssh://git@github.com/acme/pool-plugin.git",
    ],
)
def test_parse_github_remote_forms(url):
    assert parse_github_remote(url) == ("acme", "pool-plugin")


# --- deploy mode ---


@pytest.mark.asyncio
async def test_deploy_fresh_generates_key_config_and_known_host_then_needs_upload(home, monkeypatch):
    fake = FakeShell(home, ssh_results=[DENIED])
    _install(monkeypatch, fake)

    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE}, get_user_id=_logged_in())

    assert result["status"] == "needs_key_upload"
    assert result["scope"] == "deploy"
    assert result["public_key"] == PUBLIC_KEY
    assert result["ssh_remote_url"] == "git@github.com-acme-pool-plugin:acme/pool-plugin.git"
    assert result["actions_taken"] == ["generated_key", "wrote_ssh_config", "added_known_host"]
    assert "Allow write access" in result["instructions"]
    assert "github.com/acme/pool-plugin/settings/keys" in result["instructions"]

    key = home / ".ssh" / "eisy_ai_deploy_acme_pool-plugin"
    assert key.exists()
    assert (home / ".ssh").stat().st_mode & 0o777 == 0o700
    config = (home / ".ssh" / "config").read_text()
    assert "Host github.com-acme-pool-plugin" in config
    assert f"IdentityFile {key}" in config
    assert "IdentitiesOnly yes" in config
    assert "HostKeyAlias github.com" in config
    assert (home / ".ssh" / "known_hosts").read_text() == HOST_LINE + "\n"

    keygen = fake.ran("ssh-keygen -t ed25519")[0]
    assert "-N ''" in keygen and EMAIL in keygen
    assert f"HOME={home}" in keygen
    assert fake.ran(" -T ")[0].endswith("git@github.com-acme-pool-plugin")
    assert "BatchMode=yes" in fake.ran(" -T ")[0]


@pytest.mark.asyncio
async def test_deploy_rerun_after_upload_is_configured(home, monkeypatch):
    first = FakeShell(home, ssh_results=[DENIED])
    _install(monkeypatch, first)
    await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})

    second = FakeShell(home, ssh_results=[_ok("acme/pool-plugin")], known_host=True)
    _install(monkeypatch, second)
    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})

    assert result["status"] == "configured"
    assert result["authenticated_as"] == "acme/pool-plugin"
    assert result["ssh_remote_url"] == "git@github.com-acme-pool-plugin:acme/pool-plugin.git"
    assert result["actions_taken"] == []
    # fast path: nothing but the tool check and one ssh test ran
    assert not second.ran("ssh-keygen")
    assert not second.ran("ssh-keyscan")
    assert len(second.ran(" -T ")) == 1


@pytest.mark.asyncio
async def test_deploy_rerun_does_not_duplicate_config_or_regenerate(home, monkeypatch):
    for _ in range(2):
        fake = FakeShell(home, ssh_results=[DENIED, DENIED], known_host=True)
        _install(monkeypatch, fake)
        result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})
        assert result["status"] == "needs_key_upload"

    assert (home / ".ssh" / "config").read_text().count("Host github.com-acme-pool-plugin") == 1
    assert not fake.ran("ssh-keygen -t ed25519")
    assert result["actions_taken"] == []


@pytest.mark.asyncio
async def test_deploy_never_overwrites_existing_private_key(home, monkeypatch):
    key = home / ".ssh" / "eisy_ai_deploy_acme_pool-plugin"
    key.parent.mkdir()
    key.write_text("EXISTING")
    Path_pub = home / ".ssh" / "eisy_ai_deploy_acme_pool-plugin.pub"
    Path_pub.write_text(PUBLIC_KEY + "\n")
    fake = FakeShell(home, ssh_results=[DENIED, DENIED], known_host=True)
    _install(monkeypatch, fake)

    await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})

    assert key.read_text() == "EXISTING"
    assert not fake.ran("ssh-keygen -t ed25519")


# --- account mode ---


@pytest.mark.asyncio
async def test_account_reuses_existing_rsa_key_and_derives_missing_pub(home, monkeypatch):
    rsa = home / ".ssh" / "id_rsa"
    rsa.parent.mkdir()
    rsa.write_text("RSA-PRIVATE")
    fake = FakeShell(home, ssh_results=[DENIED, DENIED], known_host=True)
    _install(monkeypatch, fake)

    result = await setup_github_ssh(None, {"github_email": EMAIL, "key_scope": "account", "remote_url": REMOTE})

    assert result["status"] == "needs_key_upload"
    assert result["scope"] == "account"
    assert rsa.read_text() == "RSA-PRIVATE"
    assert fake.ran("ssh-keygen -y")
    assert not fake.ran("ssh-keygen -t ed25519")
    assert (home / ".ssh" / "id_rsa.pub").read_text().strip() == PUBLIC_KEY
    assert result["ssh_remote_url"] == "git@github.com:acme/pool-plugin.git"
    assert "SSH and GPG keys" in result["instructions"]
    assert not (home / ".ssh" / "config").exists()
    assert fake.ran(" -T ")[0].endswith("git@github.com")


@pytest.mark.asyncio
async def test_account_without_key_generates_id_ed25519_and_remote_is_optional(home, monkeypatch):
    fake = FakeShell(home, ssh_results=[_ok("octocat")])
    _install(monkeypatch, fake)

    result = await setup_github_ssh(None, {"github_email": EMAIL, "key_scope": "account"})

    assert result["status"] == "configured"
    assert result["authenticated_as"] == "octocat"
    assert "ssh_remote_url" not in result
    assert result["actions_taken"] == ["generated_key", "added_known_host"]
    assert (home / ".ssh" / "id_ed25519").exists()


@pytest.mark.asyncio
async def test_account_already_working_changes_nothing(home, monkeypatch):
    key = home / ".ssh" / "id_ed25519"
    key.parent.mkdir()
    key.write_text("PRIVATE")
    fake = FakeShell(home, ssh_results=[_ok("octocat")])
    _install(monkeypatch, fake)

    result = await setup_github_ssh(None, {"github_email": EMAIL, "key_scope": "account", "remote_url": REMOTE})

    assert result["status"] == "configured"
    assert result["actions_taken"] == []
    assert not fake.ran("ssh-keygen")
    assert not fake.ran("ssh-keyscan")
    assert not (home / ".ssh" / "known_hosts").exists()


# --- known_hosts / ssh test classification ---


@pytest.mark.asyncio
async def test_fingerprint_mismatch_refuses_and_writes_nothing(home, monkeypatch):
    fake = FakeShell(home, fingerprint="SHA256:evil")
    _install(monkeypatch, fake)

    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})

    assert "fingerprint" in result["error"]
    assert not (home / ".ssh" / "known_hosts").exists()
    assert not fake.ran(" -T ")


@pytest.mark.asyncio
async def test_existing_known_host_is_not_rescanned(home, monkeypatch):
    fake = FakeShell(home, ssh_results=[DENIED], known_host=True)
    _install(monkeypatch, fake)

    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})

    assert "added_known_host" not in result["actions_taken"]
    assert not fake.ran("ssh-keyscan")


@pytest.mark.asyncio
async def test_known_host_appended_on_new_line_after_existing_content(home, monkeypatch):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "known_hosts").write_text("example.org ssh-ed25519 AAAA")
    _install(monkeypatch, FakeShell(home, ssh_results=[DENIED]))

    await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})

    assert (home / ".ssh" / "known_hosts").read_text() == "example.org ssh-ed25519 AAAA\n" + HOST_LINE + "\n"


@pytest.mark.asyncio
async def test_network_failure_is_an_error_not_a_key_upload_request(home, monkeypatch):
    err = {"exit_code": 255, "stdout": "", "stderr": "ssh: Could not resolve hostname github.com"}
    _install(monkeypatch, FakeShell(home, ssh_results=[err]))

    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})

    assert result["status"] == "error"
    assert "Could not resolve" in result["error"]
    assert "public_key" not in result


@pytest.mark.asyncio
async def test_missing_ssh_binaries_is_an_error(home, monkeypatch):
    fake = FakeShell(home, missing="ssh-keyscan\n")
    _install(monkeypatch, fake)

    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})

    assert "ssh-keyscan" in result["error"]
    assert not (home / ".ssh").exists()


@pytest.mark.asyncio
async def test_shell_plumbing_failure_passes_through(home, monkeypatch):
    async def broken(nucore_interface, args):
        return {"error": "failed to start command: boom"}

    _install(monkeypatch, broken)
    result = await setup_github_ssh(None, {"github_email": EMAIL, "remote_url": REMOTE})
    assert "boom" in result["error"]
