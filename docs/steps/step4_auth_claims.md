# Step 4 — Auth claims: tenant_id + roles in the JWT (Finding 3)

**Status:** done; verified against a real DB and the project's JWT library.
**Files changed:** `services/backend/app/api/v1/endpoints/auth.py` (login + refresh).
**Files added:** `services/backend/app/services/auth.py` (claim builder), `services/backend/tests/test_auth_claims.py`.
**New runtime dependencies:** none.

---

## What was wrong (from the audit)

The architecture says tenant isolation is enforced via the JWT `tenant_id` claim and RBAC via a `roles` claim. But:

- `login()` put only `{"superuser": ...}` in the token — **no `tenant_id`, no `roles`**.
- `refresh()` re-signed with `sub` and **no claims at all**.

So `require_roles()` (which reads `user["roles"]`) saw an empty list for every non-superuser and **denied them every role-gated route**, while tenant-scoped queries had no `tenant_id` to filter on. RBAC and multi-tenancy looked implemented but were inert.

## What it does now

- **Login** loads the user's role names (`user_roles → roles`) and `tenant_id`, and embeds `{superuser, tenant_id, roles}` in the access token.
- **Refresh** re-loads the user from the DB and **rebuilds** the claims, rather than trusting the old token to carry them. This means a refreshed session reflects the user's *current* tenant and roles — revoke a role and the next refresh drops it. Refresh also now rejects a token whose user has been deleted or deactivated.
- A shared `build_user_claims()` in `app/services/auth.py` is the single source of truth, so login and refresh cannot drift (same pattern as the Step 3 analytics core).

`app/api/deps.py` already read `user.get("roles")` and `user.get("superuser")` — it was waiting for claims login never supplied. No change needed there; the fix simply provides what it expected.

## Interaction with Step 3 (gets stronger for free)

Step 3 scoped analytics at the data layer and *also* checked `tenant_id` from the token when present — but the token never had it, so only the data-layer check was active. Now that login embeds `tenant_id`, Step 3's `_caller_tenant()` returns a real value and the **token-level cross-check activates automatically**, on top of the data-layer guarantee. Defense in depth, no further change required. (The Step 3 code already handles the `None` case, so older tokens degrade gracefully.)

## How it was verified (I ran all of it)

Against an in-memory SQLite DB seeded with a tenant, two roles, and a user with both, plus the real `python-jose` library:

- role names load correctly from `user_roles → roles`;
- the access token carries `tenant_id` and `roles` after encode/decode;
- `require_roles('supervisor')` → allowed; `require_roles('factory_manager')` → denied; any-of matching works;
- a **pre-fix token (no roles) is denied** every role-gated route — the exact Finding 3 symptom, kept as a regression marker;
- refresh tokens stay claim-light (claims are rebuilt from the DB on refresh).

```
cd services/backend
pytest tests/test_auth_claims.py -v     # 5 passed
```

> Honest caveat: tests validate the claim-building and enforcement *logic* with SQLite + the real JWT lib. A full HTTP login→protected-route→refresh round-trip against the running FastAPI app + Postgres is an integration check to run on the server; the unit-level proof covers the logic that was broken.

## Security notes / what's still open

- **Refresh rotation/revocation (Finding 11)** is still open and intentionally out of scope here: a leaked refresh token remains valid for its TTL. This step is written to be compatible with that work (refresh already loads the user; adding a stored `jti` to rotate is the next increment).
- **`factory_id`-scoped roles.** `user_roles.factory_id` lets a role be scoped to one factory. This step puts global role *names* in the token (enough to make `require_roles` work). Per-factory authorization is a finer-grained enhancement for when endpoints need factory-level checks.

---

## Next

Step 5 — consumer groups across the detection/tracking/activity engines
(Finding 5): make the stream pipeline crash-safe and horizontally scalable.
