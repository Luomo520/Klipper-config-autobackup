# Fluidd Cloud Backup API

The `cloud_backup` Moonraker component owns credentials, filesystem access,
archive creation, and Baidu Netdisk or GitHub uploads. Fluidd only consumes the API
below. All endpoints use Moonraker's normal authentication policy.

## Endpoints

- `GET /server/cloud_backup/status`
  Returns component readiness, credential/authentication state, OAuth state,
  the active job, automatic-backup schedule, latest successful upload time,
  and `history_updated_at` revision timestamp. Secrets and tokens are never
  included.
- `GET /server/cloud_backup/config`
  Returns selectable root aliases, selected aliases, bypy and web destination
  paths, bypy availability, and local retention count.
- `POST /server/cloud_backup/config`
  Accepts `provider` (`baidu` or `github`), Baidu `auth_mode` (`bypy` or
  `web_password`), provider destination settings, selected roots,
  automatic-backup settings, and
  write-only credentials for the selected mode. Automatic mode is `interval` or `startup`; intervals are
  1-365 days and startup delays are 1-1440 minutes. Root aliases must be a
  subset of the server allowlist.
  The request is fully validated before runtime state or credential files are
  changed. Configuration and authorization mutations return HTTP 409 while a
  backup is active.
- `POST /server/cloud_backup/oauth/device`
  Starts the interactive bypy authorization process.
- `GET /server/cloud_backup/oauth/status`
  Returns the authorization URL, expiry, message, and state. It never returns
  the one-time authorization code submitted by the user.
- `POST /server/cloud_backup/oauth/verify`
  Writes a one-time Baidu authorization code to the waiting bypy process.
- `POST /server/cloud_backup/oauth/revoke`
  Stops a pending authorization and removes bypy's local token directory.
- `POST /server/cloud_backup/web/login`
  Starts the isolated Playwright worker for optional Baidu password login.
- `GET /server/cloud_backup/web/status`
  Returns web-login progress and, only when required, a verification screenshot.
- `POST /server/cloud_backup/web/verify`
  Submits a CAPTCHA or SMS verification code to the active worker.
- `POST /server/cloud_backup/web/logout`
  Removes the persistent browser profile and marks the web session unauthorized.
- `POST /server/cloud_backup/github/logout`
  Removes the locally stored GitHub token.
- `POST /server/cloud_backup/backup`
  Accepts `reason` (10-500 characters) and optional `roots`. Returns a job
  immediately; archive creation and upload continue in the background. This
  manual endpoint remains available when automatic upload is enabled.
- `GET /server/cloud_backup/history`
  Returns newest-first completed job records. Each new job includes a
  `trigger` value of `manual` or `automatic` and the snapshotted `provider`.
- `GET /server/cloud_backup/job?job_id=...`
  Returns the active or historical job.
- `POST /server/cloud_backup/download`
  Accepts a historical `job_id`. Only successful jobs from the component's
  history are accepted. Moonraker verifies or retrieves the backup, creates a
  temporary `.tar.gz`, and returns its read-only file-manager root, filename,
  size, SHA-256, and expiry time. It never restores files into `config`.

## Notifications

- `cloud_backup:status_changed`
- `cloud_backup:job_progress`

Active jobs expose `archive_size`, `uploaded_bytes`, `upload_total_bytes`,
`upload_progress`, `current_file`, `uploaded_files`, and
`upload_total_files`. For bypy uploads, byte and file counters advance only
after the corresponding remote file has passed an exact byte-size check, so
the displayed values are verified progress rather than an elapsed-time
estimate. `backup-manifest.json` is uploaded last and is the completion marker.

Fluidd compares `history_updated_at` while polling so a short automatic job
that starts and finishes between polls still refreshes the visible history.

## Security Boundary

- The UI cannot provide arbitrary local paths.
- The default allowlist contains only Moonraker's `config` file root.
- Provider credentials are stored below `printer_data/cloud_backup` in a file
  with mode `0600`.
- GitHub credentials may instead be referenced from `moonraker.secrets` by the
  `github_token` option.
- In `web_password` mode, the username and password are stored only in
  `printer_data/cloud_backup/credentials.json` with mode `0600`. The password
  is write-only through the API and is never returned to Fluidd.
- bypy tokens, GitHub tokens, passwords, and authorization codes are never
  logged or returned to the browser.
- Web-login, configuration, and verification request bodies are redacted from
  Moonraker verbose logs.
- bypy paths are validated without traversal segments and are always relative
  to Baidu's fixed `/apps/bypy` application directory. The default visible
  destination is `我的应用数据/bypy/3D打印机备份`.
- Uploads are not marked successful from transfer text alone. Baidu web and
  bypy modes both verify the exact remote byte count before returning success.
- Download requests accept only a historical job ID, never an arbitrary local
  or remote path. Downloaded directory backups must pass both the manifest and
  `SHA256SUMS` checks before Moonraker publishes a temporary archive through
  its authenticated file handler.
- GitHub tokens are write-only, and uploads use the repository Contents API.
  Archives larger than 100 MiB are rejected before a request is sent.
