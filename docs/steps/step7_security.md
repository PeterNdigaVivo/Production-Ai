# Step 7 — Remaining security hardening (Findings 7, 10, 11)

**Status:** done; security-critical logic verified; all changed files parse and PG-compatible.
**Files changed:** `services/backend/app/api/v1/endpoints/cameras.py`, `.../internal.py`, `.../endpoints/auth.py`, `.../endpoints/ws.py`, `app/core/security.py`, `services/video-ingestion/ingestion/rtsp_worker.py`, `frontend/components/LiveEventFeed.tsx`, `services/backend/tests/test_security.py`.
**Files added:** `app/services/token_store.py`, `services/backend/tests/test_security_step7.py`.
**New runtime dependencies:** none (Redis already present; used for the refresh-token store).

---

## Finding 7 — heartbeat endpoint was unauthenticated

`POST /cameras/{id}/heartbeat` lived on the public cameras router with no auth, so
anyone could keep a dead camera looking alive (defeating the offline-alert
watchdog) or enumerate camera IDs via 200-vs-404.

**Fix:** moved it to the internal router as `POST /cameras/{id}/_internal/heartbeat`,
gated by `require_internal_token` like the other service-to-service routes. The
ingestion worker now sends `X-Internal-Token` (it already held the secret) and
posts to the new path. The path segment `_internal` also makes nginx block it
from outside the cluster — I verified the existing nginx regex `/_internal(/|$)`
matches the new path (the first naming I tried, `…/heartbeat_internal`, would
NOT have been blocked; renamed to `/_internal/heartbeat` so it is). Defense in
depth: token gate **and** edge block.

## Finding 10 — WebSocket feed unauthenticated and cross-tenant

The live-events socket accepted any connection and streamed the global
`stream:events` to it — unauthenticated access to operational data, and every
client saw every tenant's events.

**Fix:**
- **Auth.** The access token is passed as a query param (`?token=…`; browsers
  can't set headers on a WebSocket) and validated *before* the socket is
  accepted; invalid/missing → policy-violation close. A refresh token does not
  authenticate the socket (access only).
- **Tenant filter.** Each event is mapped to its tenant via a cached
  workstation→tenant map (TTL-refreshed); only the caller's tenant's events are
  forwarded. **Fail closed:** an event that can't be mapped to a tenant is
  withheld from non-superusers. Superusers see all.
- Frontend updated to send the token on connect.

## Finding 11 — refresh tokens not rotated or revocable

A leaked refresh token was valid for its full 30-day TTL with no remedy short of
rotating `JWT_SECRET` (which logs everyone out).

**Fix: rotation + revocation via a Redis-backed jti store** (no DB migration):
- Each refresh token carries a unique `jti`; login records it as the user's
  active jti (`refresh:active:<user_id>`, TTL = refresh TTL).
- Refresh accepts only the currently-active jti, then **rotates** it (issues and
  stores a new jti). A reused/leaked old refresh token is rejected the moment the
  legitimate client refreshes.
- New `POST /auth/logout` revokes the user's refresh token immediately.

---

## How it was verified (I ran all of it)

With the real `python-jose` library and a fake Redis store:

- **Rotation** — after a refresh, the old jti is rejected and the new one
  accepted; **logout** revokes.
- **WS auth** — missing/invalid tokens rejected; a refresh token can't auth the
  socket.
- **WS tenant isolation** — tenant A sees only A's events, not B's; unknown
  workstation → withheld (fail closed); superuser sees all.

```
cd services/backend
pytest tests/test_security_step7.py tests/test_security.py -q   # 8 passed
```

> Honest caveats:
> - The refresh store keeps **one active refresh token per user** (simple, correct for single-session). Multi-device sessions would store a *set* of valid jtis per user — same mechanism, trivially extended.
> - WS tenant filtering relies on the workstation→tenant cache; events with no `workstation_id` are withheld from non-superusers by design. The cleaner long-term fix is to tag events with `tenant_id` at emission time (upstream), which would remove the lookup entirely — noted for a future pass.
> - Logic verified in isolation + parse/compile checks; a live HTTP/WS round-trip against running FastAPI + Redis + Postgres is a server-side integration check.

---

## Where this leaves the audit

Findings closed across Steps 2–7: **#1, #2, #3, #4, #5, #6, #7, #8, #10, #11.**
Remaining: **#9** (dev-admin auto-seed hardening — small, can fold into a config
pass) and **#12** (activity FSM time-weighting), plus the Phase-2 ML thinking.

## Next

Step 8 — FSM time-weighting (Finding 12) and a look at the Phase-2 ML / training
process improvements, which is where the "improve the training process" goal
from the brief comes in.
