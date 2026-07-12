# PR Plan: feat/normalize-pve-shapes

**Branch:** `feat/normalize-pve-shapes`  
**Base:** `main`  
**Goal:** Implement the "optional small PR" listed as step #1 in `handoff.md` (post PRs #1-4).

## Context (from handoff.md and plan.md)

After PR #4 (`fix/ui-visual-pass`), the following was flagged:

> `/api/node` returns raw PVE shape (nested `memory`/`rootfs`, cores in `cpuinfo.cpus`, no flat `maxcpu`) and `/api/tasks/recent` passes tasks verbatim (`status` vs `exitstatus`). The frontend now tolerates both mock and real PVE; the backend *could* normalize these. Non-blocking — worth a small PR later.

`plan.md` defines the contract the frontend expects, but the backend should make mock and real PVE produce identical shapes for maintainability.

## What this PR does

1. **Backend normalization (`/api/node`)**  
   - `routes/metrics.py`: `node_info` now always emits flat `maxcpu`, `mem`, `maxmem` on the `status` object (sourced from `cpuinfo.cpus` / nested `memory` when the raw PVE response omits the flats).  
   - Original nested data is preserved via `**raw`.  
   - Storage list unchanged.

2. **Backend normalization (`/api/tasks/recent`)**  
   - `routes/vms.py`: Post-processes the list.  
   - Guarantees: `"status"` is the lifecycle state (`"running"` / `"stopped"`).  
   - `"exitstatus"` holds the result (`"OK"`, error string, or absent).  
   - Handles the variant where some PVE responses put the final result directly into `status`.

3. **Mock update (to exercise normalization)**  
   - `dev/mock_pve.py`: `node_status` now omits top-level `maxcpu`/`mem`/`maxmem` (matches "real PVE" shape described in handoff + frontend comments). Normalization in backend is what provides the flats.

4. **Frontend (docs + minor cleanup only)**  
   - `types.ts`, `NodePage.tsx`, `TasksTab.tsx`: Updated comments and tolerance ordering to reflect that normalization is now the source of truth. No logic or tolerance removal (keeps compat).

5. **Documentation**  
   - `handoff.md`: Updated the "flagged follow-ups" section and immediate next-steps to mark the item as addressed by this PR.

## Verification

- `pytest backend/tests/ -q` → 50 passed
- `cd frontend && npm run build` → clean
- Manual shape verification (via TestClient against mock + simulated normalization) confirms:
  - Raw mock: no flat maxcpu/mem
  - Normalized: flats present + nested preserved
- No changes to public contract for clients that were already tolerant.

## Scope & Risks

- Small, targeted change.
- No breaking changes (adds fields that were already being read via fallbacks).
- Does not touch `/api/tasks/{upid}/status` (used for live polling; already works).
- Aligns with `plan.md` §4 (endpoints) and the "tolerate both" note.

## How to review / merge

1. Review this branch: `feat/normalize-pve-shapes`
2. Check the two response normalizers + updated mock.
3. Run local tests if desired (`pytest` + `npm run build`).
4. Merge when ready (squash or rebase as per project style).
5. After merge, the "immediate next steps" list in handoff can continue to the Nix step.

This PR unblocks cleaner future work (e.g. removing some frontend tolerance code later if desired).

**Related:** `plan.md` (design), `handoff.md` (status + next steps).