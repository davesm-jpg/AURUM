# AURUM local operation

## Container services

The Compose project runs the API and dashboard with a persistent SQLite
volume. Workers run when explicitly invoked. Both published ports bind to
localhost. The dashboard shares the API network namespace so its server-side
proxy can retain the existing loopback restriction.

1. Install a Docker-compatible runtime and Compose v2. Start the runtime.
2. Copy `.env.example` to `.env` and set a local `AURUM_API_TOKEN`. Generate a
   value with `openssl rand -hex 32`. Keep it local.
3. Create `data/` for authorized import files and reviewed calendars.
4. Run `docker compose config --quiet`, then `docker compose up --build -d`.
5. Open `http://127.0.0.1:3000`. Inspect `docker compose ps` and health status.

Use `docker-compose` when Compose is installed as a standalone command.
Image manifests are pinned in both Dockerfiles. Python runtime dependencies
are pinned in `requirements-runtime.lock`. The frontend uses `npm ci` and its
lock file. Review and test these pins together when updating dependencies.

The API requires a token when explicitly bound to all interfaces inside its
container. The API accepts localhost Host headers, and the dashboard forwards
the token from its server environment. Workers run under the same unprivileged
UID as the API and share the data volume.

## Import and workers

Paths under the host's `data/` directory appear at `/imports` in a worker.
Pass ordinary CLI arguments after `worker`.

```sh
docker compose run --rm worker import-csv /imports/mgc.csv \
  --calendar /imports/calendar.json --provider AUTHORIZED_SOURCE \
  --contract MGC-2026-12 --source-timezone UTC \
  --timestamp-label open --data-kind historical --price-basis unadjusted
docker compose run --rm worker verify-store --verify-sources --verify-normalized
docker compose run --rm worker monitor-profile PROFILE_ID
docker compose run --rm worker deliver-profile PROFILE_ID --watch
```

Configure provider and channel environment values locally using the variable
names in the saved profile. Compose supplies the standard names shown in
`.env.example`. Use `docker compose run -e NAME worker ...` for a custom name
already set in the shell. Review source authorization and the applicable CME
license before starting a market-data worker.

## Backup and shutdown

```sh
docker compose run --rm worker backup /data/research-backup.sqlite --verify-normalized
docker compose stop
```

Choose a new backup filename for each snapshot. The backup command rejects an
existing output. The named volume survives ordinary container replacement and
shutdown. To restore, stop all writers, retain the existing database as a
separate checkpoint, place the reviewed backup at the configured database path
and run the integrity checks before restarting workers.

## Verification limits

On 2026-11-11, Compose configuration validation passed and the native standalone
dashboard served assets and proxied the token-protected API. The direct API
returned 401 without the token. The image digests were obtained from the
official Docker registry. Container builds and execution remain unverified in
this environment. Starting the available Colima runtime failed while fetching
its VM image from GitHub, and the runtime was stopped.

Service definitions follow the documented health and dependency behavior.
[Compose services](https://docs.docker.com/reference/compose-file/services/) ·
[Next.js standalone deployment](https://nextjs.org/docs/app/getting-started/deploying)
