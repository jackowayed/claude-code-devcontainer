#!/usr/bin/env python3
"""Post-install configuration for Claude Code + OpenCode + Codex devcontainer.

Runs on container creation to set up:
- Onboarding bypass (when CLAUDE_CODE_OAUTH_TOKEN is set)
- Claude settings (bypassPermissions mode)
- OpenCode defaults (unrestricted permissions, no autoupdate/share)
- Codex defaults (unrestricted approvals/sandbox, file-based auth)
- Tmux configuration (200k history, mouse support)
- Directory ownership fixes for mounted volumes
"""

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path


def setup_onboarding_bypass():
    """Bypass the interactive onboarding wizard when CLAUDE_CODE_OAUTH_TOKEN is set.

    Runs `claude -p` to seed ~/.claude.json with auth state. The subprocess
    writes the config file during startup before the API call completes, so
    a timeout is expected and acceptable. After the subprocess finishes (or
    times out), we check whether ~/.claude.json was populated and only then
    set hasCompletedOnboarding.

    Workaround for https://github.com/anthropics/claude-code/issues/8938.
    """
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if not token:
        print(
            "[post_install] No CLAUDE_CODE_OAUTH_TOKEN set, skipping onboarding bypass",
            file=sys.stderr,
        )
        return

    # When `CLAUDE_CONFIG_DIR` is set, as is done in `devcontainer.json`, `claude` unexpectedly 
    # looks for `.claude.json` in *that* folder, instead of in `~`, contradicting the documentation.
    #  See https://github.com/anthropics/claude-code/issues/3833#issuecomment-3694918874
    claude_json_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home()))
    claude_json = claude_json_dir / ".claude.json"

    print("[post_install] Running claude -p to populate auth state...", file=sys.stderr)
    try:
        result = subprocess.run(
            ["claude", "-p", "ok"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(
                f"[post_install] claude -p exited {result.returncode}: "
                f"{result.stderr.strip()}",
                file=sys.stderr,
            )
    except subprocess.TimeoutExpired:
        print(
            "[post_install] claude -p timed out (expected on cold start)",
            file=sys.stderr,
        )
    except (FileNotFoundError, OSError) as e:
        print(
            f"[post_install] Warning: could not run claude ({e}) — "
            "onboarding bypass skipped",
            file=sys.stderr,
        )
        return

    if not claude_json.exists():
        print(
            f"[post_install] Warning: {claude_json} not created by claude -p — "
            "onboarding bypass skipped",
            file=sys.stderr,
        )
        return

    config: dict = {}
    try:
        config = json.loads(claude_json.read_text())
    except json.JSONDecodeError as e:
        print(
            f"[post_install] Warning: {claude_json} has invalid JSON ({e}), "
            "starting fresh",
            file=sys.stderr,
        )

    config["hasCompletedOnboarding"] = True

    claude_json.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(
        f"[post_install] Onboarding bypass configured: {claude_json}", file=sys.stderr
    )


def setup_claude_settings():
    """Configure Claude Code with bypassPermissions enabled."""
    claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    claude_dir.mkdir(parents=True, exist_ok=True)

    settings_file = claude_dir / "settings.json"

    # Load existing settings or start fresh
    settings = {}
    if settings_file.exists():
        with contextlib.suppress(json.JSONDecodeError):
            settings = json.loads(settings_file.read_text())

    # Set bypassPermissions mode
    if "permissions" not in settings:
        settings["permissions"] = {}
    settings["permissions"]["defaultMode"] = "bypassPermissions"

    settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(
        f"[post_install] Claude settings configured: {settings_file}", file=sys.stderr
    )


def setup_opencode_config():
    """Configure OpenCode with sandbox-friendly defaults.

    Mirrors the Claude bypassPermissions setup: allow all actions without
    approval prompts, since the container already provides filesystem
    isolation. Denies access to .devcontainer/ to match
    .claude/settings.json, as container-side config edits would execute
    on the host during rebuild.

    Merges with any existing user config instead of overwriting it.
    Disables autoupdate (use `devc upgrade`) and sharing by default.
    """
    config_dir = Path.home() / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "opencode.json"

    # Patterns blocking .devcontainer access, whether opencode runs from
    # /workspace or a subdirectory.
    devcontainer_denies = [
        ".devcontainer/**",
        "**/.devcontainer/**",
    ]

    def with_devcontainer_denies(existing: dict | str | None) -> dict:
        """Return a permission rule allowing everything except .devcontainer."""
        merged: dict[str, str] = {"*": "allow"}
        if isinstance(existing, dict):
            merged.update(existing)
        # Last matching rule wins, so denies go last.
        for pattern in devcontainer_denies:
            merged[pattern] = "deny"
        return merged

    config: dict = {}
    if config_file.exists():
        with contextlib.suppress(json.JSONDecodeError):
            loaded = json.loads(config_file.read_text())
            if isinstance(loaded, dict):
                config = loaded

    config.setdefault("$schema", "https://opencode.ai/config.json")
    # Pin updates to `devc upgrade` so the image version stays reproducible.
    config.setdefault("autoupdate", False)
    config.setdefault("share", "disabled")

    if "permission" not in config or isinstance(config["permission"], str):
        # String form ("allow") can't express denies; expand to object form.
        config["permission"] = {"*": "allow"}
    if not isinstance(config["permission"], dict):
        config["permission"] = {"*": "allow"}
    permissions = config["permission"]

    for tool in ("read", "edit", "glob", "grep"):
        permissions[tool] = with_devcontainer_denies(permissions.get(tool))

    config_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"[post_install] OpenCode config written: {config_file}", file=sys.stderr)


def setup_codex_config():
    """Configure Codex CLI with sandbox-friendly defaults.

    Mirrors the Claude bypassPermissions setup: no approval prompts and no
    sandbox restrictions, since the container already provides filesystem
    isolation. This matches the upstream guidance to run Codex with
    ``--sandbox danger-full-access`` inside containerized Linux environments
    where Landlock/seccomp sandboxing is unavailable.

    Merges with any existing user config instead of overwriting it (existing
    keys are left untouched). Also sets:

    - ``cli_auth_credentials_store = "file"`` so ``codex login`` credentials
      persist as ``auth.json`` inside the per-project ``.codex`` volume that
      survives rebuilds (no OS keyring in the container).
    - ``[sandbox_workspace_write] network_access = true`` so network works
      if the user drops back to ``workspace-write`` mode.
    - ``[projects."/workspace"] trust_level = "trusted"`` so Codex does not
      prompt to trust the shared workspace directory on every new checkout.
    """
    import re

    try:
        import tomllib
    except ImportError:  # Python < 3.11, should not happen (image has 3.13)
        tomllib = None  # type: ignore[assignment]

    codex_dir = Path.home() / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    config_file = codex_dir / "config.toml"

    existing_text = ""
    existed = config_file.exists()
    if existed:
        existing_text = config_file.read_text(encoding="utf-8")

    existing: dict = {}
    reset_invalid = False
    if existing_text.strip() and tomllib is not None:
        try:
            parsed = tomllib.loads(existing_text)
            if isinstance(parsed, dict):
                existing = parsed
        except Exception as e:
            backup = config_file.with_suffix(".toml.bak")
            backup.write_text(existing_text, encoding="utf-8")
            print(
                f"[post_install] Warning: {config_file} has invalid TOML ({e}), "
                f"backed up to {backup} and starting fresh",
                file=sys.stderr,
            )
            existing_text = ""
            existing = {}
            reset_invalid = True

    additions: list[str] = []

    def has_top_level_key(key: str) -> bool:
        if key in existing:
            return True
        # Fall back to a text scan for unparseable-but-present files.
        return (
            re.search(rf"(?m)^{re.escape(key)}\s*=", existing_text) is not None
        )

    if not has_top_level_key("approval_policy"):
        additions.append('approval_policy = "never"')
    if not has_top_level_key("sandbox_mode"):
        additions.append('sandbox_mode = "danger-full-access"')
    if not has_top_level_key("cli_auth_credentials_store"):
        additions.append('cli_auth_credentials_store = "file"')

    sandbox_section = existing.get("sandbox_workspace_write")
    if not isinstance(sandbox_section, dict):
        if not re.search(r"(?m)^\[sandbox_workspace_write\]", existing_text):
            additions.append("[sandbox_workspace_write]\nnetwork_access = true")
        elif (
            re.search(r"(?m)^\[sandbox_workspace_write\]", existing_text)
            and re.search(r"(?m)^network_access\s*=", existing_text) is None
        ):
            # Section header exists but the key is missing; appending a
            # duplicate header would be invalid TOML, so note it instead.
            print(
                "[post_install] Note: [sandbox_workspace_write] exists without "
                "network_access; leaving it untouched",
                file=sys.stderr,
            )
    elif "network_access" not in sandbox_section:
        print(
            "[post_install] Note: [sandbox_workspace_write] exists without "
            "network_access; leaving it untouched",
            file=sys.stderr,
        )

    projects = existing.get("projects")
    workspace_trusted = (
        isinstance(projects, dict)
        and isinstance(projects.get("/workspace"), dict)
        and projects["/workspace"].get("trust_level") == "trusted"
    )
    if not workspace_trusted and '[projects."/workspace"]' not in existing_text:
        additions.append('[projects."/workspace"]\ntrust_level = "trusted"')

    if not existed or reset_invalid:
        header = (
            "# Managed by post_install.py (devcontainer).\n"
            "# Unrestricted defaults: the container provides isolation, so\n"
            "# approvals and sandboxing are disabled. Mirrors Claude\n"
            "# bypassPermissions and OpenCode permission:allow.\n"
        )
        body = "\n\n".join(additions) + "\n" if additions else ""
        config_file.write_text(header + body, encoding="utf-8")
    elif additions:
        with config_file.open("a", encoding="utf-8") as f:
            f.write("\n# Added by post_install.py (devcontainer).\n")
            f.write("\n\n".join(additions) + "\n")

    print(f"[post_install] Codex config written: {config_file}", file=sys.stderr)


def setup_tmux_config():
    """Configure tmux with 200k history, mouse support, and vi keys."""
    tmux_conf = Path.home() / ".tmux.conf"

    if tmux_conf.exists():
        print("[post_install] Tmux config exists, skipping", file=sys.stderr)
        return

    config = """\
# 200k line scrollback history
set-option -g history-limit 200000

# Enable mouse support
set -g mouse on

# Use vi keys in copy mode
setw -g mode-keys vi

# Start windows and panes at 1, not 0
set -g base-index 1
setw -g pane-base-index 1

# Renumber windows when one is closed
set -g renumber-windows on

# Faster escape time for vim
set -sg escape-time 10

# True color support
set -g default-terminal "tmux-256color"
set -ag terminal-overrides ",xterm-256color:RGB"

# Terminal features (ghostty, cursor shape in vim)
set -as terminal-features ",xterm-ghostty:RGB"
set -as terminal-features ",xterm*:RGB"
set -ga terminal-overrides ",xterm*:colors=256"
set -ga terminal-overrides '*:Ss=\\E[%p1%d q:Se=\\E[ q'

# Status bar
set -g status-style 'bg=#333333 fg=#ffffff'
set -g status-left '[#S] '
set -g status-right '%Y-%m-%d %H:%M'
"""
    tmux_conf.write_text(config, encoding="utf-8")
    print(f"[post_install] Tmux configured: {tmux_conf}", file=sys.stderr)


def fix_directory_ownership():
    """Fix ownership of mounted volumes that may have root ownership."""
    uid = os.getuid()
    gid = os.getgid()

    dirs_to_fix = [
        Path.home() / ".claude",
        Path.home() / ".config" / "opencode",
        Path.home() / ".local" / "share" / "opencode",
        Path.home() / ".local" / "state" / "opencode",
        Path.home() / ".codex",
        Path("/commandhistory"),
        Path.home() / ".config" / "gh",
    ]

    for dir_path in dirs_to_fix:
        if dir_path.exists():
            try:
                # Use sudo to fix ownership if needed
                stat_info = dir_path.stat()
                if stat_info.st_uid != uid:
                    subprocess.run(
                        ["sudo", "chown", "-R", f"{uid}:{gid}", str(dir_path)],
                        check=True,
                        capture_output=True,
                    )
                    print(
                        f"[post_install] Fixed ownership: {dir_path}", file=sys.stderr
                    )
            except (PermissionError, subprocess.CalledProcessError) as e:
                print(
                    f"[post_install] Warning: Could not fix ownership of {dir_path}: {e}",
                    file=sys.stderr,
                )


def setup_global_gitignore():
    """Set up global gitignore and local git config.

    Since ~/.gitconfig is mounted read-only from host, we create a local
    config file that includes the host config and adds container-specific
    settings like core.excludesfile and delta configuration.

    GIT_CONFIG_GLOBAL env var (set in devcontainer.json) points git to this
    local config as the "global" config.
    """
    home = Path.home()
    gitignore = home / ".gitignore_global"
    local_gitconfig = home / ".gitconfig.local"
    host_gitconfig = home / ".gitconfig"

    # Create global gitignore with common patterns
    patterns = """\
# Claude Code
.claude/

# macOS
.DS_Store
.AppleDouble
.LSOverride
._*

# Python
*.pyc
*.pyo
__pycache__/
*.egg-info/
.eggs/
*.egg
.venv/
venv/
.mypy_cache/
.ruff_cache/

# Node
node_modules/
.npm/

# Editors
*.swp
*.swo
*~
.idea/
.vscode/
*.sublime-*

# Misc
*.log
.env.local
.env.*.local
"""
    gitignore.write_text(patterns, encoding="utf-8")
    print(f"[post_install] Global gitignore created: {gitignore}", file=sys.stderr)

    # Create local git config that includes host config and sets excludesfile + delta
    # Delta config is included here so it works even if host doesn't have it configured
    # safe.directory takes no path glob, and repos can sit anywhere under /workspace
    local_config = f"""\
# Container-local git config
# Includes host config (mounted read-only) and adds container settings

[include]
    path = {host_gitconfig}

[core]
    excludesfile = {gitignore}
    pager = delta

[interactive]
    diffFilter = delta --color-only

[delta]
    navigate = true
    light = false
    line-numbers = true
    side-by-side = false

[merge]
    conflictstyle = diff3

[diff]
    colorMoved = default

[gpg "ssh"]
    program = /usr/bin/ssh-keygen

# Bind mounts report a foreign uid, which trips git's ownership check
[safe]
    directory = *
"""
    local_gitconfig.write_text(local_config, encoding="utf-8")
    print(
        f"[post_install] Local git config created: {local_gitconfig}", file=sys.stderr
    )


def main():
    """Run all post-install configuration."""
    print("[post_install] Starting post-install configuration...", file=sys.stderr)

    setup_onboarding_bypass()
    setup_claude_settings()
    setup_opencode_config()
    setup_codex_config()
    setup_tmux_config()
    fix_directory_ownership()
    setup_global_gitignore()

    print("[post_install] Configuration complete!", file=sys.stderr)


if __name__ == "__main__":
    main()
