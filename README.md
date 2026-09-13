# Claude Code + OpenCode + Codex in a devcontainer

A containerized development environment for running Claude Code with `bypassPermissions` enabled, opencode with unrestricted permissions, and Codex with approvals/sandboxing disabled. Built at [Trail of Bits](https://www.trailofbits.com/) for security audit workflows.

## Why Use This?

Running Claude with `bypassPermissions` (or opencode / Codex unrestricted) on your host machine is risky—it can execute any command without confirmation. This devcontainer provides **filesystem isolation**, so an unrestricted agent reaches only your project directory and a disposable container, not the rest of your host.

**Designed for:**

- **Security audits**: Review client code without exposing your host
- **Untrusted repositories**: Explore unknown codebases safely
- **Experimental work**: Let Claude, opencode, or Codex modify code freely in isolation
- **Multi-repo engagements**: Work on multiple related repositories

## Prerequisites

- **Docker runtime** (one of):
  - [Docker Desktop](https://docker.com/products/docker-desktop) - ensure it's running
  - [OrbStack](https://orbstack.dev/)
  - [Colima](https://github.com/abiosoft/colima): `brew install colima docker && colima start`

- **For terminal workflows** (one-time install):

  ```bash
  npm install -g @devcontainers/cli
  git clone https://github.com/trailofbits/claude-code-devcontainer ~/.claude-devcontainer
  ~/.claude-devcontainer/install.sh self-install
  ```

<details>
<summary><strong>Optimizing Colima for Apple Silicon</strong></summary>

Colima's defaults (QEMU + sshfs) are conservative. For better performance:

```bash
# Stop and delete current VM (removes containers/images)
colima stop && colima delete

# Start with optimized settings
colima start \
  --cpu 4 \
  --memory 8 \
  --disk 100 \
  --vm-type vz \
  --vz-rosetta \
  --mount-type virtiofs
```

Adjust `--cpu` and `--memory` based on your Mac (e.g., 6/16 for Pro, 8/32 for Max).

| Option | Benefit |
|--------|---------|
| `--vm-type vz` | Apple Virtualization.framework (faster than QEMU) |
| `--mount-type virtiofs` | 5-10x faster file I/O than sshfs |
| `--vz-rosetta` | Run x86 containers via Rosetta |

Verify with `colima status` - should show "macOS Virtualization.Framework" and "virtiofs".

</details>

## Quick Start

Choose the pattern that fits your workflow:

### Pattern A: Per-Project Container (Isolated)

Each project gets its own container with independent volumes. Best for one-off reviews or when you need isolation between projects.

**Terminal:**

```bash
git clone <untrusted-repo>
cd untrusted-repo
devc .          # Installs template + starts container
devc shell      # Opens shell in container
```

**VS Code / Cursor:**

> **Not recommended for untrusted code.** Container code can execute commands on your host through this path, by design. See [Threat Model](#threat-model).

1. Install the Dev Containers extension:
   - VS Code: `ms-vscode-remote.remote-containers`
   - Cursor: `anysphere.remote-containers`

2. Set up the devcontainer (choose one):

   ```bash
   # Option A: Use devc (recommended)
   devc .

   # Option B: Clone manually
   git clone https://github.com/trailofbits/claude-code-devcontainer .devcontainer/
   ```

3. Open **your project folder** in VS Code, then:
   - Press `Cmd+Shift+P` (Mac) or `Ctrl+Shift+P` (Windows/Linux)
   - Type "Reopen in Container" and select **Dev Containers: Reopen in Container**

### Pattern B: Shared Workspace Container (Grouped)

A parent directory contains the devcontainer config, and you clone multiple repos inside. Shared volumes across all repos. Best for client engagements, related repositories, or ongoing work.

```bash
# Create workspace for a client engagement
mkdir -p ~/sandbox/client-name
cd ~/sandbox/client-name
devc .          # Install template + start container
devc shell      # Opens shell in container

# Inside container:
git clone <client-repo-1>
git clone <client-repo-2>
cd client-repo-1
claude            # Ready to work (or: opencode, codex)
```

## Token-Based Auth (Headless)

For headless servers or to skip the interactive login wizard:

```bash
claude setup-token                          # run on host, one-time
export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
devc rebuild                                # rebuilds with token
```

The token is forwarded into the container. On each container creation, `post_install.py` runs a one-shot auth handshake so `claude` starts without the login wizard.

This works around Claude Code's interactive onboarding wizard always showing in containers, even with valid credentials ([#8938](https://github.com/anthropics/claude-code/issues/8938)).

If you don't set a token, the interactive login flow works as before.

### opencode auth

opencode needs no onboarding handshake. It picks up provider keys forwarded via `remoteEnv`:

- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_GENERATIVE_AI_API_KEY`, `OPENROUTER_API_KEY`, `OPENCODE_API_KEY`

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
devc rebuild
```

Alternatively, run `opencode auth login` (or `/connect` in the TUI) inside the container. Credentials persist in the per-project `opencode-data` volume (`~/.local/share/opencode/auth.json`), alongside `~/.config/opencode` settings, so they survive `devc rebuild`.

### codex auth

Codex needs no onboarding handshake. It picks up provider keys forwarded via `remoteEnv`:

- `OPENAI_API_KEY` (API-key auth), `CODEX_API_KEY` (single non-interactive `codex exec` runs), `CODEX_ACCESS_TOKEN` (ChatGPT/token automation)

```bash
export OPENAI_API_KEY=sk-...
devc rebuild
```

Alternatively, run `codex login` inside the container (ChatGPT OAuth or API key). `post_install.py` pins `cli_auth_credentials_store = "file"`, so credentials persist as `~/.codex/auth.json` in the per-project `codex` volume and survive `devc rebuild`. There is no OS keyring in the container, so keep the `file` store.

Codex runs unrestricted by default here (`approval_policy = "never"`, `sandbox_mode = "danger-full-access"`, network enabled, `/workspace` pre-trusted) since the container provides isolation — matching upstream guidance to use `danger-full-access` inside Docker. If you prefer guardrails, edit `~/.codex/config.toml` in the container or pass flags per run (e.g. `codex --sandbox workspace-write`).

### opencode server

`opencode serve` (headless HTTP API + OpenAPI spec) starts automatically on every container start via `postStartCommand` and listens on `0.0.0.0:4096`. Port `4096` is forwarded to the host (`forwardPorts` + `portsAttributes` in `devcontainer.json`), so from the host or the IDE you can reach:

- API base: `http://localhost:4096`
- OpenAPI spec: `http://localhost:4096/doc`

Details:

- Startup script: `start_opencode_server.sh` (shipped to `/opt/` by the Dockerfile, re-runs idempotently on each start). Logs go to `~/.local/share/opencode/serve.log` inside the container.
- Re-run manually inside the container: `bash /opt/start_opencode_server.sh` or `devc exec bash /opt/start_opencode_server.sh`.
- The port is fixed at `4096` (opencode's default) to match `forwardPorts` — no env setup needed. Running several project containers is fine: each gets its own forwarded host port automatically.
- Protect with basic auth: `export OPENCODE_SERVER_PASSWORD=...` (and optionally `OPENCODE_SERVER_USERNAME`, default `opencode`) on the host, then `devc rebuild`. Both are forwarded via `remoteEnv`.
- Prefer the browser UI instead? Run `opencode web --hostname 0.0.0.0 --port 4096` inside the container (stop the `serve` instance first if the port is taken). To attach a TUI to it: `opencode attach http://localhost:4096`.

## CLI Helper Commands

```
devc .              Install template + start container in current directory
devc up             Start the devcontainer
devc rebuild        Rebuild container (preserves persistent volumes)
devc destroy [-f]   Remove container, volumes, and image for current project
devc down           Stop the container
devc shell          Open zsh shell in container
devc exec CMD       Execute command inside the container
devc upgrade        Upgrade Claude Code, opencode, and codex in the container
devc mount SRC DST  Add a bind mount (host → container)
devc sync [project] [--trusted] Sync Claude Code sessions from devcontainers to host
devc cp SRC DST     Copy a path from the container to the host
devc template DIR   Copy devcontainer files to directory
devc self-install   Install devc to ~/.local/bin
devc update         Update devc to the latest version
```

> **Note:** Use `devc destroy` to clean up a project's Docker resources. Removing containers manually (e.g., `docker rm`) will leave orphaned volumes and images behind that `devc destroy` won't be able to find.

## Session Sync for `/insights` (Claude Code)

Claude Code's `/insights` command analyzes your session history, but it only reads from `~/.claude/projects/` on the host. Sessions inside devcontainer volumes are invisible to it.

`devc sync` copies session logs from all devcontainers (running and stopped) to the host so `/insights` can include them:

```bash
devc sync              # Sync all devcontainers
devc sync crypto       # Filter by project name (substring match)
```

Devcontainers are auto-discovered via Docker labels — no need to know container names or IDs. The sync is incremental, so it's safe to run repeatedly.

> **Security note:** this copies container-authored data onto your host, so it prompts first (`--trusted` skips it). Only `*.jsonl` logs are copied, always under a `-devcontainer-<project>` key, so a container cannot plant files elsewhere in `~/.claude/projects/`. The transcripts are still container-authored text that a later host session will read.

opencode sessions need no sync: they persist in the per-project `opencode-data` volume (`~/.local/share/opencode`) and survive `devc rebuild`. Codex sessions/auth persist the same way in the per-project `codex` volume (`~/.codex`). Use `devc destroy` to remove them with the project.

## File Sharing

### VS Code / Cursor

Drag files from your host into the VS Code Explorer panel — they are copied into `/workspace/` automatically. No configuration needed.

### Terminal: `devc mount`

To make a host directory available inside the container:

```bash
devc mount ~/drop /drop           # Read-write
devc mount ~/secrets /secrets --readonly
```

This adds a bind mount to `devcontainer.json` and recreates the container. Existing mounts are preserved across `devc template` updates.

**Tip:** A shared "drop folder" is useful for passing files in without mounting your entire home directory.

> **Security note:** Avoid mounting large host directories (e.g., `$HOME`). Every mounted path is writable from inside the container unless `--readonly` is specified, which undermines the filesystem isolation this project provides.

## Network Isolation

By default, containers have full outbound network access. For stricter security, use iptables to restrict network access.

### When to Enable Network Isolation

- Reviewing code that may contain malicious dependencies
- Auditing software with telemetry or phone-home behavior
- Maximum isolation for highly sensitive reviews

### Example: Claude + OpenCode + Codex + GitHub + Package Registries

Run this inside the container (`devc shell`). The allowlist lives in an `ipset` that the
`iptables` rule references by name, so refreshing it does not mean re-adding rules.

```bash
# 1. Loopback, plus DNS to whatever resolver the container was given.
#    Without this the final DROP blocks name resolution and nothing works.
sudo iptables -A OUTPUT -o lo -j ACCEPT
for ns in $(awk '/^nameserver/{print $2}' /etc/resolv.conf); do
  sudo iptables -A OUTPUT -p udp -d "$ns" --dport 53 -j ACCEPT
  sudo iptables -A OUTPUT -p tcp -d "$ns" --dport 53 -j ACCEPT
done

# 2. Resolve the allowlist into an ipset.
sudo ipset create allowed-egress hash:ip -exist
for host in api.anthropic.com github.com raw.githubusercontent.com \
            registry.npmjs.org pypi.org files.pythonhosted.org \
            opencode.ai api.openai.com api.gemini.google.com openrouter.ai models.dev; do
  for ip in $(getent ahostsv4 "$host" | awk '{print $1}' | sort -u); do
    sudo ipset add allowed-egress "$ip" -exist
  done
done

# 3. Allow the set, drop everything else.
sudo iptables -A OUTPUT -m set --match-set allowed-egress dst -j ACCEPT
sudo iptables -A OUTPUT -j DROP
```

### Trade-offs

- Blocks package managers unless you allowlist registries
- May break tools that require network access
- DNS is permitted (DNS remains an exfiltration channel)
- The allowlist is per-IP, so any other site behind the same CDN address is also reachable
- IPv6 is not filtered. If your Docker network has an IPv6 default route, mirror the
  rules with `ip6tables` and an `ipset ... family inet6`
- Rules are lost on container restart; re-apply them per session

## Threat Model

**Protects against:**
- Claude, opencode, or Codex running unrestricted during a session.
- Direct access to your SSH key material and other credentials
- Unrestricted, direct access to the whole filesystem
- Cross-engagement leakage

**Does not protect against:**

- **Container escape.** A container is containment, not a strong security boundary. Escape should be hard, not impossible.
- **Deferred escape.** Container-planted code can get executed on the host, when the user performs some action on the host. Planting files under shared `.git` folder is an example escape path.
- **VS Code "Reopen in Container".** The command runs an extension host *inside* the container wired to your editor over RPC, and container code can drive host-only editor commands (`terminal.newLocal` then `sendSequence`) to run shell commands on your host. This is [Microsoft's design](https://github.com/microsoft/vscode-remote-release/issues/6608#issuecomment-1112960548), not a bug here ([how it works](https://blog.theredguild.org/leveraging-vscode-internals-to-escape-containers/)).
- **Network rules overwrite.** Container has `NET_ADMIN` and passwordless sudo, its user can change the iptables rules dynamically.
- **Exfiltration of in-container credentials.** Claude, opencode/provider, Codex, GitHub, and other tokens provided to container are simply accessible inside it.

**Also not isolated:** forwarded SSH agent (container code can authenticate as you; keys stay on the host), `~/.gitconfig` (read-only). The Docker socket is not mounted.

## Container Details

| Component | Details |
|-----------|---------|
| Base | Ubuntu 24.04, Node.js 24, Python 3.13 + uv, zsh |
| User | `vscode` (passwordless sudo), working dir `/workspace` |
| Tools | `rg`, `fd`, `tmux`, `fzf`, `delta`, `iptables`, `ipset` |
| Volumes (survive rebuilds) | Command history (`/commandhistory`), Claude config (`~/.claude`), OpenCode config (`~/.config/opencode`), OpenCode data/auth (`~/.local/share/opencode`), OpenCode TUI state (`~/.local/state/opencode`), Codex config/auth/sessions (`~/.codex`), GitHub CLI auth (`~/.config/gh`) |
| Host mounts | `~/.gitconfig`, `.devcontainer/`, `.git/config`, `.git/hooks/` (all read-only) |
| Auto-configured | `bypassPermissions` mode (via `post_install.py`), opencode defaults (`permission: allow` except `.devcontainer/`, `autoupdate: false`, `share: disabled`), Codex defaults (`approval_policy: never`, `sandbox_mode: danger-full-access`, file-based auth, `/workspace` pre-trusted), skills from [anthropics/skills](https://github.com/anthropics/skills) + [trailofbits/skills](https://github.com/trailofbits/skills) + [trailofbits/skills-curated](https://github.com/trailofbits/skills-curated), git-delta |

Volumes are stored outside the container, so your shell history, Claude settings, opencode settings/auth, Codex config/auth/sessions, and `gh` login persist even after `devc rebuild`. Host `~/.gitconfig` is mounted read-only for git identity.

The container ships common development tooling so you can do all your work inside it, not just run Claude, opencode, or Codex. The intended workflow is: clone a repository, start the container, and stay in it. If you need extra runtimes, add them to the Dockerfile for repeat use or install them ad-hoc with `devc exec`.

## Troubleshooting

### "devcontainer CLI not found"

```bash
npm install -g @devcontainers/cli
```

### Container won't start

1. Check Docker is running
2. Try rebuilding: `devc rebuild`
3. Check logs: `docker logs $(docker ps -lq)`

### GitHub CLI auth not persisting

The gh volume may need ownership fix:

```bash
sudo chown -R $(id -u):$(id -g) ~/.config/gh
```

### Python/uv not working

Python is managed via uv:

```bash
uv run script.py              # Run a script
uv add package                # Add project dependency
uv run --with requests py.py  # Ad-hoc dependency
```

## Development

Build the image manually:

```bash
devcontainer build --workspace-folder .
```

Test the container:

```bash
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . zsh
```
