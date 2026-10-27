# TrustTrip — AWS + Docker + Cloudflare Tunnel Deployment Guide

## 1. Executive Architecture Summary

### Production Target: **Docker Compose on Amazon EC2 with Native Host Cloudflared**

After evaluating TrustTrip's multi-agent supervisor pipeline, PostgreSQL relational storage, `pgvector` embedding indexes, and session security model, **Docker Compose on an Amazon EC2 instance (Ubuntu 24.04 LTS, x86_64) connected to a native host Cloudflared systemd service** is the authoritative production deployment architecture (Option B).

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
|  [Native Host Systemd Service]                                                          |
|  cloudflared (/usr/bin/cloudflared)                                                     |
|       |                                                                                 |
|       | HTTP (127.0.0.1:8080)                                                           |
|       v                                                                                 |
|  +-------------------------------- Docker Network (trusttrip-net) -------------------+  |
|  |                                                                                   |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  |  |                                web (Nginx)                                  |  |  |
|  |  | - Bound exclusively to 127.0.0.1:8080 (via docker-compose.prod.yml)          |  |  |
|  |  | - Serves compiled React assets                                              |  |  |
|  |  | - Reverse proxies /api/, /ready, /health to backend                         |  |  |
|  |  +--------------------------------------+--------------------------------------+  |  |
|  |                                         |                                         |  |
|  |                                         | HTTP Port 8000                          |  |
|  |                                         v                                         |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  |  |                              backend (FastAPI)                              |  |  |
|  |  | - LangGraph Multi-Agent Planner                                             |  |  |
|  |  | - Auth, Revisions, HITL, Search                                             |  |  |
|  |  | - 1 Worker (--workers 1)                                                    |  |  |
|  |  +--------------------------------------+--------------------------------------+  |  |
|  |                                         |                                         |  |
|  |                                         | TCP Port 5432 (Internal network only)   |  |
|  |                                         v                                         |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  |  |                          postgres (16 + pgvector)                           |  |  |
|  |  | - Persistent pgdata volume (no published host ports)                        |  |  |
|  |  | - Schema migrations (004-016)                                               |  |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  |                                                                                   |  |
|  +-----------------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------------+
```

### Core Architectural Principles
1. **Zero Public Ingress Attack Surface:** Cloudflared runs directly on the EC2 host as a `systemd` daemon, establishing an outbound TLS/QUIC connection to Cloudflare Edge. The EC2 Security Group requires **0 inbound rules from the internet** (ports 80, 443, 5432, and 8000 are not exposed).
2. **Loopback Ingress Binding:** In production, Nginx binds exclusively to the host loopback interface at `127.0.0.1:8080` via `docker-compose.prod.yml` (`ports: !override`). Public clients reach Nginx only through Cloudflare Tunnel.
3. **Database Network Isolation:** PostgreSQL published host ports are completely removed in production (`ports: !reset []`), leaving PostgreSQL reachable exclusively by the `backend` container across the internal Docker bridge network `trusttrip-net`.
4. **Single-Worker Concurrency Lock:** The FastAPI backend runs with a single Uvicorn worker (`--workers 1`) to guarantee deterministic in-memory session protection, prevent database migration race conditions, and preserve thread-level lock integrity.
5. **Development Environment Preservation:** The base [docker-compose.yml](../../docker-compose.yml) is preserved without modification so local development workflows remain unchanged. Production overrides are applied cleanly via `docker compose -f docker-compose.yml -f docker-compose.prod.yml`.

> [!IMPORTANT]
> **Implementation Status Note:** This document represents a validated production deployment design, operational checklist, and disaster recovery specification. While container builds, reverse-proxy routing, and migration lifecycles have been verified in local Docker testing, actual cloud deployment to AWS EC2 hardware has **not** yet been performed.

---

## 2. Infrastructure Sizing & Specifications (Estimates)

The specifications below are **baseline estimates** derived from local development profiling (FastAPI, LangGraph supervisor, sentence-transformers, PyTorch runtime dependencies, and PostgreSQL buffer caches). Sizing should be monitored and adjusted based on actual concurrent traffic and database volume:

| Component | Recommended Specification (Estimated) | Operational Rationale |
| :--- | :--- | :--- |
| **EC2 Instance Type** | `t3.large` (2 vCPU, 8 GiB RAM) or `c6i.large` | 8 GiB RAM ensures Python sentence-transformers, PyTorch dependencies, and PostgreSQL buffer caches run stably without risking Linux Out-Of-Memory (OOM) termination. (`t3.medium` with 4 GiB is acceptable for minimal evaluation). |
| **Operating System** | Ubuntu 24.04 LTS | 64-bit x86_64 architecture (verified package baseline). |
| **EBS Storage** | 30–50 GiB gp3 (3000 IOPS, 125 MB/s) | Estimated capacity for OS, Docker image layers, PostgreSQL relational and vector data, and local pre-deployment backups. |
| **Network Egress** | Standard internet gateway | Outbound HTTPS (port 443) and QUIC/HTTP2 (port 7844) to Cloudflare Edge, plus outbound HTTPS to external APIs (Groq, LiteAPI, Tavily). |
| **Inbound Firewall** | **Zero open inbound ports from 0.0.0.0/0** | SSH (port 22) restricted to trusted bastion IP or AWS Systems Manager (SSM) Session Manager. |

---

## 3. Production Services Inventory

The production architecture separates application containers from the host ingress connector:

### A. Docker Compose Services ([docker-compose.yml](../../docker-compose.yml) + [docker-compose.prod.yml](../../docker-compose.prod.yml))
1. **`postgres` (`pgvector/pgvector:pg16`):**
   - **Role:** Primary relational and vector database.
   - **Network:** Reachable only via internal Docker network `trusttrip-net:5432`. Host port publishing is disabled (`ports: !reset []`).
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
   - **Port Binding:** Published exclusively to loopback interface `127.0.0.1:8080:80`.
   - **Proxying:** Forwards `/api/`, `/health`, and `/ready` to `http://backend:8000` with `proxy_buffering off;`.
   - **Healthcheck:** `wget -qO- http://localhost:80/nginx-health`.
4. **`cloudflared` (Containerized Service Disabled):**
   - In production, the containerized `cloudflared` service is disabled via `scale: 0` in `docker-compose.prod.yml`.

### B. Native Host Service
1. **`cloudflared` (Host `systemd` daemon):**
   - **Role:** Cloudflare Named Tunnel connector running directly on EC2.
   - **Target Ingress:** Proxies inbound public requests to local loopback `http://127.0.0.1:8080`.
   - **Lifecycle:** Managed via systemd (`sudo systemctl {start|stop|status} cloudflared`).

---

## 4. Pre-Deployment Configuration & Setup

### Step A: Configure Cloudflare Zero Trust Named Tunnel
1. Log in to the [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/).
2. Navigate to **Networks** $\to$ **Tunnels**.
3. Create a named tunnel (e.g., `trusttrip-prod`).
4. Under the **Public Hostnames** tab, add a route:
   - **Subdomain / Domain:** Your desired public domain (e.g. `planner.yourdomain.com`).
   - **Type:** `HTTP`
   - **URL:** `127.0.0.1:8080` *(routes directly to the host loopback port where Nginx is listening)*.

### Step B: Provision AWS EC2 Instance & Security Group
1. Launch an EC2 instance:
   - **AMI:** Ubuntu 24.04 LTS (64-bit x86_64).
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

### Step C: Install Docker Engine & Compose on EC2 (Ubuntu 24.04)
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

### Step D: Install & Configure Cloudflared Native Host Service

Install the official Cloudflare repository and package on Ubuntu 24.04:
```bash
# Add Cloudflare GPG key and APT repository
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared noble main' | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt-get update
sudo apt-get install -y cloudflared

# Confirm binary path (expected: /usr/bin/cloudflared)
command -v cloudflared
```

#### Authentication Option 1: Locally Managed Tunnel (Recommended)
This approach uses a credentials JSON file and a local YAML configuration:
```bash
sudo mkdir -p /etc/cloudflared

# Place the credentials JSON generated during tunnel creation
# Path: /etc/cloudflared/<TUNNEL_UUID>.json
sudo chmod 600 /etc/cloudflared/<TUNNEL_UUID>.json

# Create /etc/cloudflared/config.yml:
cat << 'EOF' | sudo tee /etc/cloudflared/config.yml
tunnel: <TUNNEL_UUID>
credentials-file: /etc/cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: <YOUR_PUBLIC_HOSTNAME>
    service: http://127.0.0.1:8080
  - service: http_status:404
EOF

sudo chmod 644 /etc/cloudflared/config.yml

# Install native systemd service using official installer
sudo cloudflared --config /etc/cloudflared/config.yml service install
```

#### Authentication Option 2: Remotely Managed Tunnel via Token-File
If managing routes via the Cloudflare dashboard, store the tunnel token in a root-restricted file:
```bash
sudo mkdir -p /etc/cloudflared

# Store token in a restricted file (no token in command line or shell history)
sudo touch /etc/cloudflared/tunnel-token
sudo chmod 600 /etc/cloudflared/tunnel-token
# Write the token to /etc/cloudflared/tunnel-token

# Create systemd unit using the literal absolute path /usr/bin/cloudflared
cat << 'EOF' | sudo tee /etc/systemd/system/cloudflared.service
[Unit]
Description=Cloudflare Tunnel
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run --token-file /etc/cloudflared/tunnel-token
Restart=on-failure
RestartSec=5s
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable cloudflared
```

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

*(Note: When running Cloudflared natively via host systemd, `TUNNEL_TOKEN` is managed via host credentials files as documented in Section 4.D and is not required inside `.env` because containerized `cloudflared` is scaled to 0).*

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

### B. Backup Archive Catalog Validation
This command reads the archive's table of contents and checks whether the catalog can be listed. It does not restore the database or prove that a complete restoration will succeed.

Do not pipe the validation command through `head` or other truncating utilities, as early stream termination triggers `SIGPIPE` and obscures the true exit status of `pg_restore`.

#### Alternative 1: Host-Side Catalog Check (Preferred when `pg_restore` is installed on host)
```bash
pg_restore --list ./backups/trusttrip_backup_<TIMESTAMP>.dump > /dev/null
echo "Exit status: $?"
# Expected: Exit status: 0
```

#### Alternative 2: Container-Based Catalog Check (When host utility is unavailable)
Stream the host backup file through standard input into the PostgreSQL container to preserve the true exit status:
```bash
cat ./backups/trusttrip_backup_<TIMESTAMP>.dump | docker compose exec -T postgres pg_restore --list > /dev/null
echo "Exit status: $?"
# Expected: Exit status: 0
```

> [!NOTE]
> Archive catalog validation verifies that the custom-format header and table of contents can be read without corruption. It does not perform data block decompression, foreign key checks, or a restoration test.

### C. Database Disaster Recovery / Restoration

> [!CAUTION]
> **DESTRUCTIVE RECOVERY OPERATION:**
> Running `pg_restore` with `--clean --if-exists` is an exceptional **disaster recovery operation** that drops and replaces existing tables in the target database.
>
> **Mandatory Pre-Restoration Safeguards:**
> 1. **Confirm Target Identity:** Verify that the active container is `trusttrip-postgres` and the database is `trusttrip`. Never run this against an unverified container.
> 2. **Verify Archive Catalog First:** Always execute Section 6.B (`pg_restore --list`) to confirm the backup catalog is valid.
> 3. **Create Pre-Restoration Safety Snapshot:** Before executing any restore, take a safety backup of the database's current state:
>    ```bash
>    docker compose exec postgres pg_dump -U trusttrip -d trusttrip -Fc > ./backups/pre_restore_safety_$(date +%Y%m%d_%H%M%S).dump
>    ```
> 4. **Obtain Operator Sign-Off:** Ensure administrative approval before proceeding with table drops.
> 5. **Untested Restoration Notice:** While backup creation (`pg_dump -Fc`) and archive listing (`pg_restore --list`) have been verified, actual destructive restoration over the authoritative database has **not** been executed during this review to preserve existing records.

To restore a verified custom-format dump into the PostgreSQL container during disaster recovery:
```bash
# 1. Stop public ingress and application containers
sudo systemctl stop cloudflared
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop web backend

# 2. Execute pg_restore with clean and if-exists flags
docker compose exec -T postgres pg_restore -U trusttrip -d trusttrip --clean --if-exists < ./backups/trusttrip_backup_<TIMESTAMP>.dump

# 3. Restart application containers and public ingress
docker compose -f docker-compose.yml -f docker-compose.prod.yml start backend web
sudo systemctl start cloudflared
```

---

## 7. Deployment Execution Workflow

> [!NOTE]
> **Operational Boundary:**
> - **Tested Locally:** Container builds, database startup, migration runner execution, Nginx reverse proxying to loopback port 8080, and local health probes have been verified.
> - **Untested Cloud Target:** Registering `cloudflared` to a live Cloudflare Edge tunnel and provisioning AWS EC2 hardware represent planned deployment procedures that have not yet been executed.

Execute the deployment sequence step by step:

```bash
# Step 1: Navigate to repository
cd /opt/trusttrip

# Step 2: Build production container images
docker compose -f docker-compose.yml -f docker-compose.prod.yml build

# Step 3: Launch PostgreSQL database service first
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres

# Step 4: Verify database container health (wait for 'healthy' status)
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps postgres

# Step 5: Launch Backend and Web reverse proxy
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d backend web

# Step 6: Verify Backend readiness and database migration status
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs backend | grep -i "Application ready"

# Step 7: Start native host Cloudflare Tunnel service
sudo systemctl start cloudflared

# Step 8: Verify Tunnel connectivity
sudo systemctl status cloudflared
sudo journalctl -u cloudflared -n 50 --no-pager
# Look for: "Connection ... registered" and "Registered tunnel connection"
```

---

## 8. Verification & Health Probes

### A. Non-Mutating Production Compose Configuration Assertion
Before starting containers, run this in-memory validation pipeline to verify the merged configuration without writing any temporary files:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --format json | python3 -c '
import sys, json
c = json.load(sys.stdin)
services = c.get("services", {})

# 1. PostgreSQL host ports must be completely stripped
pg = services.get("postgres", {})
assert not pg.get("ports"), f"PostgreSQL ports exposed: {pg.get(\"ports\")}"

# 2. Nginx must bind exclusively to loopback port 8080
web = services.get("web", {})
web_ports = [f"{p.get(\"host_ip\",\"0.0.0.0\")}:{p.get(\"published\")}:{p.get(\"target\")}" for p in web.get("ports", [])]
assert any("127.0.0.1:8080:80" in p for p in web_ports), f"Web not bound to 127.0.0.1:8080:80: {web_ports}"

# 3. Containerized cloudflared must be scaled to 0
cf = services.get("cloudflared", {})
assert cf.get("scale", 1) == 0, f"Cloudflared container scale is not 0: {cf.get(\"scale\")}"

# 4. Database volume must be preserved
assert "trusttrip_pgdata" in c.get("volumes", {}), "trusttrip_pgdata volume missing"

print("Production Compose validation PASSED.")
'
```

### B. Local Host Health Probes
Run the verification probes directly from the EC2 host against the loopback listener:

```bash
# 1. Verify Nginx reverse proxy health probe
curl -f http://127.0.0.1:8080/nginx-health
# Expected Output: healthy

# 2. Verify Backend readiness probe (probes PostgreSQL SELECT 1)
curl -f http://127.0.0.1:8080/ready
# Expected Output: {"status":"ready"}

# 3. Verify Backend process liveness probe
curl -f http://127.0.0.1:8080/health
# Expected Output: {"status":"ok","version":"2.0.0",...}

# 4. Verify Public HTTPS Ingress (from an external machine)
curl -I https://planner.yourdomain.com/nginx-health
# Expected Output: HTTP/2 200
```

---

## 9. Non-Destructive Service Recovery Procedure

If startup issues, bad configuration, or transient service failures occur, follow this **non-destructive service recovery** procedure. This process stops ingress, inspects diagnostics, and restarts services without reverting Git history or modifying database data:

```bash
# Step 1: Temporarily stop public ingress and application containers
sudo systemctl stop cloudflared
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop web backend

# Step 2: Inspect service diagnostics
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs backend
sudo journalctl -u cloudflared -n 50 --no-pager

# Step 3: Correct configuration or environment values in .env if needed
# (Ensure chmod 600 .env is maintained)

# Step 4: Restart application services
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d backend web

# Step 5: Verify local health probes
curl -f http://127.0.0.1:8080/ready

# Step 6: Resume public ingress
sudo systemctl start cloudflared
```

> [!NOTE]
> Service recovery addresses runtime failures and configuration errors safely. If fatal database corruption or unrecoverable application state occurs, refer to the destructive restoration workflow in **Section 6.C**.

---

## 10. Pre-Flight Release Gate Checklist

Before declaring the deployment live, verify every item below:

- [ ] **Infrastructure:** EC2 instance has **0 inbound rules** from `0.0.0.0/0` (ports 80, 443, 5432, 8000 closed to the internet).
- [ ] **Cloudflare Tunnel:** Native host `systemd` service is configured and points public traffic to `http://127.0.0.1:8080`.
- [ ] **Port Security:** Nginx is bound exclusively to `127.0.0.1:8080`; PostgreSQL has no published host ports.
- [ ] **Environment Security:** `APP_ENV=production` is set; `.env` permissions are `chmod 600`.
- [ ] **Key Length:** `JWT_SECRET` is at least 32 cryptographically secure characters.
- [ ] **Payload Encryption:** `PAYLOAD_ENCRYPTION_KEY` is a valid 32-byte base64 encoded key.
- [ ] **CORS & CSRF:** `ALLOWED_ORIGINS` matches the exact public Cloudflare hostname with **no wildcard (`*`)**.
- [ ] **Database Integrity:** Named volume `trusttrip_pgdata` is mapped and pre-deployment backup is created.
- [ ] **Archive Validation:** Backup catalog has been verified via Section 6.B (`pg_restore --list`).
- [ ] **Single Worker:** Backend Docker container is confirmed running `--workers 1`.
- [ ] **Fail-Closed Booking:** Live flight booking remains disabled (`DUFFEL_API_TOKEN` unset or in `test` mode).
- [ ] **Health Probes:** `/nginx-health`, `/ready`, and `/health` return HTTP 200 via `127.0.0.1:8080`.
