# Production deploy hardening

R15 introduced safe deploy behavior; later hardening keeps exact-commit deployment, bounded backups, readiness checks and rollback while preserving the current VPS account/path compatibility.

## What is active immediately

- deployment requires successful static checks, tests and coverage for the same SHA via the reusable `tests.yml` workflow and `needs: test`; failed, cancelled or skipped checks prevent the SSH job from starting;
- exact-commit, serialized deployment;
- runtime backup before the code switch;
- SQLite online backup plus copies of root `*.json` and the deletion ledger;
- `user_messages.log` is snapshotted as fixed-size immutable chunks; unchanged chunks are hard-linked between retained backups instead of copied again;
- `manifest.json` contains size/SHA-256 for direct files and size/SHA-256 plus per-chunk hashes for the history journal;
- only the three newest completed deploy backups are retained; incomplete snapshots are removed, and low-disk preflight can prune old completed backups while preserving the newest known-good snapshot;
- systemd + in-process loopback `/ready` + Telegram `getMe` health-check with 12 attempts, allowing an initial long poll to complete;
- code/dependency rollback while retaining the pre-deploy backup.

Backups are written next to the app directory, under `upupa-backups/`. The backup policy is intentionally bounded because `history.db` and the append-only journal can be large. R15 and later releases intentionally do not restore a database automatically: replacing live state during rollback could discard messages written after restart.

Readiness listens only on `127.0.0.1:8766`. It requires a successful `getUpdates` within 90 seconds, no subsequent polling failure, all required scheduler loops, and readable SQLite tables with an available write lock. The response PID must match systemd `MainPID`. Set `UPUPA_HEALTHCHECK_PORT` consistently in the bot service and healthcheck environment if overriding the port. No separate healthcheck polling client is started.

The first counter migration imports `message_stats.json` transactionally into `statistics.db`; malformed data fails startup. Before rolling back to a JSON-only revision, the workflow stops the service and exports current counters with `scripts/export_rank_counters.py`. SQLite is retained and a handoff marker makes the next upgrade import progress from the old version. If export fails, rollback stops before switching code and requires operator recovery; the bot remains stopped. Rollback uses the restored revision's healthcheck, so an older release is not required to implement `/ready`.

## Workflow secrets

| Secret | Legacy fallback | Purpose |
| --- | --- | --- |
| `DEPLOY_HOST` | current VPS address | SSH destination |
| `DEPLOY_USER` | `root` | SSH account |
| `DEPLOY_APP_DIR` | `/root/upupa` | repository and runtime-state directory |
| `DEPLOY_SERVICE` | `upupa_bot.service` | systemd unit |
| `SSH_PRIVATE_KEY` | none | runner authentication to the VPS |

The current compatibility workflow intentionally uses `StrictHostKeyChecking=no` and emits a warning. There is no `SSH_KNOWN_HOSTS` requirement. If strict host-key verification is introduced later, it should be done as a separate operational change with a trusted host-key source.

## Moving to a dedicated deploy user

Perform these steps from the VPS console in a separate maintenance window.

1. Create a non-login-purpose account such as `upupa-deploy` with a home directory.
2. Prepare the final application directory, for example `/srv/upupa`, and make the deploy account its owner. Copy the repository, virtual environment and runtime state with ownership and permissions appropriate for the service.
3. Add the GitHub Actions SSH public key to that account's `authorized_keys`. Do not send private keys through issues, logs or chat.
4. Give the VPS deploy account its own read-only GitHub repository deploy key. The runner key authenticates to the VPS; a remote `git fetch` needs credentials available on the VPS itself.
5. Grant passwordless sudo only for the exact unit operations used by the workflow. Verify the real `systemctl` path first, then edit a dedicated sudoers file with `visudo`. Required operations are:
   - `systemctl restart upupa_bot.service`
   - `systemctl is-active --quiet upupa_bot.service`
   - `systemctl show --property MainPID --value upupa_bot.service`
   - `systemctl stop upupa_bot.service` (counter handoff before legacy rollback)
6. If the app moves from `/root/upupa`, update the systemd unit's `WorkingDirectory` and `ExecStart`, reload systemd, and verify a manual restart. Ensure the runtime service user can read/write the state files.
7. Set the workflow secrets and run one normal PR/merge deploy.
8. Confirm the service, Telegram `getMe`, logs, backup manifest and a representative bot command before disabling root SSH deployment.

Do not switch only `DEPLOY_USER` in isolation. Directory ownership, remote GitHub access, limited sudo and the systemd unit must be ready together.

## Recovery notes

A failed deploy prints the retained backup directory. Inspect its `manifest.json` and verify hashes before restoration. Stop the service before manually replacing live state, preserve the failed state separately, and use SQLite-aware restoration procedures. Code rollback is automatic; data restoration is an explicit operator decision.

New backups do not contain a monolithic `user_messages.log`. To materialize and verify the journal from either the new chunked format or an older full-file backup:

```bash
python scripts/restore_history_journal.py \
  --backup /root/upupa-backups/<snapshot> \
  --output /tmp/user_messages.log
```

The restore command refuses to overwrite an existing output unless `--force` is passed. Reconstruct to a temporary path first; do not point it at the live journal while the bot is running.

## History index rollout and recovery

The bot requires SQLite FTS5. After backup, deployment runs the target revision's standalone `scripts/index_history.py` before switching code, while the old bot keeps serving. It leaves the final multiline record open for the live writer; startup finalizes the remaining tail before polling. The first import checkpoints every 1000 journal records and resumes after interruption. The deploy job allows 60 minutes; a preflight failure leaves the old code running and preserves import progress. Manual launches without preflight perform the initial import during startup, so allow sufficient startup time for a large journal. Subsequent starts only process the new tail.

Keep `history.db`, the deletion ledger and the journal snapshot from the same backup together when restoring. For R24+ snapshots, first materialize `user_messages.log` with `scripts/restore_history_journal.py`; older snapshots already contain the full file directly. Source path/inode can change, but the indexed checkpoint bytes must still match. Pending writes retain their original Telegram message ID and recover even when the copied journal contains later entries than the database snapshot.

Before rollback to a release without `sqlite_history.py`, the workflow stops the bot and invokes `scripts/prepare_history_rollback.py`. Failure leaves the service stopped and preserves data for recovery. The prior release can then continue using the unchanged journal format; the next upgrade indexes its new tail without replaying already imported rows. Do not truncate/rotate this journal independently of the index.
