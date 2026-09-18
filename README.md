# FTP transfer service

This repository contains the Flask transfer service (`app.py`) and supporting transfer scripts. Its Linux deployment uses Gunicorn, Redis for Flask-SSE, a Portainer Compose stack, a GHCR image built by GitHub Actions, and Watchtower updates.

Read [DEPLOYMENT.md](DEPLOYMENT.md) for host mounts, configuration, and deployment steps. `docker-compose.yml` describes the running stack; `.env.example` lists configuration names. Keep real tokens and mount credentials outside Git. `README.Docker.md` is the original generic Docker starter document and is not the service runbook.

When checking an incident, inspect transfer status as well as process health. A successful health endpoint means the service is responding; it does not prove that a transfer completed. A later successful transfer should supersede an older error in the displayed status.
