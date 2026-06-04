from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


class ExtensionMarketplaceManager:
    """Manage curated Git-backed intent extensions installed into local storage."""

    def __init__(
        self,
        *,
        marketplace_root: str | Path,
        installed_intents_directory: str | Path | None = None,
    ) -> None:
        self.marketplace_root = Path(marketplace_root).expanduser().resolve()
        self.catalog_path = self.marketplace_root / "catalog.json"
        self.state_path = self.marketplace_root / "state.json"
        self.repos_directory = self.marketplace_root / "repos"
        self.staging_directory = self.marketplace_root / "_staging"
        self.installed_intents_directory = (
            Path(installed_intents_directory).expanduser().resolve()
            if installed_intents_directory is not None
            else (self.marketplace_root / "intents").resolve()
        )

        self._ensure_directories()
        self._ensure_catalog_file()

    def discover_extensions(self, requirement_text: str | None = None) -> list[dict[str, Any]]:
        """Return curated extensions with optional keyword scoring against requirements."""
        requirements = (requirement_text or "").strip().lower()
        keywords = [token for token in requirements.split() if token]
        installed = self._installed_map()

        discovered: list[dict[str, Any]] = []
        for entry in self._catalog_entries():
            extension_id = str(entry.get("id", "")).strip()
            if not extension_id:
                continue

        #    haystack_parts = [
        #        extension_id,
        #        str(entry.get("name", "")),
        #        str(entry.get("description", "")),
        #        " ".join(str(tag) for tag in entry.get("tags", []) if isinstance(tag, str)),
        #    ]
        #    haystack = " ".join(haystack_parts).lower()
        #    score = sum(1 for keyword in keywords if keyword in haystack)
        #    if keywords and score == 0:
        #        continue
            state_item = installed.get(extension_id)
            discovered.append(
                {
                    "extension_id": extension_id,
                    "name": entry.get("name", extension_id),
                    "description": entry.get("description", ""),
                    "author": entry.get("author", ""),
                    "author_url": entry.get("author_url", ""),
                    "tags": [str(tag) for tag in entry.get("tags", []) if isinstance(tag, str)],
                    "repo_url": entry.get("git_url", ""),
                    "default_ref": entry.get("ref", "HEAD"),
                    "installed": state_item is not None,
                    "installed_sha": (state_item or {}).get("installed_sha"),
                }
            )

        discovered.sort(key=lambda item: (item.get("score", 0), item.get("name", "")), reverse=True)
        return discovered

    def list_installed_extensions(self) -> list[dict[str, Any]]:
        """Return installed extension metadata from local state."""
        state = self._load_state()
        installed = state.get("installed", {})
        if not isinstance(installed, dict):
            return []

        results: list[dict[str, Any]] = []
        for extension_id in sorted(installed.keys()):
            payload = installed.get(extension_id)
            if not isinstance(payload, dict):
                continue

            intent_path = self.installed_intents_directory / extension_id
            item = dict(payload)
            item["id"] = extension_id
            item["intent_path"] = str(intent_path)
            item["installed"] = intent_path.exists()
            results.append(item)

        return results

    def install_extension(self, extension_id: str, *, target_ref: str | None = None) -> dict[str, Any]:
        """Install one curated extension by id and return install metadata."""
        extension_id = extension_id.strip()
        if not extension_id:
            raise ValueError("extension_id is required")

        catalog_entry = self._catalog_entry_by_id(extension_id)
        repo_url = str(catalog_entry.get("git_url", "")).strip()
        if not repo_url:
            raise ValueError(f"Catalog entry '{extension_id}' is missing git_url")

        repo_directory = self.repos_directory / extension_id
        if repo_directory.exists():
            self._run_git(["-C", str(repo_directory), "fetch", "--all", "--tags", "--prune"])
        else:
            self._run_git(["clone", repo_url, str(repo_directory)])

        selected_ref = (target_ref or catalog_entry.get("ref") or "HEAD").strip()
        if not selected_ref:
            selected_ref = "HEAD"
        installed_sha = self._run_git(["-C", str(repo_directory), "rev-parse", f"{selected_ref}^{{commit}}"])

        with tempfile.TemporaryDirectory(prefix=f"{extension_id}-", dir=str(self.staging_directory)) as temp_dir_str:
            temp_directory = Path(temp_dir_str)
            checkout_directory = temp_directory / "checkout"
            self._run_git(["clone", str(repo_directory), str(checkout_directory)])
            self._run_git(["-C", str(checkout_directory), "checkout", "--force", installed_sha])

            intent_source_directory = checkout_directory 
            if not intent_source_directory.exists() or not intent_source_directory.is_dir():
                raise FileNotFoundError(
                    f"Extension '{extension_id}' source directory not found: {intent_source_directory}"
                )

            self._validate_intent_directory(extension_id=extension_id, intent_directory=intent_source_directory)

            staged_intent_directory = self.installed_intents_directory / f".{extension_id}.staged-{int(time.time())}"
            if staged_intent_directory.exists():
                shutil.rmtree(staged_intent_directory)
            shutil.copytree(intent_source_directory, staged_intent_directory)

            target_intent_directory = self.installed_intents_directory / extension_id
            backup_directory = self.installed_intents_directory / f".{extension_id}.backup-{int(time.time())}"

            if target_intent_directory.exists():
                target_intent_directory.rename(backup_directory)
            try:
                staged_intent_directory.rename(target_intent_directory)
            except Exception:
                if backup_directory.exists():
                    backup_directory.rename(target_intent_directory)
                raise
            finally:
                if backup_directory.exists():
                    shutil.rmtree(backup_directory)

        install_result = {
            "id": extension_id,
            "name": catalog_entry.get("name", extension_id),
            "repo_url": repo_url,
            "installed_sha": installed_sha,
            "selected_ref": selected_ref,
            "installed_at_epoch_s": time.time(),
            "intent_path": str(self.installed_intents_directory / extension_id),
            "status": "installed",
        }
        self._upsert_installed_state(extension_id=extension_id, payload=install_result)
        return install_result

    def uninstall_extension(self, extension_id: str) -> dict[str, Any]:
        """Uninstall one extension by id and return uninstall metadata."""
        extension_id = extension_id.strip()
        if not extension_id:
            raise ValueError("extension_id is required")

        intent_directory = self.installed_intents_directory / extension_id
        removed = False
        if intent_directory.exists():
            shutil.rmtree(intent_directory)
            removed = True

        state = self._load_state()
        installed = state.get("installed", {})
        if isinstance(installed, dict):
            installed.pop(extension_id, None)
            state["installed"] = installed
            self._save_state(state)

        return {
            "id": extension_id,
            "status": "uninstalled",
            "removed_files": removed,
        }

    def _ensure_directories(self) -> None:
        self.marketplace_root.mkdir(parents=True, exist_ok=True)
        self.repos_directory.mkdir(parents=True, exist_ok=True)
        self.staging_directory.mkdir(parents=True, exist_ok=True)
        self.installed_intents_directory.mkdir(parents=True, exist_ok=True)

    def _ensure_catalog_file(self) -> None:
        default_catalog_path = Path(__file__).resolve().parent / "extension_catalog.json"
        if default_catalog_path.exists():
            shutil.copyfile(default_catalog_path, self.catalog_path)
            return
        if not self.catalog_path.exists():
            self.catalog_path.write_text(json.dumps({"extensions": []}, indent=2), encoding="utf-8")

    def _catalog_entries(self) -> list[dict[str, Any]]:
        if not self.catalog_path.exists():
            return []
        with self.catalog_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        entries = payload.get("extensions", []) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            return []
        return [item for item in entries if isinstance(item, dict)]

    def _catalog_entry_by_id(self, extension_id: str) -> dict[str, Any]:
        for entry in self._catalog_entries():
            if str(entry.get("id", "")).strip() == extension_id:
                return entry
        raise KeyError(f"Unknown extension '{extension_id}'")

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"installed": {}}
        with self.state_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {"installed": {}}
        installed = payload.get("installed")
        if not isinstance(installed, dict):
            payload["installed"] = {}
        return payload

    def _save_state(self, payload: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _installed_map(self) -> dict[str, dict[str, Any]]:
        state = self._load_state()
        installed = state.get("installed", {})
        if not isinstance(installed, dict):
            return {}
        return {key: value for key, value in installed.items() if isinstance(value, dict)}

    def _upsert_installed_state(self, *, extension_id: str, payload: dict[str, Any]) -> None:
        state = self._load_state()
        installed = state.get("installed", {})
        if not isinstance(installed, dict):
            installed = {}
        installed[extension_id] = dict(payload)
        state["installed"] = installed
        self._save_state(state)

    def _run_git(self, args: list[str]) -> str:
        command = ["git", *args]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            details = stderr or stdout or "unknown git error"
            raise RuntimeError(f"Git command failed: {' '.join(command)}: {details}")
        return (completed.stdout or "").strip()

    def _validate_intent_directory(self, *, extension_id: str, intent_directory: Path) -> None:
        required_files = [
            intent_directory / "config.json",
            intent_directory / "prompt.md",
            intent_directory / "handler.py",
        ]
        missing = [str(path.name) for path in required_files if not path.exists()]
        if missing:
            raise FileNotFoundError(
                f"Extension '{extension_id}' missing required files: {', '.join(missing)}"
            )

        with (intent_directory / "config.json").open("r", encoding="utf-8") as handle:
            config_payload = json.load(handle)
        configured_intent = str(config_payload.get("intent", "")).strip()
        if configured_intent != extension_id:
            raise ValueError(
                f"Extension '{extension_id}' config intent mismatch: '{configured_intent}'"
            )
