"""``setup_github_ssh`` -- configures the host's SSH access to GitHub so a
plain ``git push`` (``setup_github_repo``) works non-interactively, doing
every step that can be automated and leaving only the one that can't: pasting
the public key into GitHub's web UI.

Default scope is a per-repo **deploy key**: a dedicated ed25519 key plus a
``~/.ssh/config`` Host alias, so the key can only ever touch the one repo it
was added to. GitHub refuses the same public key on a second repo and plain
``git@github.com`` can't choose between keys, hence the alias -- the returned
``ssh_remote_url`` uses it. ``key_scope="account"`` instead reuses (or
creates) the host's ordinary default key, which the developer adds to their
GitHub *account* and which then reaches all of their repos.

Gate, before any shell command: the developer must be signed in
(*get_user_id*, same two-level semantics as ``configure_developer``) and the
``github_email`` they give must equal the signed-in user's email. SSH only
ever reveals a GitHub username, never an email, so this is an attestation by
the developer, not proof; a failed gate returns ``instructions`` text.

Idempotent: a first ``ssh -T`` that already authenticates changes nothing;
otherwise each step (key, config block, known_hosts) is skipped if already
done, and a private key is never overwritten.

Details that differ from the hand-run steps this automates:

* ``ssh -T git@github.com`` exits **1 on success** ("Hi X! You've successfully
  authenticated..."), so success is parsed from the message, never the exit code.
* github.com's host key is added to ``known_hosts`` only after its ed25519
  fingerprint matches GitHub's published one (``ssh-keyscan`` alone is
  trust-on-first-use), and only if it isn't there already.
* Every ssh call is ``BatchMode`` so a missing key/host fails fast instead of
  waiting on a prompt the non-interactive shell can never answer.
* ``shell.run_shell_command``'s child env has no ``HOME``, so commands get an
  explicit ``HOME=`` resolved from the passwd entry -- the same user that later
  runs ``git push``.
"""

from __future__ import annotations

import os
import pwd
import re
import shlex
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

from ...handlers import shell

# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
_GITHUB_ED25519_FINGERPRINT = "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"

_SSH_OPTS = "-o BatchMode=yes -o ConnectTimeout=10"
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_REMOTE_RE = re.compile(
    r"^(?:https://(?:www\.)?github\.com/|(?:ssh://)?git@github\.com(?:-[A-Za-z0-9._-]+)?[:/])"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)
_AUTH_OK_RE = re.compile(r"Hi (?P<who>[^!\s]+)! You've successfully authenticated")
_DEFAULT_KEY_NAMES = ("id_ed25519", "id_ecdsa", "id_rsa")
_SCOPES = ("deploy", "account")


class _Abort(Exception):
    def __init__(self, result: dict[str, Any]):
        self.result = result


def _home_dir() -> str:
    return pwd.getpwuid(os.getuid()).pw_dir


def _valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and bool(name.strip("."))


def parse_github_remote(remote_url: str) -> tuple[str, str] | None:
    match = _REMOTE_RE.match(remote_url.strip())
    if not match:
        return None
    owner, repo = match.group("owner"), match.group("repo")
    if not (_valid_name(owner) and _valid_name(repo)):
        return None
    return owner, repo


def _gate(email: str, get_user_id: Callable[[], str | None] | None) -> dict[str, Any] | None:
    if get_user_id is None:
        return None  # no login concept applies (e.g. MCP/CLI) -- nothing to compare against
    authenticated = get_user_id()
    if not authenticated:
        return {
            "error": "not logged in -- the person must log in to eisyUI before GitHub SSH access can be set up",
            "instructions": (
                "You need to be signed in first.\n"
                "1. Log in to eisyUI with your UD account.\n"
                "2. Ask me to set up GitHub access again."
            ),
        }
    if authenticated.strip().lower() != email.lower():
        return {
            "error": f"GitHub account email '{email}' does not match the signed-in user '{authenticated}'",
            "instructions": (
                f"The GitHub account must use the same email you're signed in with ({authenticated}).\n"
                f"1. On GitHub, go to Settings > Emails, add {authenticated}, verify it, and make it your primary "
                "email (or use a GitHub account that already has it).\n"
                f"2. Or sign in here with the account whose email is '{email}'.\n"
                "3. Then ask me to set up GitHub access again."
            ),
        }
    return None


async def _sh(nucore_interface: NuCoreInterface, command: str) -> dict[str, Any]:
    result = await shell.run_shell_command(nucore_interface, {"command": command})
    if "error" in result:
        raise _Abort({"error": result["error"]})
    return result


def _out(result: dict[str, Any]) -> str:
    return f"{result.get('stdout', '')}\n{result.get('stderr', '')}".strip()


async def _ssh_test(nucore_interface: NuCoreInterface, prefix: str, host: str) -> tuple[str, str]:
    """('ok', who) | ('denied', output) | ('error', output) -- parsed from the message, since success exits 1."""
    result = await _sh(nucore_interface, f"{prefix}ssh {_SSH_OPTS} -T git@{shlex.quote(host)}")
    text = _out(result)
    match = _AUTH_OK_RE.search(text)
    if match:
        return "ok", match.group("who")
    if "Permission denied (publickey)" in text:
        return "denied", text
    return "error", text


async def _ensure_known_host(nucore_interface: NuCoreInterface, prefix: str, ssh_dir: Path) -> bool:
    """True if a github.com entry was added; False if already present."""
    found = await _sh(nucore_interface, f"{prefix}ssh-keygen -F github.com")
    if found.get("exit_code") == 0 and (found.get("stdout") or "").strip():
        return False

    scan = await _sh(nucore_interface, f"{prefix}ssh-keyscan -t ed25519 github.com")
    line = next(
        (ln.strip() for ln in (scan.get("stdout") or "").splitlines() if ln.startswith("github.com ssh-ed25519 ")),
        None,
    )
    if not line:
        raise _Abort({"error": f"could not fetch github.com's host key: {_out(scan) or 'no output'}"})

    check = await _sh(nucore_interface, f"printf '%s\\n' {shlex.quote(line)} | {prefix}ssh-keygen -lf -")
    if _GITHUB_ED25519_FINGERPRINT not in (check.get("stdout") or ""):
        raise _Abort(
            {
                "error": "github.com's host key fingerprint does not match GitHub's published ed25519 fingerprint "
                f"({_GITHUB_ED25519_FINGERPRINT}) -- refusing to trust it; nothing was written to known_hosts",
                "scanned": _out(check),
            }
        )

    known_hosts = ssh_dir / "known_hosts"
    existing = known_hosts.read_text(encoding="utf-8") if known_hosts.exists() else ""
    with known_hosts.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(line + "\n")
    known_hosts.chmod(0o600)
    return True


async def _ensure_key(
    nucore_interface: NuCoreInterface, prefix: str, key_path: Path, email: str
) -> tuple[Path, bool]:
    """(public key path, generated?). Never overwrites an existing private key."""
    generated = False
    pub_path = Path(f"{key_path}.pub")
    if not key_path.exists():
        result = await _sh(
            nucore_interface,
            f"{prefix}ssh-keygen -t ed25519 -C {shlex.quote(email)} -f {shlex.quote(str(key_path))} -N '' -q",
        )
        if result.get("exit_code") != 0 or not key_path.exists():
            raise _Abort({"error": f"ssh-keygen failed: {_out(result)}"})
        generated = True
    if not pub_path.exists():
        result = await _sh(nucore_interface, f"{prefix}ssh-keygen -y -f {shlex.quote(str(key_path))}")
        public = (result.get("stdout") or "").strip()
        if result.get("exit_code") != 0 or not public:
            raise _Abort(
                {"error": f"could not derive the public key for {key_path} (passphrase-protected?): {_out(result)}"}
            )
        pub_path.write_text(public + "\n", encoding="utf-8")
    return pub_path, generated


def _ensure_config_block(ssh_dir: Path, owner: str, repo: str, alias: str, key_path: Path) -> bool:
    begin = f"# >>> eisy-ai deploy key {owner}/{repo} >>>"
    config = ssh_dir / "config"
    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    if begin in existing:
        return False
    block = (
        f"{begin}\n"
        f"Host {alias}\n"
        "    HostName github.com\n"
        "    User git\n"
        f"    IdentityFile {key_path}\n"
        "    IdentitiesOnly yes\n"
        "    HostKeyAlias github.com\n"
        f"# <<< eisy-ai deploy key {owner}/{repo} <<<\n"
    )
    with config.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        if existing:
            handle.write("\n")
        handle.write(block)
    config.chmod(0o600)
    return True


def _upload_instructions(scope: str, owner: str | None, repo: str | None, title: str, public_key: str) -> str:
    if scope == "deploy":
        steps = (
            f"1. Open https://github.com/{owner}/{repo}/settings/keys (the repository must already exist).\n"
            "2. Click \"Add deploy key\".\n"
            f"3. Title: {title}\n"
            f"4. Key: paste this line:\n{public_key}\n"
            "5. Tick \"Allow write access\" (otherwise git push is rejected), then click \"Add key\"."
        )
    else:
        steps = (
            "1. On GitHub.com, click your profile photo > Settings > SSH and GPG keys > New SSH key.\n"
            f"2. Title: {title}; leave Key type as \"Authentication Key\".\n"
            f"3. Key: paste this line:\n{public_key}\n"
            "4. Click \"Add SSH key\" (GitHub may ask for your password or 2FA code)."
        )
    return steps + "\nThen ask me to run the GitHub setup again to verify it."


async def setup_github_ssh(
    nucore_interface: NuCoreInterface,
    args: dict[str, Any],
    *,
    get_user_id: Callable[[], str | None] | None = None,
) -> Any:
    email = (args.get("github_email") or "").strip()
    if not email or "@" not in email:
        return {"error": "github_email is required -- the email address of the person's GitHub account"}

    gate_failure = _gate(email, get_user_id)
    if gate_failure is not None:
        return gate_failure

    scope = (args.get("key_scope") or "deploy").strip().lower()
    if scope not in _SCOPES:
        return {"error": f"key_scope must be one of {list(_SCOPES)}"}

    remote_url = (args.get("remote_url") or "").strip()
    parsed = parse_github_remote(remote_url) if remote_url else None
    if remote_url and parsed is None:
        return {"error": f"'{remote_url}' is not a recognizable GitHub repository URL (owner/repo)"}
    if scope == "deploy" and parsed is None:
        return {
            "error": "remote_url is required for a deploy key -- the developer must create the empty repository "
            "first and pass its URL (https or ssh form)"
        }
    owner, repo = parsed if parsed else (None, None)

    home = Path(_home_dir())
    ssh_dir = home / ".ssh"
    prefix = f"HOME={shlex.quote(str(home))} "
    title = (args.get("key_title") or "").strip() or f"eisy - {socket.gethostname()}"
    actions: list[str] = []

    if scope == "deploy":
        alias = f"github.com-{owner}-{repo}"
        key_path = ssh_dir / f"eisy_ai_deploy_{owner}_{repo}"
        test_host = alias
        ssh_remote_url = f"git@{alias}:{owner}/{repo}.git"
        hint = "A deploy key reaches only this repository. For one key across all your repos, re-run with key_scope=account."
    else:
        key_path = next((ssh_dir / n for n in _DEFAULT_KEY_NAMES if (ssh_dir / n).exists()), ssh_dir / "id_ed25519")
        test_host = "github.com"
        ssh_remote_url = f"git@github.com:{owner}/{repo}.git" if parsed else None
        hint = "An account key reaches every repository on the account. For a key limited to one repo, re-run with key_scope=deploy."

    def configured(who: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "configured",
            "scope": scope,
            "authenticated_as": who,
            "actions_taken": actions,
            "hint": hint,
        }
        if ssh_remote_url:
            result["ssh_remote_url"] = ssh_remote_url
        return result

    try:
        missing = await _sh(
            nucore_interface,
            "for b in ssh ssh-keygen ssh-keyscan; do command -v $b >/dev/null 2>&1 || echo $b; done",
        )
        if (missing.get("stdout") or "").strip():
            return {"error": f"missing required tools on this system: {', '.join(missing['stdout'].split())}"}

        prerequisites_present = key_path.exists() and (
            scope == "account" or "# >>> eisy-ai deploy key" in _read(ssh_dir / "config")
        )
        if prerequisites_present:
            state, detail = await _ssh_test(nucore_interface, prefix, test_host)
            if state == "ok":
                return configured(detail)

        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        ssh_dir.chmod(0o700)

        pub_path, generated = await _ensure_key(nucore_interface, prefix, key_path, email)
        if generated:
            actions.append("generated_key")
        if scope == "deploy" and _ensure_config_block(ssh_dir, owner, repo, alias, key_path):
            actions.append("wrote_ssh_config")
        if await _ensure_known_host(nucore_interface, prefix, ssh_dir):
            actions.append("added_known_host")

        state, detail = await _ssh_test(nucore_interface, prefix, test_host)
    except _Abort as abort:
        return {**abort.result, "actions_taken": actions}

    if state == "ok":
        return configured(detail)

    if state == "denied":
        public_key = pub_path.read_text(encoding="utf-8").strip()
        result = {
            "status": "needs_key_upload",
            "scope": scope,
            "public_key": public_key,
            "key_title": title,
            "instructions": _upload_instructions(scope, owner, repo, title, public_key),
            "actions_taken": actions,
            "hint": hint,
        }
        if ssh_remote_url:
            result["ssh_remote_url"] = ssh_remote_url
        return result

    return {"status": "error", "error": f"ssh test to GitHub failed: {detail}", "actions_taken": actions}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""
