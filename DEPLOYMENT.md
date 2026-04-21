# Long-term deployment for Linux / Proxmox

The long-term setup for this service is:

1. Build a Docker image in GitHub Actions.
2. Push the image to GHCR.
3. Run the service as a Portainer stack with Docker Compose.
4. Let Watchtower detect the new image and restart the app automatically.

This repo now includes the pieces for that workflow.

## Architecture

- `ftptransfer` runs in a Python container under Gunicorn.
- `redis` runs as a sidecar because the app config includes `Flask-SSE`.
- Your existing Linux host folders stay on the host and are bind-mounted into the container.
- Portainer manages the stack.
- GitHub Actions builds and publishes the image.
- Watchtower automatically updates the app container when GHCR gets a new image.

## Host setup on the Proxmox Linux guest

Install Docker and Portainer on the VM or LXC where this service will live.

Create the working directory:

```bash
sudo mkdir -p /opt/ftptransfer
sudo chown -R $USER:$USER /opt/ftptransfer
cd /opt/ftptransfer
```

Copy these repo files there:

- `docker-compose.yml`
- `.env.example` as a reference only

You can either create a real `.env` file on the server or, more simply, enter the same values in Portainer's stack environment variables UI.

The important setting is the host bind mount:

```bash
MOUNTS_ROOT=/home/actserver/mounts
```

That host folder must contain the same content your app already expects:

- `/home/actserver/mounts/token`
- `/home/actserver/mounts/prelims`
- `/home/actserver/mounts/json/Reports`
- `/home/actserver/mounts/mlp`

## Portainer stack deployment

In Portainer:

1. Go to `Stacks`
2. Create a new stack
3. Paste `docker-compose.yml`
4. Add the environment variables from `.env.example`
5. Deploy the stack

If you prefer CLI first:

```bash
docker compose --env-file .env up -d
```

## GitHub Actions image publishing

The workflow in `.github/workflows/docker-image.yml` builds the image and pushes it to:

```bash
ghcr.io/actresearch/ftptransfer:latest
```

Make sure the GHCR package is readable by your Docker host:

- easiest option: make `ghcr.io/actresearch/ftptransfer` public
- private option: log Portainer into `ghcr.io` with a GitHub token that has `read:packages`

Important: a public GitHub repository does not automatically guarantee anonymous pulls from GHCR. The container package itself must be public, or Portainer / Docker must authenticate to `ghcr.io`.

If Portainer fails with:

```text
Head "https://ghcr.io/v2/actresearch/ftptransfer/manifests/latest": unauthorized
```

check these items in order:

1. Open the package page for `actresearch/ftptransfer` in GitHub Packages and confirm the package visibility is `Public`.
2. Confirm the package actually contains a `latest` tag.
3. If the package is private or org policy blocks anonymous pulls, add a GHCR registry in Portainer or run `docker login ghcr.io` on the Docker host with a classic GitHub PAT that has `read:packages`.

If the error changes to:

```text
manifest unknown
```

that usually means GHCR can be reached, but the requested tag does not exist yet. This repo's workflow should publish `latest` from `main` or `master`, plus branch and SHA tags for troubleshooting.

If you add a Portainer registry, use:

- registry: `ghcr.io`
- username: your GitHub username
- password/token: a classic PAT with at least `read:packages`

## Update strategies

### Recommended: Watchtower automatic updates

- GitHub Actions pushes a new image
- Watchtower checks GHCR every 5 minutes
- If the image changed, Watchtower pulls it and restarts only the labeled app container

This is the most hands-off approach.

Watchtower only updates containers with this label:

`com.centurylinklabs.watchtower.enable=true`

That keeps updates limited to this stack.

### What you need to enter in Portainer

At minimum, add these environment variables in the stack:

- `O365_CLIENT_ID`
- `O365_CLIENT_SECRET`
- `O365_TENANT_ID`
- `MOUNTS_ROOT=/home/actserver/mounts`

Usually you will also want:

- `MAILBOX_USER`
- `COMPLETED_FOLDER`
- `FLASK_PORT=5000`

The path-related variables already have defaults that match your current Linux layout.

## Local development auto-restart

`watchdog_runner.py` is still useful for development only:

```bash
pip install watchdog
python watchdog_runner.py
```

It restarts `app.py` when `.py`, `.txt`, or `.sh` files change.
