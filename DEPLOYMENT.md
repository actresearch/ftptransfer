# Long-term deployment for Linux / Proxmox

The long-term setup for this service is:

1. Build a Docker image in GitHub Actions.
2. Push the image to GHCR.
3. Run the service as a Portainer stack with Docker Compose.
4. Update via Portainer redeploys or Watchtower.

This repo now includes the pieces for that workflow.

## Architecture

- `ftptransfer` runs in a Python container under Gunicorn.
- `redis` runs as a sidecar because the app config includes `Flask-SSE`.
- Your existing Linux host folders stay on the host and are bind-mounted into the container.
- Portainer manages the stack.
- GitHub Actions builds and publishes the image.

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
- `.env.example` copied to `.env`

Create `.env` and set the real values:

```bash
cp .env.example .env
```

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
4. Upload or recreate the `.env` values
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

Add these repository secrets if you want Portainer to redeploy automatically after a push:

- `PORTAINER_WEBHOOK_URL`

If that secret is not present, the image still builds and pushes successfully, and you can redeploy from Portainer manually.

## Update strategies

### Recommended: controlled redeploy from Portainer

- GitHub Actions pushes a new image
- Portainer pulls and redeploys when you trigger it

This is the safest production approach.

### Optional: Watchtower

The compose file includes an optional `watchtower` service under the `ops` profile.

Run it only if you want automatic image-based updates:

```bash
docker compose --profile ops up -d
```

Watchtower only updates containers with this label:

```bash
com.centurylinklabs.watchtower.enable=true
```

That keeps updates limited to this stack.

## Local development auto-restart

`watchdog_runner.py` is still useful for development only:

```bash
pip install watchdog
python watchdog_runner.py
```

It restarts `app.py` when `.py`, `.txt`, or `.sh` files change.
