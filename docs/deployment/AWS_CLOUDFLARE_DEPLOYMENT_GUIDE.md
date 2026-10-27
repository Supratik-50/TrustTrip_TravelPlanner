# TrustTrip — AWS + Docker + Cloudflare Tunnel Deployment Guide

## 1. Executive Architecture Summary

### Production Target: **Docker Compose on Amazon EC2**

After evaluating TrustTrip's multi-agent supervisor pipeline, PostgreSQL relational storage, `pgvector` embedding indexes, and session security model, **Docker Compose on an Amazon EC2 instance connected to a Cloudflare Named Tunnel** is the authoritative production deployment architecture.

```text
+-----------------------------------------------------------------------------------------+
|                                    CLOUDFLARE EDGE                                      |
|    - Global Anycast CDN & DDoS Mitigation                                               |
|    - Automatic TLS Termination (HTTPS on user's custom domain)                          |
|    - Cloudflare Named Tunnel Routing                                                    |
+--------------------------------------------+--------------------------------------------+
                                             | Outbound TLS (Port 7844 / 443 only)
                                             v
+-----------------------------------------------------------------------------------------+
|                                  AWS EC2 INSTANCE                                       |
|  Security Group: ZERO INBOUND PORTS OPEN FROM INTERNET (0.0.0.0/0)                      |
|  Outbound: Port 443 / 7844 to Cloudflare Edge                                           |
|                                                                                         |
|  +-------------------------------- Docker Network (trusttrip-net) -------------------+  |
|  |                                                                                   |  |
|  |  +-----------------------+     Internal     +----------------------------------+  |  |
|  |  |      cloudflared      | ---------------> |           web (Nginx)            |  |  |
|  |  | (Cloudflare Connector)|    HTTP Port 80  | - Serves compiled React assets   |  |  |
|  |  +-----------------------+                  | - Reverse proxies /api/ & /ready |  |  |
|  |                                             +----------------+-----------------+  |  |
|  |                                                              |                    |  |
|  |                                                              | HTTP Port 8000     |  |
|  |                                                              v                    |  |
|  |                                             +----------------+-----------------+  |  |
|  |                                             |         backend (FastAPI)        |  |  |
|  |                                             | - LangGraph Multi-Agent Planner  |  |  |
|  |                                             | - Auth, Revisions, HITL, Search  |  |  |
|  |                                             | - 1 Worker (--workers 1)         |  |  |
|  |                                             +----------------+-----------------+  |  |
|  |                                                              |                    |  |
|  |                                                              | TCP Port 5432      |  |
|  |                                                              v                    |  |
|  |                                             +----------------+-----------------+  |  |
|  |                                             |    postgres (16 + pgvector)      |  |  |
|  |                                             | - Persistent pgdata volume       |  |  |
|  |                                             | - Schema migrations (004-016)    |  |  |
|  |                                             +----------------------------------+  |  |
|  |                                                                                   |  |
|  +-----------------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------------+
```

### Core Architectural Principles
1. **Zero Public Ingress Attack Surface:** With Cloudflare Tunnel (`cloudflared`), the EC2 Security Group requires **0 inbound rules from the internet** (no open port 80, 443, 8000, or 5432). The `cloudflared` container initiates outbound encrypted connections to Cloudflare Edge.
2. **Same-Origin API Routing:** The `web` container serves the compiled React SPA and internally reverse-proxies `/api/`, `/health`, and `/ready` to `backend:8000`. The browser client only makes same-origin requests (`/api/...`), eliminating third-party cookie blocking and cross-origin complexity.
3. **Unbuffered Streaming:** Nginx explicitly disables reverse-proxy buffering (`proxy_buffering off;`) for `/api/`, allowing low-latency streaming of multi-agent LLM reasoning to the client.
4. **Single-Worker Concurrency Lock:** The FastAPI backend runs with a single Uvicorn worker (`--workers 1`) to guarantee deterministic in-memory session protection, prevent database migration race conditions, and preserve thread-level lock integrity.
5. **Operational Parity:** Docker Compose ([docker-compose.yml](../../docker-compose.yml)) provides exact local-to-cloud fidelity between development and production environments.

> [!IMPORTANT]
> **Implementation Status Note:** This document represents a validated deployment design, operational checklist, and disaster recovery specification. While container builds, reverse-proxy routing, and migration lifecycles have been verified in local Docker testing, actual cloud deployment to AWS EC2 hardware has **not** yet been performed.

---

## 2. Infrastructure Sizing & Specifications (Estimates)

The specifications below are **baseline estimates** derived from local development profiling (FastAPI, LangGraph supervisor, sentence-transformers, PyTorch runtime dependencies, and PostgreSQL buffer caches). Sizing should be monitored and adjusted based on actual concurrent traffic and database volume:

| Component | Recommended Specification (Estimated) | Operational Rationale |
| :--- | :--- | :--- |
| **EC2 Instance Type** | `t3.large` (2 vCPU, 8 GiB RAM) or `c6i.large` | 8 GiB RAM ensures Python sentence-transformers, PyTorch dependencies, and PostgreSQL buffer caches run stably without risking Linux Out-Of-Memory (OOM) termination. (`t3.medium` with 4 GiB is acceptable for minimal evaluation). |
| **Operating System** | Ubuntu 24.04 LTS or Amazon Linux 2023 | 64-bit x86_64 architecture (or ARM64 Graviton `t4g.large`). |
| **EBS Storage** | 30–50 GiB gp3 (3000 IOPS, 125 MB/s) | Estimated capacity for OS, Docker image layers, PostgreSQL relational and vector data, and local pre-deployment backups. |
| **Network Egress** | Standard internet gateway | Outbound HTTPS (port 443) and QUIC/HTTP2 (port 7844) to Cloudflare Edge, plus outbound HTTPS to external APIs (Groq, LiteAPI, Tavily). |
| **Inbound Firewall** | **Zero open inbound ports from 0.0.0.0/0** | SSH (port 22) restricted to trusted bastion IP or AWS Systems Manager (SSM) Session Manager. |

---

## 3. Container Services Inventory

The production stack consists of 4 interconnected services defined in [docker-compose.yml](../../docker-compose.yml):

1. **`postgres` (`pgvector/pgvector:pg16`):**
   - **Role:** Primary relational and vector database.
   - **Internal Network:** Reachable only via internal Docker network `trusttrip-net:5432`.
   - **Persistence:** Named volume `trusttrip_pgdata` (mapped to `trusttrip_travelplanner_trusttrip_pgdata`).
   - **Healthcheck:** `pg_isready -U ${POSTGRES_USER:-trusttrip} -d ${POSTGRES_DB:-trusttrip}`.
2. **`backend` (FastAPI / Python 3.11):**
   - **Role:** LangGraph multi-agent supervisor, authentication, revision management, and provider adapters.
   - **Execution:** `uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1 --lifespan on`.
   - **Security:** Non-root execution user `appuser` (UID 1001).
   - **Migrations:** Executes `services/migration_runner.py` on startup, applying migrations `004` through `016`.
   - **Healthcheck:** `curl -f http://localhost:8000/ready`.
3. **`web` (Nginx 1.27 Alpine + Compiled React SPA):**
   - **Role:** High-performance static web server and reverse proxy.
   - **Assets:** Serves compiled Vite assets from `/usr/share/nginx/html`.
   - **Proxying:** Forwards `/api/`, `/health`, and `/ready` to `http://backend:8000` with `proxy_buffering off;`.
   - **Healthcheck:** `wget -qO- http://localhost:80/nginx-health`.
4. **`cloudflared` (`cloudflare/cloudflared:latest`):**
   - **Role:** Cloudflare Named Tunnel connector.
   - **Command:** `tunnel --no-autoupdate run --token ${TUNNEL_TOKEN}`.
   - **Routing:** Directs external traffic to `http://web:80` inside `trusttrip-net`.

---

## 4. Pre-Deployment Configuration & Setup

### Step A: Configure Cloudflare Zero Trust Named Tunnel
1. Log in to the [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/).
2. Navigate to **Networks** $\to$ **Tunnels**.
3. Click **Add a tunnel** and select **Cloudflare (recommended)**.
4. Name the tunnel (e.g. `trusttrip-prod`).
5. Under the **Public Hostnames** tab, add a route:
   - **Subdomain / Domain:** Your desired public domain (e.g. `planner.yourdomain.com`).
   - **Type:** `HTTP`
   - **URL:** `web:80` *(inside Docker Compose, this resolves to the Nginx service)*.
6. Copy the generated **Tunnel Token** (base64 string). Store it securely; do not commit this token.

### Step B: Provision AWS EC2 Instance & Security Group
1. Launch an EC2 instance:
   - **AMI:** Ubuntu 24.04 LTS (64-bit x86).
   - **Instance Type:** `t3.large`.
   - **Storage:** 40 GiB gp3.
2. **Configure Security Group Inbound Rules:**
   - **HTTP (Port 80):** **None** (Do NOT open to 0.0.0.0/0).
   - **HTTPS (Port 443):** **None** (Do NOT open to 0.0.0.0/0).
   - **PostgreSQL (Port 5432):** **None** (Do NOT open to 0.0.0.0/0).
   - **FastAPI (Port 8000):** **None** (Do NOT open to 0.0.0.0/0).
   - **SSH (Port 22):** Restrict to your specific administrative IP address, or use AWS Systems Manager (SSM) Session Manager for zero-inbound SSH access.
3. **Configure Security Group Outbound Rules:**
   - Outbound HTTPS (Port 443) and QUIC/HTTP2 (Port 7844) open to allow outbound tunnel connectivity.

### Step C: Install Docker Engine & Compose on EC2
Run the following commands on the EC2 instance:
```bash
# Update and install system prerequisites
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

# Add Docker official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Set up the Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine, CLI, and Docker Compose plugin
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable docker group for ubuntu user
sudo usermod -aG docker $USER
```
*(Log out and log back in for group changes to take effect).*

---

## 5. Production Environment Configuration (`.env`)

Clone the repository to the EC2 instance and create a secure `.env` file:
```bash
git clone https://github.com/Supratik-50/TrustTrip_TravelPlanner.git /opt/trusttrip
cd /opt/trusttrip

# Create .env from template
cp .env.example .env

# Enforce strict file permissions (readable only by owner)
chmod 600 .env
```

### Production Variable Reference

| Variable | Required? | Example / Generation | Purpose |
| :--- | :---: | :--- | :--- |
| `APP_ENV` | **Yes** | `production` | Enables strict validation and security enforcement. |
| `LOG_LEVEL` | **Yes** | `INFO` | Standard production logging verbosity. |
| `JWT_SECRET` | **Yes** | 32+ char cryptographically secure string | Signs session tokens. Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `PAYLOAD_ENCRYPTION_KEY` | **Yes** | 32-byte base64 encoded string | Encrypts search sessions. Generate: `python3 -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"` |
| `ALLOWED_ORIGINS` | **Yes** | `https://planner.yourdomain.com` | Exact Cloudflare public hostname without wildcards (`*`). |
| `FRONTEND_URL` | **Yes** | `https://planner.yourdomain.com` | Base URL used by backend links and redirects. |
| `POSTGRES_DB` | **Yes** | `trusttrip` | Database name initialized in PostgreSQL container. |
| `POSTGRES_USER` | **Yes** | `trusttrip` | Database administrative user in PostgreSQL container. |
| `POSTGRES_PASSWORD` | **Yes** | `<SECURE_DB_PASSWORD>` | Database password. `docker-compose.yml` automatically constructs the internal backend `DATABASE_URL` from these credentials. |
| `DATABASE_URL` | Optional | `postgresql://...` | Used for host-based development; overridden inside Compose by container service DNS (`postgres:5432`). |
| `TUNNEL_TOKEN` | **Yes** | `<CLOUDFLARE_NAMED_TUNNEL_TOKEN>` | Injected token connecting `cloudflared` to Cloudflare Edge. |
| `LLM_PROVIDER` | **Yes** | `groq` | Primary LLM inference provider. |
| `GROQ_API_KEY` | **Yes** | `<YOUR_GROQ_API_KEY>` | API key for Groq Cloud. |
| `GROQ_MODEL` | Optional | `openai/gpt-oss-120b` | Primary high-parameter planning model. |
| `GROQ_FALLBACK_MODEL` | Optional | `qwen/qwen3.8-27b` | Secondary fallback model. |
| `GEMINI_API_KEY` | Optional | `<YOUR_GEMINI_API_KEY>` | Secondary fallback provider key. |
| `TAVILY_API_KEY` | Optional | `<YOUR_TAVILY_API_KEY>` | Destination research and RAG search key. |
| `LITEAPI_SANDBOX_API_KEY` | Optional | `<YOUR_LITEAPI_KEY>` | LiteAPI Nuitee hotel integration key. |
| `LITEAPI_MODE` | Optional | `sandbox` | `sandbox` or `production`. |
| `DUFFEL_API_TOKEN` | Optional | *(Leave empty for launch)* | Flight provider token. Leave empty to maintain safe fail-closed behavior. |
| `DUFFEL_MODE` | Optional | `test` | Duffel provider mode (`test` or `live`). |
| `RAZORPAY_KEY_ID` | Optional | `<YOUR_RAZORPAY_KEY_ID>` | Razorpay merchant key ID for INR payments. |
| `RAZORPAY_KEY_SECRET` | Optional | `<YOUR_RAZORPAY_KEY_SECRET>` | Razorpay secret key for signature verification. |

---

## 6. Authoritative Database Backup & Recovery Procedure

### A. Pre-Deployment Backup
Before updating container images or executing database changes on the host:
```bash
# 1. Create a local backup directory
mkdir -p ./backups

# 2. Export database dump in PostgreSQL custom format (-Fc)
docker compose exec postgres pg_dump -U trusttrip -d trusttrip -Fc > ./backups/trusttrip_backup_$(date +%Y%m%d_%H%M%S).dump

# 3. Verify backup file existence and non-zero size
ls -lh ./backups/
```

### B. Backup Verification
Verify the custom-format archive integrity without touching the database:
```bash
docker compose exec -T postgres pg_restore --list < ./backups/trusttrip_backup_<TIMESTAMP>.dump | head -n 25
```

### C. Database Disaster Recovery / Restoration

> [!CAUTION]
> **DESTRUCTIVE RECOVERY OPERATION:**
> Running `pg_restore` with `--clean --if-exists` is a **destructive operation** that drops and replaces existing tables in the target database.
>
> **Mandatory Pre-Restoration Safeguards:**
> 1. **Confirm Target Identity:** Verify that the active container is `trusttrip-postgres` and the database is `trusttrip`. Never run this against an unverified container.
> 2. **Verify Archive Integrity First:** Always execute Section 6.B (`pg_restore --list`) to confirm the backup file is non-empty, readable, and contains valid table definitions.
> 3. **Create Pre-Restoration Safety Snapshot:** Before executing any restore, take a safety backup of the database's current state:
>    ```bash
>    docker compose exec postgres pg_dump -U trusttrip -d trusttrip -Fc > ./backups/pre_restore_safety_$(date +%Y%m%d_%H%M%S).dump
>    ```
> 4. **Obtain Operator Sign-Off:** Ensure administrative approval before proceeding with table drops.
> 5. **Untested Restoration Notice:** While backup creation (`pg_dump -Fc`) and archive listing (`pg_restore --list`) have been verified, actual destructive restoration over the authoritative database has **not** been executed during this review to preserve existing records.

To restore a verified custom-format dump into the PostgreSQL container:
```bash
# 1. Stop application containers to terminate active database sessions
docker compose stop web backend cloudflared

# 2. Execute pg_restore with clean and if-exists flags
docker compose exec -T postgres pg_restore -U trusttrip -d trusttrip --clean --if-exists < ./backups/trusttrip_backup_<TIMESTAMP>.dump

# 3. Restart application containers
docker compose start backend web cloudflared
```

---

## 7. Deployment Execution Workflow

> [!NOTE]
> **Operational Boundary:**
> - **Tested Locally:** Steps 1 through 6 (container builds, database startup, migration runner execution, Nginx reverse proxying, and local health probes) have been verified in the local Docker environment.
> - **Untested Cloud Target:** Steps 7 and 8 (registering `cloudflared` to a live Cloudflare Edge tunnel and routing public traffic) and AWS EC2 hardware provisioning represent planned deployment procedures that have not yet been executed.

Execute the deployment sequence step by step:

```bash
# Step 1: Navigate to repository
cd /opt/trusttrip

# Step 2: Build production container images (uses layer caching)
docker compose build

# (Optional: Use 'docker compose build --no-cache' only when troubleshooting stale dependency layers)

# Step 3: Launch PostgreSQL database service first
docker compose up -d postgres

# Step 4: Verify database container health (wait for 'healthy' status)
docker compose ps postgres

# Step 5: Launch Backend and Web reverse proxy
docker compose up -d backend web

# Step 6: Verify Backend readiness and database migration status
docker compose ps
docker compose logs backend | grep -i "Application ready"

# Step 7: Launch Cloudflare Tunnel connector
docker compose up -d cloudflared

# Step 8: Verify Tunnel connectivity
docker compose logs -f cloudflared
# Look for: "Connection ... registered" and "Registered tunnel connection"
```

---

## 8. Verification & Health Probes

Run the verification probes directly from the EC2 host:

```bash
# 1. Verify Nginx reverse proxy health probe
curl -f http://127.0.0.1/nginx-health
# Expected Output: healthy

# 2. Verify Backend readiness probe (probes PostgreSQL SELECT 1)
curl -f http://127.0.0.1/ready
# Expected Output: {"status":"ready"}

# 3. Verify Backend process liveness probe
curl -f http://127.0.0.1/health
# Expected Output: {"status":"ok","version":"2.0.0",...}

# 4. Verify Public HTTPS Ingress (from an external machine)
curl -I https://planner.yourdomain.com/nginx-health
# Expected Output: HTTP/2 200
```

---

## 9. Rollback Procedure

If startup fails or regression defects are detected:

```bash
# Step 1: Stop application ingress
docker compose stop web backend cloudflared

# Step 2: Inspect error logs
docker compose logs backend

# Step 3: Revert to previous stable Git release commit
git checkout <PREVIOUS_STABLE_COMMIT>

# Step 4: Rebuild and start application services
docker compose build backend web
docker compose up -d backend web cloudflared

# Step 5: If database schema rollback is required, restore pre-deployment backup
# (Follow Section 6.C Disaster Recovery)
```

---

## 10. Pre-Flight Release Gate Checklist

Before declaring the deployment live, verify every item below:

- [ ] **Infrastructure:** EC2 instance has **0 inbound rules** from `0.0.0.0/0` (ports 80, 443, 5432, 8000 closed to the internet).
- [ ] **Cloudflare Tunnel:** Named tunnel is active and maps public hostname to `HTTP` service `web:80`.
- [ ] **Environment Security:** `APP_ENV=production` is set; `.env` permissions are `chmod 600`.
- [ ] **Key Length:** `JWT_SECRET` is at least 32 cryptographically secure characters.
- [ ] **Payload Encryption:** `PAYLOAD_ENCRYPTION_KEY` is a valid 32-byte base64 encoded key.
- [ ] **CORS & CSRF:** `ALLOWED_ORIGINS` matches the exact public Cloudflare hostname with **no wildcard (`*`)**.
- [ ] **Database Integrity:** Named volume `trusttrip_pgdata` is mapped and pre-deployment backup is created.
- [ ] **Single Worker:** Backend Docker container is confirmed running `--workers 1`.
- [ ] **Fail-Closed Booking:** Live flight booking remains disabled (`DUFFEL_API_TOKEN` unset or in `test` mode).
- [ ] **Health Probes:** `/nginx-health`, `/ready`, and `/health` return HTTP 200.
