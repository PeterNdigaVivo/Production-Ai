# Staging deploy — real Hikvision NVR on the GPU factory box

**Audience:** whoever brings up the first factory-adjacent staging environment.
**Scope:** single-tenant, real RTSP feeds, no fake demo data.
**Compose files:** `docker-compose.yml` + `docker-compose.staging.yml` (**not** `docker-compose.prod.yml` — that pulls from a registry CI doesn't populate yet).

This runbook takes you from a freshly-provisioned Linux box with a GPU to the first `docker compose logs -f video-ingestion` line showing real frames arriving from the Hikvision NVR.

---

## 1. Prerequisites

Verify each of these BEFORE touching the repo. If any check fails, stop and fix it — don't try to work around it.

### 1a. Docker + NVIDIA runtime

```bash
docker --version                 # want 24+ (compose v2 built in)
docker compose version           # want v2.x
nvidia-smi                       # must show the GPU and driver version
```

### 1b. GPU visible from a container

The NVIDIA Container Toolkit is separate from the driver. This one-liner proves the runtime is wired:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

If this prints the same `nvidia-smi` output as on the host, you're good. If it errors with `could not select device driver "nvidia"`, install `nvidia-container-toolkit` and restart the Docker daemon:

```bash
# Ubuntu/Debian shorthand — adapt to your distro
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
# Then re-run the `docker run --gpus all …` test above.
```

### 1c. Network line-of-sight to the NVR

The staging box must reach the Hikvision NVR's RTSP port (554/tcp by default). From the host:

```bash
# adjust to the NVR's actual IP
NVR_IP=192.168.1.10
nc -zv "$NVR_IP" 554        # want: succeeded
```

If `nc` isn't installed: `docker run --rm --network host busybox nc -zv "$NVR_IP" 554`.

If this fails, the compose bring-up won't help — resolve the firewall/VLAN issue at the network layer first. RTSP over TCP through NAT is a common failure; if the NVR is on a different subnet, verify routing before rebuilding anything.

### 1d. Ports on the host

```bash
ss -ltn | awk '$4 ~ /:(8000|3000|6379|5432|3001|5000|9090)$/'
```

Nothing should be listening on these already. If something is, stop it or change the port mapping in a compose override.

---

## 2. Clone + branch

```bash
git clone <repo-url> ~/production-ai
cd ~/production-ai
git checkout claude/happy-hamilton-otnhj9    # or whichever branch you're staging
```

Staging **does not** use `docker-compose.prod.yml`. That file overrides images to `${REGISTRY}/production-ai/*` which CI only populates on pushes to `main`, and that hasn't happened yet. We build locally.

---

## 3. Generate real secrets

Do NOT reuse the `.env.example` placeholders (`replace-with-a-32+byte-random-string`, `change-me-in-prod`, `admin`). This box may sit on a real factory network — the placeholders are known.

Use the generator already documented in `.env.example`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Run it once for each of:

| Env var | Notes |
| --- | --- |
| `JWT_SECRET` | Signs access + refresh tokens. Rotating it invalidates every session — that's fine. |
| `INTERNAL_SERVICE_TOKEN` | Shared secret between ingestion/tracking and the backend for `/_internal` endpoints. Must be identical across services (same `.env`, one process reads it per container). |
| `POSTGRES_PASSWORD` | Also update `DATABASE_URL` to match — the URL embeds the password. |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin login. |
| `DEV_ADMIN_PASSWORD` | The bootstrap admin (see §5). Make this strong even though the account is short-lived. |

---

## 4. Configure `.env`

```bash
cp .env.example .env
$EDITOR .env
```

**Fields that MUST be changed from the template values before this box touches a real network:**

| Field | Template value | Set to |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `change-me-in-prod` | 32+ random chars (§3) |
| `DATABASE_URL` | contains `change-me-in-prod` | update password to match `POSTGRES_PASSWORD` |
| `TIMESCALE_URL` | same | same |
| `JWT_SECRET` | `replace-with-…` | §3 output |
| `INTERNAL_SERVICE_TOKEN` | `replace-with-…` | §3 output (distinct from JWT_SECRET) |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | §3 output |
| `DEV_ADMIN_PASSWORD` | `admin` | §3 output |
| `YOLO_DEVICE` | `cuda:0` | keep — this is the GPU box |
| `YOLO_HALF` | `true` | keep — FP16 on GPU is cheaper |
| `INGEST_HW_ACCEL` | `auto` | keep for now; switch to `cuda` once NVDEC is verified working |

Strip any inline `# comment` from value lines — several `.env.example` lines have them, and Docker Compose treats the `#` as part of the value.

**Enable the staging override.** Append this line so `make up` / `docker compose up` automatically picks it up:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.staging.yml
```

Without this, `docker compose up` uses only the base file and the detection engine will run CPU-only.

**Dev-admin bootstrap (temporary — see §5):**

```
ENVIRONMENT=development
SEED_DEV_ADMIN=true
DEV_ADMIN_EMAIL=admin@<yourdomain>
DEV_ADMIN_PASSWORD=<strong password from §3>
```

`ENVIRONMENT=development` is required for the seed hook to run — it's an intentional guard against a misconfigured prod deploy silently creating a well-known superuser. On staging we want the seed exactly once, then we turn it off (see §7 turndown).

---

## 5. Bring up postgres + backend and migrate

```bash
make up               # brings the whole stack; ingestion will retry the NVR until §6 configures it
```

`make up` invokes `docker compose up -d --build`, which — thanks to the `COMPOSE_FILE` line from §4 — resolves to the base + staging overrides together. First build pulls the CUDA base image for `detection-engine` (~8 GB) and installs Python deps for the backend — allow a few minutes.

Once the containers report Healthy:

```bash
docker compose ps
make migrate          # runs `alembic upgrade head` inside the backend container
```

The dev-admin seed runs automatically on backend startup (`ENVIRONMENT=development` + `SEED_DEV_ADMIN=true`). Verify:

```bash
docker compose logs backend | grep 'seed\.dev_admin'
# want either "seed.dev_admin.created" (first boot) or "seed.dev_admin.already_exists" (subsequent boots)
```

---

## 6. Register the REAL factory / line / cameras — NOT `make seed`

**Do not run `make seed`.** It executes `services/backend/app/scripts/seed_demo.py`, which inserts:

- Tenant `Demo Co.`
- Factory `Plant 1` in Nairobi
- Line `Line A` with a target of 80 pieces/hour
- **Camera `Cam-101` with `rtsp_url = rtsp://user:pass@nvr.local:554/…`** — a fake NVR hostname that will never resolve

On a staging box this fake camera row causes the ingestion registry poller to burn cycles forever trying to connect to `nvr.local`, and pollutes analytics with a phantom camera the operators will see in the UI.

**Instead, register the real entities through the dashboard.**

1. Open `http://<staging-box>:3000` and sign in with `DEV_ADMIN_EMAIL` / `DEV_ADMIN_PASSWORD`.
2. **Production Lines** page → **+ Factory**: pick the auto-seeded `Default` tenant, enter your real factory name, timezone (e.g. `Africa/Nairobi`).
3. **Production Lines** page → **+ Add line**: pick the factory, name the line, set a realistic target pieces/hour.
4. **Cameras** page → **+ Add camera**: pick the line, name the camera (e.g. `Line-A-Cam-1`), paste the **real** RTSP URL — Hikvision sub-stream URL shape:

   ```
   rtsp://<user>:<pass>@<nvr-ip>:554/Streaming/Channels/102
   ```

   Sub-stream (`…/102`) first — lower bitrate, plenty for detection on GPU, far lighter to decode. Only switch to the main stream (`…/101`) if you have a specific reason (e.g. finer machine-flow signal once calibrated).

**Or, equivalently, via the REST API** (useful for scripted onboarding of many cameras):

```bash
# 6a. log in and grab the access token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$DEV_ADMIN_EMAIL\",\"password\":\"$DEV_ADMIN_PASSWORD\"}" | jq -r .access_token)

# 6b. list tenants (super_admin only after Step 9), grab Default tenant id
TENANT_ID=$(curl -s http://localhost:8000/api/v1/tenants -H "Authorization: Bearer $TOKEN" | jq -r '.[0].id')

# 6c. create factory
FACTORY_ID=$(curl -s -X POST http://localhost:8000/api/v1/factories \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"tenant_id\":\"$TENANT_ID\",\"name\":\"<real factory name>\",\"timezone\":\"Africa/Nairobi\"}" | jq -r .id)

# 6d. create line
LINE_ID=$(curl -s -X POST http://localhost:8000/api/v1/lines \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"factory_id\":\"$FACTORY_ID\",\"name\":\"Line 1\",\"target_pieces_per_hour\":80}" | jq -r .id)

# 6e. create camera (do NOT commit this command — the URL contains credentials)
curl -s -X POST http://localhost:8000/api/v1/cameras \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"line_id\":\"$LINE_ID\",\"name\":\"Line-1-Cam-1\",\"rtsp_url\":\"rtsp://<user>:<pass>@<nvr-ip>:554/Streaming/Channels/102\",\"fps_target\":8}"
```

Prefix each curl with a space (`HISTCONTROL=ignorespace` in `~/.bashrc` first) so the credentials don't land in shell history.

---

## 7. Verify frames are actually flowing from the NVR

Container `Up` status is not proof of ingestion. Check each layer explicitly.

### 7a. FFmpeg opened the RTSP stream

```bash
docker compose logs -f --tail=100 video-ingestion
```

Expect:
- `ingest.start camera_id=<uuid> cmd=ffmpeg -loglevel warning -nostdin -hwaccel …`
- No `Connection refused`, no `401 Unauthorized`, no `Server returned 404`.
- If you see repeated `ingest.error` with `readexactly` errors, the RTSP handshake failed — usually wrong credentials, wrong channel path, or the NVR only allows one concurrent session per user.

### 7b. Frames landing in Redis

```bash
CAM_ID=<the uuid you got from POST /cameras>
watch -n 3 "docker compose exec redis redis-cli XLEN stream:frames:$CAM_ID"
```

Expect the count to climb steadily. `INGEST_TARGET_FPS=8` caps how fast — you'll see ~8/second. If it flatlines at 0, ingestion isn't publishing; go back to 7a.

### 7c. Backend sees the camera as alive

Ingestion sends an authenticated heartbeat to `POST /cameras/{id}/_internal/heartbeat` every ~10s (Finding 7). Verify:

```bash
docker compose exec postgres psql -U production_ai -d production_ai \
  -c "select name, last_seen_at, now() - last_seen_at as age from cameras;"
```

`age` should be under a minute. If it's `null` or growing without bound, either the heartbeat POST is failing (check backend logs for `X-Internal-Token` 403s — means `INTERNAL_SERVICE_TOKEN` differs between services' env) or the camera row was created with the wrong id.

### 7d. Detection engine is inferring on those frames

```bash
docker compose logs -f --tail=100 detection-engine
```

Expect:
- `detector.loaded` with `device=cuda:0` (staging is GPU).
- On first startup, `detector.exporting_tensorrt` followed by `detector.exported` (TensorRT engine compile — a one-off ~30s pause; the resulting `.engine` file is cached to `/models`).
- Then no more log spam until inference starts. Errors like `CUDA out of memory` mean the box's GPU is undersized for the frame batch — reduce `imgsz` or drop `YOLO_HALF=true` isn't the answer; check what else on the GPU.

### 7e. End-to-end: events reaching the DB

After ~1 minute of real frames flowing:

```bash
docker compose exec postgres psql -U production_ai -d production_ai \
  -c "select state, count(*) from worker_events group by state;"
```

Expect rows in at least `WORKING`, `IDLE`, or `UNKNOWN`. If frames are flowing (7b confirmed) but this returns zero rows:
- Are workstations + zones defined for this camera? Without zones, tracking still runs but `workstation_id` is null and the activity FSM still emits events — check tracking-engine logs for `tracker.attach` on this camera.
- Is detection returning any persons? On a mostly-empty frame the FSM will produce nothing to persist. Aim the camera at people.

### 7f. Dashboard shows live KPIs

Open `http://<staging-box>:3000`. The Executive dashboard should show the real line name (not "demo") and non-zero counts under WORKING/IDLE. The Live Events feed should tick on state transitions.

---

## 8. Turndown — before this box goes anywhere near production

Once the real admin user is created via the API (`POST /api/v1/users` — TBD; not yet exposed) or you're ready to trust the seeded admin as the operational one:

1. In `.env`, delete `SEED_DEV_ADMIN=true` (or set to `false`). The next backend restart will not touch the admin row.
2. Change `ENVIRONMENT=development` to `ENVIRONMENT=staging`. The seed hook refuses to run in any environment other than `development` — this is the second layer of the two-layer gate from Finding 9.
3. Rotate `DEV_ADMIN_PASSWORD` — the seeded user's password hash is what it is at first-boot; changing the env value after the fact only affects the next seed run, not the existing row.
4. Consider setting `NEXT_PUBLIC_API_URL` to the LAN-reachable hostname of the staging box (baked into the frontend at build time — a `docker compose build --no-cache frontend` is needed after changing it).

Staging is **not** production. `docker-compose.prod.yml` (registry pulls, replicas, TLS via letsencrypt) and the Helm chart are out of scope for this runbook and will land after this box has been running against real footage for long enough to trust the calibration.

---

## Honest caveats

- **First frame ≠ good calibration.** Frames arriving proves the transport works; it says nothing about zone geometry, tracking accuracy, or whether the activity FSM's motion thresholds match the actual sewing-machine footprint at this factory. Expect a follow-up calibration pass once real footage is in hand.
- **Hikvision H.265 sub-streams** vary — some models publish `/Streaming/Channels/102`, some `/ISAPI/Streaming/Channels/102`, some need the `?transportmode=unicast` query. If §7a shows "Server returned 404", check the NVR's web UI under Configuration → Network → Advanced → Integration Protocol for the exact path.
- **The dev-admin gate is intentional friction.** If someone unsets `SEED_DEV_ADMIN` and can't log in, that's the gate working. Recovery: set it again, restart backend, sign in, then unset.
- **`.env` is gitignored**, but it contains RTSP credentials and DB passwords once populated. Treat it like a private key — chmod 600, don't scp it across untrusted networks, don't paste it into chat.
