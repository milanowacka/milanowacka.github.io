#!/usr/bin/env python3
"""Desktop content manager for the milanowacka.github.io site.

A small GUI with three actions:
  - Pull latest content from GitHub
  - Preview the site locally (regenerates HTML from content/page-content.md
    without pushing anything)
  - Push local changes to GitHub (which triggers the site rebuild)

Git operations use dulwich (a pure-Python git implementation) instead of
shelling out to a `git` binary, so nothing besides this program needs to be
installed. Two remote auth methods are supported, chosen automatically from
the repo's `origin` URL:
  - HTTPS: a GitHub username + Personal Access Token
  - SSH (git@github.com:... or ssh://...): a private key file, via paramiko
Both are stored via the OS credential store (keyring) when available.

Run from source with a normal Python install, or package as a standalone
.exe with PyInstaller (see app/README.md).
"""
import io
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog

import paramiko
import paramiko.ssh_exception
from dulwich import client as dulwich_client
from dulwich import porcelain
from dulwich.client import HTTPUnauthorized
from dulwich.repo import Repo as DulwichRepo

try:
    import keyring
    _KEYRING_AVAILABLE = True
except Exception:
    keyring = None
    _KEYRING_AVAILABLE = False

# `_paramiko_vendor.py` sits alongside this file (see that file for why it's
# vendored rather than imported from dulwich.contrib).
from _paramiko_vendor import ParamikoSSHVendor  # noqa: E402

dulwich_client.get_ssh_vendor = ParamikoSSHVendor

# Allow `import site_generator` both when run from source (site_generator.py
# lives one directory up, at the repo root) and when frozen by PyInstaller
# (it gets bundled alongside this file, so the plain import below just works).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_generator import generate_site  # noqa: E402

APP_TITLE = "Mila Nowacka — Website Content Manager"

# If True, the startup sync always pops the last-session stash right after
# pulling, even if the pull moved HEAD. dulwich's stash_pop doesn't do a
# real three-way merge (see _pop_latest_stash below) — it checks out the
# stash's full snapshot verbatim, so this can silently revert any tracked
# file the pull just updated back to its pre-stash state. Set to False to
# go back to the safe behaviour: leave the stash on hold and let the user
# reconcile it by hand (e.g. `git stash pop`) whenever HEAD moved.
AUTO_POP_STASH_AFTER_PULL = True

CONFIG_FILE = Path.home() / ".mila_content_manager.json"
KEYRING_SERVICE_HTTPS = "mila-content-manager-github"
KEYRING_SERVICE_SSH = "mila-content-manager-github-ssh"
SSH_DIR = Path.home() / ".ssh"
SSH_KEY_CANDIDATES = ["id_ed25519", "id_ecdsa", "id_rsa"]

# GitHub's own published SSH host keys, so a first-time SSH connection works
# without the artist ever having to manually accept/verify a host key.
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
# Cross-checked against `gh api meta --jq .ssh_keys` (2026-08-29).
GITHUB_KNOWN_HOSTS = [
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
    "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=",
    "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=",
]


def _ensure_github_known_hosts() -> None:
    """Pre-populate ~/.ssh/known_hosts with GitHub's pinned host keys.

    The vendored ParamikoSSHVendor fails closed (RejectPolicy) against
    unknown host keys, which is the right default — but without this, a
    first-time SSH pull/push on a fresh machine would fail with no way for
    a non-technical user to resolve it. Only ever appends GitHub's own
    published keys, never trusts whatever the server presents.
    """
    known_hosts_path = SSH_DIR / "known_hosts"
    try:
        SSH_DIR.mkdir(mode=0o700, exist_ok=True)
        existing = known_hosts_path.read_text() if known_hosts_path.exists() else ""
        missing = [line for line in GITHUB_KNOWN_HOSTS if line.split()[2] not in existing]
        if missing:
            with known_hosts_path.open("a") as f:
                for line in missing:
                    f.write(line + "\n")
            known_hosts_path.chmod(0o600)
    except OSError:
        pass


def _is_ssh_url(url: str) -> bool:
    return url.startswith("ssh://") or bool(url.startswith("git@") and ":" in url)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(config))
    except OSError:
        pass


def is_valid_repo(path: Path) -> bool:
    return (path / ".git").is_dir() and (path / "content" / "page-content.md").is_file()


class CredentialsDialog(simpledialog.Dialog):
    """Collects a GitHub username + Personal Access Token."""

    def __init__(self, parent, initial_username: str = ""):
        self.initial_username = initial_username
        self.username: str | None = None
        self.token: str | None = None
        super().__init__(parent, title="GitHub Credentials")

    def body(self, master):
        tk.Label(master, text="GitHub username:").grid(row=0, column=0, sticky="w", pady=4)
        self.username_entry = tk.Entry(master, width=32)
        self.username_entry.grid(row=0, column=1, pady=4)
        self.username_entry.insert(0, self.initial_username)

        tk.Label(master, text="Personal Access Token:").grid(row=1, column=0, sticky="w", pady=4)
        self.token_entry = tk.Entry(master, width=32, show="•")
        self.token_entry.grid(row=1, column=1, pady=4)

        tk.Label(
            master,
            text="Create one at github.com → Settings → Developer settings →\n"
                 "Personal access tokens (needs 'repo' scope).",
            fg="gray", justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        return self.username_entry

    def validate(self):
        if not self.username_entry.get().strip() or not self.token_entry.get().strip():
            messagebox.showwarning("Missing info", "Please fill in both fields.", parent=self)
            return False
        return True

    def apply(self):
        self.username = self.username_entry.get().strip()
        self.token = self.token_entry.get().strip()


class ContentManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("720x480")
        self.minsize(560, 380)

        self.repo_path: Path | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.buttons: list[tk.Button] = []
        self._startup_synced = False

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self.after(100, self._drain_log_queue)

        config = load_config()
        saved_path = config.get("repo_path")
        if saved_path and is_valid_repo(Path(saved_path)):
            self.repo_path = Path(saved_path)
        else:
            self.after(200, self._prompt_for_repo)
        self._refresh_repo_label()
        self.after(300, self._maybe_startup_sync)

    # ---------------------------------------------------------------- UI --
    def _build_ui(self):
        top = tk.Frame(self, padx=10, pady=10)
        top.pack(fill="x")

        self.repo_label = tk.Label(top, text="Working folder: (none selected)", anchor="w")
        self.repo_label.pack(side="left", fill="x", expand=True)

        creds_btn = tk.Button(top, text="GitHub Access…", command=self._reconfigure_auth)
        creds_btn.pack(side="right", padx=(5, 0))

        change_btn = tk.Button(top, text="Change Folder…", command=self._prompt_for_repo)
        change_btn.pack(side="right")

        btn_frame = tk.Frame(self, padx=10, pady=5)
        btn_frame.pack(fill="x")

        pull_btn = tk.Button(btn_frame, text="⬇  Pull Latest", width=18,
                              command=self._on_pull)
        edit_btn = tk.Button(btn_frame, text="📝  Edit Content", width=18,
                              command=self._on_edit_content)
        preview_btn = tk.Button(btn_frame, text="👁  Preview Locally", width=18,
                                 command=self._on_preview)
        push_btn = tk.Button(btn_frame, text="⬆  Push Changes", width=18,
                              command=self._on_push)

        for b in (pull_btn, edit_btn, preview_btn, push_btn):
            b.pack(side="left", padx=5, pady=5)
            self.buttons.append(b)

        log_frame = tk.Frame(self, padx=10)
        log_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.log_widget = scrolledtext.ScrolledText(
            log_frame, state="disabled", wrap="word", font=("Consolas", 10)
        )
        self.log_widget.pack(fill="both", expand=True)

    def _refresh_repo_label(self):
        text = f"Working folder: {self.repo_path}" if self.repo_path else "Working folder: (none selected)"
        self.repo_label.config(text=text)

    # ------------------------------------------------------------- Logging --
    def log(self, message: str):
        self.log_queue.put(message)

    def _log_stream(self, buffer: io.BytesIO):
        text = buffer.getvalue().decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip():
                self.log(line)

    def _drain_log_queue(self):
        while not self.log_queue.empty():
            message = self.log_queue.get_nowait()
            self.log_widget.config(state="normal")
            self.log_widget.insert("end", message + "\n")
            self.log_widget.see("end")
            self.log_widget.config(state="disabled")
        self.after(100, self._drain_log_queue)

    def _set_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for b in self.buttons:
            b.config(state=state)

    # -------------------------------------------------------- Repo folder --
    def _prompt_for_repo(self):
        messagebox.showinfo(
            "Select website folder",
            "Please select the folder on your computer where the website "
            "project (the git repository) is checked out.",
        )
        selected = filedialog.askdirectory(title="Select website project folder")
        if not selected:
            if self.repo_path is None:
                messagebox.showwarning(
                    "No folder selected",
                    "No working folder is set. Pull, Preview and Push will not work "
                    "until you select the website project folder.",
                )
            return
        path = Path(selected)
        if not is_valid_repo(path):
            messagebox.showerror(
                "Invalid folder",
                "That folder doesn't look like the website project "
                "(missing .git or content/page-content.md). Please choose the "
                "correct folder.",
            )
            return
        self.repo_path = path
        save_config({**load_config(), "repo_path": str(path)})
        self._refresh_repo_label()
        self.log(f"Working folder set to: {path}")
        self._maybe_startup_sync()

    def _require_repo(self) -> Path | None:
        if self.repo_path is None:
            messagebox.showwarning("No folder selected", "Please select the website project folder first.")
            self._prompt_for_repo()
            return None
        return self.repo_path

    # --------------------------------------------------------- Credentials --
    def _get_remote_url(self, repo_path: Path) -> str | None:
        try:
            with DulwichRepo(str(repo_path)) as r:
                try:
                    return r.get_config().get((b"remote", b"origin"), b"url").decode()
                except KeyError:
                    return None
        except Exception as exc:
            self.log(f"(could not read remote URL: {exc})")
            return None

    def _ensure_auth(self, repo_path: Path, force_prompt: bool = False) -> dict | None:
        """Returns kwargs to splat into porcelain.pull/push, or None if cancelled."""
        url = self._get_remote_url(repo_path)
        if url and _is_ssh_url(url):
            return self._ensure_ssh_auth(force_prompt=force_prompt)
        return self._ensure_https_credentials(force_prompt=force_prompt)

    def _ensure_auth_silent(self, repo_path: Path) -> dict | None:
        """Like _ensure_auth, but never opens a dialog.

        Used for the background startup check: returns already-stored
        credentials, {} for an anonymous HTTPS fetch (fine for this public
        repo), or None if an SSH key would need to be picked/unlocked
        interactively (in which case the startup check is skipped).
        """
        url = self._get_remote_url(repo_path)
        if url and _is_ssh_url(url):
            config = load_config()
            key_path = config.get("ssh_key_path")
            if not key_path or not Path(key_path).is_file():
                return None
            passphrase = self._get_stored_ssh_passphrase(key_path)
            try:
                paramiko.PKey.from_path(key_path, password=passphrase)
            except Exception:
                return None
            _ensure_github_known_hosts()
            return {"key_filename": key_path, "password": passphrase}

        config = load_config()
        username = config.get("github_username", "")
        if username:
            token = self._get_stored_token(username)
            if token:
                return {"username": username, "password": token}
        return {}

    def _reconfigure_auth(self):
        repo_path = self._require_repo()
        if repo_path is None:
            return
        if self._ensure_auth(repo_path, force_prompt=True) is not None:
            self.log("✓ GitHub access updated.")

    # HTTPS (username + Personal Access Token) ------------------------------
    def _get_stored_token(self, username: str) -> str | None:
        if _KEYRING_AVAILABLE:
            try:
                token = keyring.get_password(KEYRING_SERVICE_HTTPS, username)
                if token:
                    return token
            except Exception as exc:
                self.log(f"(keyring unavailable: {exc})")
        return load_config().get("github_token_insecure")

    def _store_token(self, username: str, token: str) -> None:
        if _KEYRING_AVAILABLE:
            try:
                keyring.set_password(KEYRING_SERVICE_HTTPS, username, token)
                return
            except Exception as exc:
                self.log(f"(keyring unavailable, storing token in local config instead: {exc})")
        config = load_config()
        config["github_token_insecure"] = token
        save_config(config)

    def _clear_bad_credentials(self, username: str) -> None:
        if _KEYRING_AVAILABLE:
            try:
                keyring.delete_password(KEYRING_SERVICE_HTTPS, username)
            except Exception:
                pass
        config = load_config()
        config.pop("github_token_insecure", None)
        save_config(config)

    def _ensure_https_credentials(self, force_prompt: bool = False) -> dict | None:
        config = load_config()
        username = config.get("github_username", "")
        token = None
        if not force_prompt and username:
            token = self._get_stored_token(username)
        if username and token:
            return {"username": username, "password": token}

        dialog = CredentialsDialog(self, initial_username=username)
        if dialog.username is None:
            return None
        config["github_username"] = dialog.username
        save_config(config)
        self._store_token(dialog.username, dialog.token)
        return {"username": dialog.username, "password": dialog.token}

    # SSH (private key file, optionally passphrase-protected) ---------------
    def _get_stored_ssh_passphrase(self, key_path: str) -> str | None:
        if _KEYRING_AVAILABLE:
            try:
                passphrase = keyring.get_password(KEYRING_SERVICE_SSH, key_path)
                if passphrase:
                    return passphrase
            except Exception as exc:
                self.log(f"(keyring unavailable: {exc})")
        return load_config().get("ssh_passphrase_insecure")

    def _store_ssh_passphrase(self, key_path: str, passphrase: str) -> None:
        if _KEYRING_AVAILABLE:
            try:
                keyring.set_password(KEYRING_SERVICE_SSH, key_path, passphrase)
                return
            except Exception as exc:
                self.log(f"(keyring unavailable, storing passphrase in local config instead: {exc})")
        config = load_config()
        config["ssh_passphrase_insecure"] = passphrase
        save_config(config)

    def _clear_ssh_passphrase(self, key_path: str) -> None:
        if _KEYRING_AVAILABLE:
            try:
                keyring.delete_password(KEYRING_SERVICE_SSH, key_path)
            except Exception:
                pass
        config = load_config()
        config.pop("ssh_passphrase_insecure", None)
        save_config(config)

    def _ensure_ssh_auth(self, force_prompt: bool = False) -> dict | None:
        config = load_config()
        stored_path = config.get("ssh_key_path")
        key_path = Path(stored_path) if stored_path else None

        if force_prompt or key_path is None or not key_path.is_file():
            candidates = [SSH_DIR / name for name in SSH_KEY_CANDIDATES if (SSH_DIR / name).is_file()]
            if candidates and not force_prompt:
                key_path = candidates[0]
            else:
                messagebox.showinfo(
                    "Select SSH private key",
                    "Please select your SSH private key file — the one whose "
                    "matching public key you've added to your GitHub account.",
                )
                selected = filedialog.askopenfilename(
                    title="Select SSH private key",
                    initialdir=str(SSH_DIR) if SSH_DIR.is_dir() else str(Path.home()),
                )
                if not selected:
                    return None
                key_path = Path(selected)
            config["ssh_key_path"] = str(key_path)
            save_config(config)

        key_path_str = str(key_path)
        passphrase = None if force_prompt else self._get_stored_ssh_passphrase(key_path_str)

        try:
            paramiko.PKey.from_path(key_path_str, password=passphrase)
        except paramiko.ssh_exception.PasswordRequiredException:
            passphrase = simpledialog.askstring(
                "SSH key passphrase",
                f"Enter the passphrase for {key_path.name}:",
                show="•", parent=self,
            )
            if passphrase is None:
                return None
            try:
                paramiko.PKey.from_path(key_path_str, password=passphrase)
            except Exception as exc:
                messagebox.showerror("SSH key error", f"Could not unlock the key:\n{exc}")
                return None
            self._store_ssh_passphrase(key_path_str, passphrase)
        except Exception as exc:
            messagebox.showerror("SSH key error", f"Could not read the SSH key:\n{exc}")
            return None

        _ensure_github_known_hosts()
        return {"key_filename": key_path_str, "password": passphrase}

    # ------------------------------------------------------------- Actions --
    def _run_in_thread(self, target, *args):
        self._set_buttons_enabled(False)

        def wrapper():
            try:
                target(*args)
            except Exception as exc:  # surfaced to the log, not a crash
                self.log(f"✗ Error: {exc}")
            finally:
                self.after(0, lambda: self._set_buttons_enabled(True))

        threading.Thread(target=wrapper, daemon=True).start()

    @staticmethod
    def _status_is_dirty(status) -> bool:
        return bool(any(status.staged.values()) or status.unstaged or status.untracked)

    # --------------------------------------------------- Startup / shutdown --
    def _maybe_startup_sync(self):
        if self._startup_synced or self.repo_path is None:
            return
        self._startup_synced = True
        self._run_in_thread(self._do_startup_sync, self.repo_path)

    def _do_startup_sync(self, repo_path: Path):
        # Pull (not just fetch) before popping the stash: if the stash were
        # popped first while local HEAD is still behind, then pulled
        # afterwards, the merge would run against an already-dirty working
        # tree and could conflict with the incoming commits. Pulling onto a
        # clean tree first, then popping the stash on top, avoids that.
        before_sha = self._read_head_sha(repo_path)

        auth = self._ensure_auth_silent(repo_path)
        if auth is None:
            self.log("(skipped startup update check — SSH key not yet configured; use 'GitHub Access…')")
        else:
            self.log("Checking GitHub for new content…")
            out, err = io.BytesIO(), io.BytesIO()
            try:
                porcelain.pull(repo_path, outstream=out, errstream=err, **auth)
                self._log_stream(out)
                self._log_stream(err)
            except HTTPUnauthorized:
                self._log_stream(out)
                self._log_stream(err)
                self.log("(could not check GitHub for updates: GitHub rejected the saved username/token; use 'GitHub Access…' to re-enter it)")
                if "username" in auth:
                    self._clear_bad_credentials(auth["username"])
            except paramiko.ssh_exception.SSHException as exc:
                self._log_stream(out)
                self._log_stream(err)
                self.log(f"(could not check GitHub for updates: SSH error ({exc}); use 'GitHub Access…' to re-enter your key/passphrase)")
                self._clear_ssh_passphrase(auth["key_filename"])
            except Exception as exc:
                self._log_stream(out)
                self._log_stream(err)
                self.log(f"(could not automatically pull latest changes: {exc}) — use 'Pull Latest' to try manually.")

        after_sha = self._read_head_sha(repo_path)
        if before_sha is not None and after_sha is not None:
            self.log("✓ Pulled new content from GitHub." if before_sha != after_sha else "✓ Up to date with GitHub.")

        self._pop_latest_stash(repo_path, pulled=(before_sha != after_sha))

    @staticmethod
    def _read_head_sha(repo_path: Path) -> bytes | None:
        try:
            with DulwichRepo(str(repo_path)) as r:
                return r.head()
        except Exception:
            return None

    def _pop_latest_stash(self, repo_path: Path, pulled: bool = False):
        try:
            if not list(porcelain.stash_list(repo_path)):
                return

            # dulwich's stash_pop doesn't do a real three-way merge: it
            # checks out every file in the stash's full snapshot as-is, so
            # if HEAD moved since the stash was created (e.g. the pull above
            # just landed new content), it silently reverts *any* tracked
            # file back to its pre-stash state — not just the ones the
            # stash itself touched. AUTO_POP_STASH_AFTER_PULL controls
            # whether we accept that risk or play it safe.
            if pulled and not AUTO_POP_STASH_AFTER_PULL:
                self.log(
                    "⚠ Kept your changes from the last session on hold in the stash — "
                    "new content was just pulled from GitHub, and applying the stash now "
                    "could overwrite it. Merge them by hand (e.g. via 'git stash pop') "
                    "when you're ready."
                )
                return

            porcelain.stash_pop(repo_path)
            self.log("✓ Restored your changes from the last session (from stash).")
        except Exception as exc:
            self.log(f"(could not restore stashed changes from last session: {exc})")

    def _on_close(self):
        if self.repo_path is not None:
            try:
                status = porcelain.status(self.repo_path)
                if self._status_is_dirty(status):
                    porcelain.stash_push(self.repo_path)
                    messagebox.showinfo(
                        "Changes saved",
                        "Your unsaved changes were stashed and will be restored "
                        "automatically the next time you open this app.",
                    )
            except Exception as exc:
                messagebox.showwarning(
                    "Could not save changes",
                    f"Your local changes could not be safely stashed and remain "
                    f"in your working folder as-is:\n{exc}",
                )
        self.destroy()

    # Pull -------------------------------------------------------------
    def _on_pull(self):
        repo_path = self._require_repo()
        if repo_path is None:
            return

        try:
            status = porcelain.status(repo_path)
        except Exception as exc:
            self.log(f"✗ Could not read repository status: {exc}")
            return

        if self._status_is_dirty(status):
            proceed = messagebox.askyesno(
                "Uncommitted changes",
                "You have local changes that haven't been pushed yet.\n"
                "Pulling now could cause a conflict.\n\n"
                "Continue anyway?",
            )
            if not proceed:
                self.log("Pull cancelled.")
                return

        auth = self._ensure_auth(repo_path)
        if auth is None:
            self.log("Pull cancelled (no credentials).")
            return

        self._run_in_thread(self._do_pull, repo_path, auth)

    def _do_pull(self, repo_path: Path, auth: dict):
        self.log("Pulling latest content from GitHub…")
        out, err = io.BytesIO(), io.BytesIO()
        try:
            porcelain.pull(repo_path, outstream=out, errstream=err, **auth)
            self._log_stream(out)
            self._log_stream(err)
            self.log("✓ Pull complete.")
        except HTTPUnauthorized:
            self._log_stream(out)
            self._log_stream(err)
            self.log("✗ Pull failed: GitHub rejected the username/token. Use 'GitHub Access…' to re-enter it.")
            self.after(0, lambda: self._clear_bad_credentials(auth["username"]))
        except paramiko.ssh_exception.SSHException as exc:
            self._log_stream(out)
            self._log_stream(err)
            self.log(f"✗ Pull failed: SSH error ({exc}). Use 'GitHub Access…' to re-enter your key/passphrase.")
            self.after(0, lambda: self._clear_ssh_passphrase(auth["key_filename"]))
        except Exception as exc:
            self._log_stream(out)
            self._log_stream(err)
            self.log(f"✗ Pull failed: {exc}")

    # Edit content ---------------------------------------------------------
    def _on_edit_content(self):
        repo_path = self._require_repo()
        if repo_path is None:
            return
        md_file = repo_path / "content" / "page-content.md"
        if not md_file.is_file():
            messagebox.showerror("File not found", f"Could not find:\n{md_file}")
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["notepad.exe", str(md_file)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-t", str(md_file)])
            else:
                subprocess.Popen(["xdg-open", str(md_file)])
            self.log(f"Opened {md_file.name} for editing.")
        except OSError as exc:
            messagebox.showerror("Could not open editor", f"Failed to open {md_file.name}:\n{exc}")

    # Preview ------------------------------------------------------------
    def _on_preview(self):
        repo_path = self._require_repo()
        if repo_path is None:
            return
        self._run_in_thread(self._do_preview, repo_path)

    def _do_preview(self, repo_path: Path):
        self.log("Generating local preview from content/page-content.md…")
        import contextlib

        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                generate_site(repo_path)
        finally:
            for line in buffer.getvalue().splitlines():
                self.log(line)

        index_file = (repo_path / "index.html").resolve()
        self.log(f"✓ Preview generated. Opening {index_file.name} in your browser…")
        webbrowser.open(index_file.as_uri())
        self.log(
            "Note: this preview is local only — nothing has been pushed. "
            "Use 'Push Changes' when you're happy with it."
        )

    # Push -----------------------------------------------------------------
    def _on_push(self):
        repo_path = self._require_repo()
        if repo_path is None:
            return

        try:
            status = porcelain.status(repo_path)
        except Exception as exc:
            self.log(f"✗ Could not read repository status: {exc}")
            return

        if not self._status_is_dirty(status):
            messagebox.showinfo("Nothing to push", "There are no local changes to push.")
            return

        message = simpledialog.askstring(
            "Commit message",
            "Describe what changed:",
            initialvalue="Update content",
            parent=self,
        )
        if message is None:
            self.log("Push cancelled.")
            return
        message = message.strip() or "Update content"

        auth = self._ensure_auth(repo_path)
        if auth is None:
            self.log("Push cancelled (no credentials).")
            return

        self._run_in_thread(self._do_push, repo_path, message, auth)

    def _do_push(self, repo_path: Path, message: str, auth: dict):
        self.log("Staging and committing changes…")
        try:
            porcelain.add(repo_path, paths=None)
            porcelain.commit(repo_path, message=message)
        except Exception as exc:
            self.log(f"✗ Commit failed: {exc}")
            return

        self.log("Pushing to GitHub…")
        out, err = io.BytesIO(), io.BytesIO()
        try:
            porcelain.push(repo_path, outstream=out, errstream=err, **auth)
            self._log_stream(out)
            self._log_stream(err)
            self.log("✓ Push complete. GitHub will rebuild the live site shortly.")
        except HTTPUnauthorized:
            self._log_stream(out)
            self._log_stream(err)
            self.log("✗ Push failed: GitHub rejected the username/token. Use 'GitHub Access…' to re-enter it.")
            self.after(0, lambda: self._clear_bad_credentials(auth["username"]))
        except paramiko.ssh_exception.SSHException as exc:
            self._log_stream(out)
            self._log_stream(err)
            self.log(f"✗ Push failed: SSH error ({exc}). Use 'GitHub Access…' to re-enter your key/passphrase.")
            self.after(0, lambda: self._clear_ssh_passphrase(auth["key_filename"]))
        except Exception as exc:
            self._log_stream(out)
            self._log_stream(err)
            self.log(f"✗ Push failed: {exc}")


def main():
    app = ContentManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
