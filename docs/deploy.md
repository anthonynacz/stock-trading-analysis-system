# Deploying without a laptop

A push to `main` runs `.github/workflows/deploy.yml`. The workflow opens one SSH connection to the server
with a key that can do a single thing: run `scripts/deploy.sh`, which pulls `main` and rebuilds the services
whose code changed. No session (local or cloud) needs an SSH key of its own: it only pushes.

Until the four secrets below exist, the workflow passes without deploying anything.

## One-time setup (about 5 minutes)

**1. Make a key pair for deploys only** (on any machine; do not reuse a personal key):

```bash
ssh-keygen -t ed25519 -N "" -C "github-actions-deploy" -f vela_deploy
```

This writes `vela_deploy` (private) and `vela_deploy.pub` (public).

**2. Allow that key on the server, limited to the deploy script.** Add one line to
`/root/.ssh/authorized_keys` on the server: the options below, a space, then the full content of
`vela_deploy.pub`.

```text
command="/root/edgeflow/scripts/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... github-actions-deploy
```

With `command=...` the key cannot open a shell or run anything else: whatever the caller sends is passed to
the script as a request name and checked against a fixed list.

**3. Record the server's host key** (so the workflow refuses to talk to an impostor):

```bash
ssh-keyscan -t ed25519 <server address>
```

Compare the fingerprint with the one your own SSH client already trusts before using the output.

**4. Add four repository secrets** (GitHub → the repo → Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | the server address |
| `DEPLOY_USER` | `root` |
| `DEPLOY_SSH_KEY` | the whole content of the private file `vela_deploy` |
| `DEPLOY_KNOWN_HOSTS` | the line printed by step 3 |

**5. Delete the local key files** once the secret is saved, then test: GitHub → Actions → Deploy →
Run workflow → `status`. It prints the deployed commit and the containers and changes nothing.

## Using it

- **Normal case:** push to `main`. The run log shows what was rebuilt and ends with `deploy: done, <commit> is live`.
- **By hand from GitHub:** Actions → Deploy → Run workflow, and pick `auto`, `all`, `backend`, `radar`,
  `frontend` or `status`.
- **By hand on the server:** `scripts/deploy.sh [request]`.

What `auto` rebuilds: `backend/` changes → `backend` and `radar` (one image); `frontend/` changes →
`frontend`; `docker-compose.yml` → all three; anything else, and any `.md` file wherever it lives → nothing.

## When it fails

- **"untracked working tree files would be overwritten":** a stray file sits where a new tracked file
  should go (typically an empty stub left by `docker compose run -v <file>:/app/...`). Delete it on the
  server and run the workflow again.
- **"the backend does not answer":** the containers were rebuilt but the API is not up after 3 minutes.
  `docker compose logs --tail=200 backend` on the server shows why (a failed migration is the usual cause).
- **Deploys during market hours** restart the `radar` worker. It resumes from its stored state; the scan
  in progress is lost and the next one runs on schedule.

## Cloud sessions

A Claude Code cloud session on this repo needs, in its environment:

- **Setup:** `pip install -r backend/requirements.txt` and `npm ci --prefix frontend`. The requirements
  include `torch` and `transformers` (FinBERT), which are large; the radar code and its tests do not need them.
- **Checks:** `cd backend && python -m pytest tests/radar -q` (about 5 minutes) and
  `npx tsc --noEmit -p frontend`.
- **Secrets:** only what the task needs (for example `FMP_API_KEY` for live data experiments), stored as
  environment secrets. Nothing secret belongs in the repo: it is public.
- **No server access:** deploy by pushing to `main`; read production through the app's own API.
