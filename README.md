# Welcome to the Dungeonware Homelab

This is my personal homelab project to help maintain and access al of my media.

## Features:
- Runs on **Raspberry Pi 4**
- All servies run in a **Kubernetes** environment.
- Deployments and configuration managed by **Flux GitOps**.
- **SOPS + Age** encryption and secret management.
- Services networked and exposed securely through **Cloudflare Tunnels**
- **Visibilility Dashboard** for easy access and monitoring.

![Homelab Dashboard](dashboard.png)

## Repository Structure
- `infrastructure/` → cluster configuration & manifests
- `apps/` → deployed services
- `flux/` → GitOps setup

