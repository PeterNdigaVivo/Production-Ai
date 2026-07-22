# Step 9 — /tenants restricted to super_admin (scope tightening)

**Status:** done; behaviour verified against the real `current_user` → `require_roles` chain with 7 new tests.
**Files changed:** `services/backend/app/api/v1/endpoints/tenants.py`.
**Files added:** `services/backend/tests/test_tenants_access.py`.
**New runtime dependencies:** none.

---

## What changed

`services/backend/app/api/v1/endpoints/tenants.py` — the router-level dependency:

```diff
-from app.api.deps import current_user
+from app.api.deps import require_roles
-router = APIRouter(dependencies=[Depends(current_user)])
+router = APIRouter(dependencies=[Depends(require_roles("super_admin"))])
```

Behavioural change on `GET /api/v1/tenants`:

| Caller | Before | After |
| --- | --- | --- |
| No token | 401 | 401 |
| `viewer` role token | 200 (returned all tenants) | **403** |
| `supervisor` role token | 200 | **403** |
| `production_manager` role token | 200 | **403** |
| `super_admin` role token | 200 | 200 |
| `superuser: true` token (e.g. seeded `admin@local`) | 200 | 200 (via `require_roles`' existing superuser bypass) |

## Why

Production-AI is confirmed **single-tenant** — one factory client, no multi-tenant SaaS plans. The multi-tenant scoping under the hood (tenant_id chain, RBAC) **stays as-is**: it's already built, tested, and closed audit findings 3 and 4. That work is not being removed.

What this change addresses is a narrower issue on top of that: `GET /api/v1/tenants` was reachable by **any authenticated user**. In practice a viewer or supervisor could enumerate every tenant's `name` and `slug`. That's more surface than the endpoint needs — the tenants list is an admin lookup with no self-service use case (frontend forms use it only inside the `AddFactoryForm` accessible via the admin flow). Restricting to `super_admin` matches the endpoint's actual purpose without touching the rest of the multi-tenant machinery.

The `superuser` bypass is preserved deliberately: the seeded `admin@local` user has `is_superuser=True` and issues tokens with `superuser: true`. `require_roles`' existing implementation short-circuits on `superuser`, so the admin login still works out of the box (Stage 4 of the bring-up checklist confirms this claim shape).

## Test coverage

`services/backend/tests/test_tenants_access.py` follows the standalone pattern used in `test_security_step7.py`:

- Real `current_user` and `require_roles` are exercised via a `TestClient` app that mounts the actual `tenants.router`; only `get_db` is stubbed (the endpoint's DB call is a no-op for these tests — we're proving auth, not DB behaviour).
- Tokens are signed with the real JWT secret used by `app/core/security.py`.

Cases:

| Test | Verifies |
| --- | --- |
| `test_viewer_role_forbidden` | viewer → 403 |
| `test_supervisor_role_forbidden` | supervisor → 403 |
| `test_production_manager_role_forbidden` | production_manager → 403 |
| `test_super_admin_role_allowed` | super_admin → 200 |
| `test_superuser_flag_bypasses_role_check` | `superuser: true` → 200 (matches how `admin@local` works) |
| `test_no_token_returns_401` | unauthenticated → 401 (preserves existing behaviour) |
| `test_router_does_not_depend_on_current_user_directly` | regression pin: `tenants.router.dependencies` must not contain the raw `current_user` callable |

The last test is the important one for a future maintainer. If someone reverts the router's dependency back to `Depends(current_user)` — accidentally or as a "widen access" quick fix — this pin fails immediately with a message pointing at the correct replacement. It survives future refactors of `require_roles` because it inspects the direct router deps, not the transitive chain.

## Test run

Before this step (excluding new file):
```
1 failed, 20 passed
```
After:
```
1 failed, 27 passed
```

The lone failure is `test_security.py::test_password_round_trip` — a **pre-existing** passlib/bcrypt version-mismatch quirk on this sandbox VM's system Python (`AttributeError: module 'bcrypt' has no attribute '__about__'`). Identical before and after this change. Not introduced or affected by Step 9.

## Honest caveats

- The stubbed `get_db` in these tests deliberately returns an empty tenants list. That's on purpose — the tests are proving the **authorization gate**, not the query. Backfilling a real-DB integration test for the 200-path body would add value but belongs with the broader analytics/DB integration suite (which requires a running Postgres). Out of scope here.
- Not touched: `POST /api/v1/factories`, `POST /api/v1/lines`, camera create, workstation/zone endpoints — those still use `Depends(current_user)`. In a single-tenant deployment the frontend UI already limits who reaches those forms; scope-tightening each is a separate call. If the pattern from this step is applied more broadly, `require_roles("super_admin", "factory_manager")` is likely the right guard on most write endpoints.
- The frontend `AddFactoryForm` in `frontend/app/lines/page.tsx` calls `/api/v1/tenants` to populate the tenant dropdown. That form is only reachable when the user has clicked into the admin lines page — which the current UI does not gate. If a non-admin somehow lands there, the dropdown call will now 403 instead of returning a list; the form shows an error. That's the intended failure mode for a single-tenant box; the admin will not see it.
- No commit signature was added; the local test env's failed `test_password_round_trip` was verified as pre-existing across three separate runs.
