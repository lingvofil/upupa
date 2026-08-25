# Production deploy hardening

R15 adds safe deploy behavior without silently changing the current VPS account, path or SSH trust model. The workflow works with the existing installation and exposes a controlled migration path.

## What is active immediately

- exact-commit, serialized deployment;
- append-only backup before the code switch;
- SQLite online backup plus copies of root `*.json` and `user_messages.log`;
- `manifest.json` with size and SHA-256 for every copied file;
- systemd + Telegram `getMe` health-check with three attempts;
- code/dependency rollback while retaining the pre-deploy backup.

Backups are written next to the app directory, under `upupa-backups/`. They are not deleted automatically. R15 intentionally does not restore a database automatically: replacing live state during rollback could discard messages written after restart.

## Workflow secrets

| Secret | Legacy fallback | Purpose |
| --- | --- | --- |
| `DEPLOY_HOST` | current VPS address | SSH destination |
| `DEPLOY_USER` | `root` | SSH account |
| `DEPLOY_APP_DIR` | `/root/upupa` | repository and runtime-state directory |
| `DEPLOY_SERVICE` | `upupa_bot.service` | systemd unit |
| `SSH_KNOWN_HOSTS` | empty | complete trusted known_hosts line(s) |

When `SSH_KNOWN_HOSTS` is present, the workflow uses `StrictHostKeyChecking=yes`. Without it, the workflow emits a warning and retains the previous compatibility behavior.

## Moving to a dedicated deploy user

Perform these steps from the VPS console in a separate maintenance window.

1. Create a non-login-purpose account such as `upupa-deploy` with a home directory.
2. Prepare the final application directory, for example `/srv/upupa`, and make the deploy account its owner. Copy the repository, virtual environment and runtime state with ownership and permissions appropriate for the service.
3. Add the GitHub Actions SSH public key to that account's `authorized_keys`. Do not send private keys through issues, logs or chat.
4. Give the VPS deploy account its own read-only GitHub repository deploy key. The runner key authenticates to the VPS; a remote `git fetch` needs credentials available on the VPS itself.
5. Grant passwordless sudo only for the exact unit operations used by the workflow. Verify the real `systemctl` path first, then edit a dedicated sudoers file with `visudo`. Required operations are:
   - `systemctl restart upupa_bot.service`
   - `systemctl is-active --quiet upupa_bot.service`
6. If the app moves from `/root/upupa`, update the systemd unit's `WorkingDirectory` and `ExecStart`, reload systemd, and verify a manual restart. Ensure the runtime service user can read/write the state files.
7. Obtain the VPS host key through the provider console or another trusted channel. Compare its fingerprint independently; `ssh-keyscan` alone does not establish trust. Store the verified full known_hosts line in `SSH_KNOWN_HOSTS`.
8. Set all five workflow secrets and run one normal PR/merge deploy.
9. Confirm the service, Telegram `getMe`, logs, backup manifest and a representative bot command before disabling root SSH deployment.

Do not switch only `DEPLOY_USER` in isolation. Directory ownership, remote GitHub access, limited sudo, the systemd unit and host-key trust must be ready together.

## Recovery notes

A failed deploy prints the retained backup directory. Inspect its `manifest.json` and verify hashes before restoration. Stop the service before manually replacing live state, preserve the failed state separately, and use SQLite-aware restoration procedures. Code rollback is automatic; data restoration is an explicit operator decision.
