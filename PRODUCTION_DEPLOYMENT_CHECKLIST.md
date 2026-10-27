# TrustTrip — Production Deployment Checklist

**Audit Date:** October 27, 2026
**Auditor:** Senior Full-Stack & DevOps Engineer / Antigravity Agent
**Repository:** [TrustTrip_TravelPlanner](https://github.com/Supratik-50/TrustTrip_TravelPlanner)

---

## 1. Executive Summary

This checklist provides a prescriptive, step-by-step operational guide for deploying the TrustTrip platform to an AWS EC2 instance (Ubuntu 24.04 LTS, x86_64) using Docker Compose for application services and a native host `systemd` Cloudflared service for public ingress. It details mandatory pre-flight environment requirements, secret configuration, deployment procedures, database persistence safeguards, and non-destructive service recovery operations.

---

## 2. Pre-Deployment Environment Checklist

- [ ] **1. Compute Instance:** AWS EC2 instance provisioned (recommended: `t3.large` or `c6i.large`, 2 vCPUs, 8 GB RAM, Ubuntu 24.04 LTS x86_64).
- [ ] **2. Persistent Storage:** Amazon EBS GP3 volume attached and formatted for `/var/lib/docker/volumes` to guarantee database volume persistence across EC2 instance recreation.
- [ ] **3. Security Groups:** Inbound public traffic on ports 80, 443, 5432, and 8000 blocked (0 inbound rules from 0.0.0.0/0). Outbound UDP/TCP port 7844 and port 443 open for Cloudflare Tunnel traffic. Port 22 open only to trusted bastion IP or AWS SSM Session Manager.
- [ ] **4. Container Runtime:** Docker Engine v24+ and Docker Compose v2.20+ installed.
- [ ] **5. Host Ingress Connector:** Native `cloudflared` package installed and configured as a host `systemd` service routing to `127.0.0.1:8080`.

---

## 3. Production Secret Management & Configuration Checklist

All production credentials must be placed inside the EC2 `.env` file with restrictive permissions (`chmod 600 .env`):

| Variable | Description | Security Requirements | Status |
| :--- | :--- | :--- | :--- |
| `APP_ENV` | Application runtime environment | Must be set to `production` | **Verified** |
| `JWT_SECRET` | Primary JWT signing secret | Min 32 random characters (`openssl rand -hex 32`) | **Pending Production Injection** |
| `JWT_REFRESH_SECRET` | Refresh token signing secret | Distinct from `JWT_SECRET` | **Pending Production Injection** |
| `ENCRYPTION_KEY` | AES-256 payload encryption key | Fernet-compatible key (`cryptography.fernet`) | **Verified in .env** |
| `ALLOWED_ORIGINS` | CORS & CSRF allowed origins | Exact domain, e.g., `https://planner.yourdomain.com` | **Pending Domain Config** |
| `DATABASE_URL` | Compose PostgreSQL connection | `postgresql://trusttrip:<STRONG_PASSWORD>@postgres:5432/trusttrip` | **Constructed inside Compose** |
| `TUNNEL_TOKEN` | Cloudflare Named Tunnel token | Stored in `/etc/cloudflared/tunnel-token` (`chmod 600`) or credentials JSON | **Pending Cloudflare Token** |
| `GROQ_API_KEY` | Primary LLM inference key | High-throughput production quota key | **Present in .env** |
| `TAVILY_API_KEY` | Web research RAG search key | Production quota key | **Present in .env** |
| `LITEAPI_SANDBOX_API_KEY` | Hotel search sandbox key | Validated for development/staging | **Present in .env** |
| `LITEAPI_PRODUCTION_API_KEY` | Hotel search production key | Optional until hotel bookings go live | **Pending Live Key** |
| `DUFFEL_API_TOKEN` | Flight booking provider key | **Leave empty / disabled** for initial release | **Disabled (Fail-Closed Verified)** |

---

## 4. Authoritative Database Migration & Backup Procedure

### Pre-Deployment Backup
Before updating container images or executing database changes on the production host:
```bash
# 1. Create backup directory
mkdir -p ./backups

# 2. Export database dump in PostgreSQL custom format (-Fc)
docker compose exec postgres pg_dump -U trusttrip -d trusttrip -Fc > ./backups/trusttrip_backup_$(date +%Y%m%d_%H%M%S).dump

# 3. Verify backup file existence and non-zero size
ls -lh ./backups/
```

### Backup Archive Catalog Validation
This command reads the archive's table of contents and checks whether the catalog can be listed. It does not restore the database or prove that a complete restoration will succeed.

```bash
# Host-side check (preferred when pg_restore is available on host):
pg_restore --list ./backups/trusttrip_backup_<TIMESTAMP>.dump > /dev/null
echo "Exit status: $?"

# Container-based stream check (when host pg_restore is unavailable):
cat ./backups/trusttrip_backup_<TIMESTAMP>.dump | docker compose exec -T postgres pg_restore --list > /dev/null
echo "Exit status: $?"
```

### Production Host Restoration (Disaster Recovery Only)
> [!CAUTION]
> Restoring a database drops and recreates existing tables. Follow all safeguards in Section 6.C of the deployment guide.

```bash
# 1. Stop public ingress and application containers
sudo systemctl stop cloudflared
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop web backend

# 2. Restore custom dump into volume
docker compose exec -T postgres pg_restore -U trusttrip -d trusttrip --clean --if-exists < ./backups/trusttrip_backup_<TIMESTAMP>.dump

# 3. Restart application containers and public ingress
docker compose -f docker-compose.yml -f docker-compose.prod.yml start backend web
sudo systemctl start cloudflared
```

---

## 5. Deployment Execution Steps

```bash
# 1. Clone repository to EC2
git clone https://github.com/Supratik-50/TrustTrip_TravelPlanner.git /opt/trusttrip
cd /opt/trusttrip

# 2. Secure .env configuration
cp .env.example .env
chmod 600 .env
nano .env  # Populate production JWT_SECRET, ALLOWED_ORIGINS, and database credentials

# 3. Validate merged production Compose configuration (non-mutating, in-memory)
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --format json | python3 -c '
import sys, json
c = json.load(sys.stdin)
services = c.get("services", {})
assert not services.get("postgres", {}).get("ports"), "Postgres ports exposed"
assert any("127.0.0.1:8080:80" in f"{p.get(\"host_ip\",\"0.0.0.0\")}:{p.get(\"published\")}:{p.get(\"target\")}" for p in services.get("web", {}).get("ports", [])), "Web not on 127.0.0.1:8080"
assert services.get("cloudflared", {}).get("scale", 1) == 0, "Cloudflared not scaled to 0"
print("Configuration verified.")
'

# 4. Build production images
docker compose -f docker-compose.yml -f docker-compose.prod.yml build

# 5. Launch PostgreSQL and verify health
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres
sleep 5
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps postgres

# 6. Launch Backend (single worker verified) and Nginx
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d backend web

# 7. Verify health probes on local loopback port 8080
curl -f http://127.0.0.1:8080/nginx-health
curl -f http://127.0.0.1:8080/ready
curl -f http://127.0.0.1:8080/health

# 8. Start native host Cloudflare Tunnel service
sudo systemctl start cloudflared

# 9. Inspect tunnel status to confirm edge route established
sudo systemctl status cloudflared
sudo journalctl -u cloudflared -n 50 --no-pager
```

---

## 6. Service Recovery Procedure

If a failure occurs during production startup or runtime:
```bash
# 1. Stop public ingress and application containers (DO NOT REMOVE VOLUMES)
sudo systemctl stop cloudflared
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop web backend

# 2. Check logs for error diagnostics
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs backend
sudo journalctl -u cloudflared -n 50 --no-pager

# 3. Correct configuration or environment settings in .env
# (Ensure chmod 600 .env is maintained)

# 4. Restart application containers
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d backend web

# 5. Verify local health probes
curl -f http://127.0.0.1:8080/ready

# 6. Resume public ingress
sudo systemctl start cloudflared
```

*(Note: For fatal database corruption requiring backup restoration, follow the disaster recovery instructions in Section 4).*

---

## 7. Verified Facts

- Concurrency safely locked to single worker (`--workers 1`).
- Database volume `trusttrip_pgdata` preserves schema and data across container restarts.
- Live flight booking safely fails closed when `DUFFEL_API_TOKEN` is unconfigured.
- Cloudflare Tunnel runs as a native host `systemd` service routing public traffic to `127.0.0.1:8080`.
- In production, PostgreSQL host ports are completely removed (`ports: !reset []`) and Nginx binds exclusively to loopback port `127.0.0.1:8080:80`.
- Containerized `cloudflared` is disabled in production (`scale: 0`), preventing duplicate tunnel instances.

---

## 8. Inferences

- EC2 instance type with at least 8 GB RAM (`t3.large` or `c6i.large`) is recommended to support Python PyTorch / sentence-transformers model loading during startup without triggering the Linux Out-Of-Memory (OOM) killer.

---

## 9. Failed Checks

- **None.**

---

## 10. Checks Not Performed

- Live deployment on AWS hardware (prohibited by instructions; instructions are fully prepared).
- Production Cloudflare Tunnel activation and external DNS routing (awaiting infrastructure provisioning).

---

## 11. Changes Made

- Created `docker-compose.prod.yml` with `ports: !reset []` for PostgreSQL, `127.0.0.1:8080:80` for Nginx, and `scale: 0` for containerized `cloudflared`.
- Updated deployment documentation and checklist to reflect Option B architecture with native host Cloudflared systemd service.
- Documented backup archive catalog validation without truncating pipelines.
- Renamed rollback procedure to non-destructive Service Recovery.

---

## 12. Remaining Blockers

- Provisioning of EC2 virtual machine (Ubuntu 24.04 LTS x86_64) and Cloudflare Tunnel credentials.

---

## 13. Recommended Next Steps

- Execute deployment following the steps in Section 5 once AWS and Cloudflare infrastructure is provisioned.
