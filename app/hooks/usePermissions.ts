"use client";
// app/hooks/usePermissions.ts
//
// Right now permission checks only ever happen (if at all) server-side --
// the UI shows the same controls to everyone regardless of what they can
// actually do, so a user without `manage_cameras` sees a working-looking
// Delete button that just fails silently or 403s on click. This hook reads
// the same permissions object AdminUsersView already edits and exposes a
// simple `can("manage_cameras")` check for gating render + disabled state.
//
// NOTE: this is UX polish, not security -- backend.py must still enforce
// every one of these checks itself. This just stops showing controls a
// user can't use.

import { useMemo } from 'react';

export type PermissionKey =
  | 'view_map'
  | 'view_records'
  | 'view_history'
  | 'manage_cameras'
  | 'confirm_dismiss_alerts'
  // BUG FOUND 2026-09-22: missing from this union (and from lib/permissions.ts's
  // own separate PERMISSION_KEYS copy) since the key was added to the backend
  // -- can('manage_notify_targets') would still work at runtime (this is just
  // a TS union, nothing here throws), but it was never included in any of
  // the hardcoded role-default objects below, so it silently always read as
  // undefined/false for every role, including DEVTEAM.
  | 'manage_notify_targets';

type StoredUser = {
  role: string;
  permissions?: string | Record<string, boolean>; // backend sends JSON string
  custom_permissions?: boolean;
};

function readStoredUser(): StoredUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem("ecoUser");
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function usePermissions() {
  const user = readStoredUser();

  const permissions = useMemo<Record<string, boolean>>(() => {
    if (!user) return {};

    // DEVTEAM is unscoped and can do everything.
    if (user.role === 'DEVTEAM') {
      return {
        view_map: true, view_records: true, view_history: true,
        manage_cameras: true, confirm_dismiss_alerts: true, manage_notify_targets: true,
      };
    }

    // Admin tiers implicitly have everything EXCEPT manage_cameras, which is
    // barangay-only: the barangay funded and installed the smartpoles, PNP
    // consumes the feed. Mirrors BARANGAY_ONLY_PERMISSIONS in backend.py --
    // the server enforces this regardless; this just stops rendering a
    // Delete button a PNP admin would only get a 403 from.
    //
    // BUG FOUND 2026-09-04 (caught live testing the day's own override
    // feature): this returned the SAME hardcoded full-access object for
    // every PNP_ADMIN/BARANGAY_ADMIN unconditionally -- DevTeam could
    // password-confirm an override that revoked, say, view_map on the
    // backend (require_permission() correctly started 403ing that admin's
    // /api/incidents calls), and the sidebar still showed "Incident Map"
    // and let them click into a view that would just fail to load, because
    // this hook never even looked at custom_permissions or the real
    // permissions blob for an admin -- only for non-admin roles below. A
    // custom_permissions admin now reads their actual stored grants, same
    // as a standard operator account; an admin still on the automatic
    // default (the overwhelming majority, and every admin before this
    // feature existed) is completely unaffected by this branch.
    //
    // BUG FOUND 2026-09-22 (explicit user request: barangay loses the
    // crime-history archive, police-only now): this used to give BOTH
    // PNP_ADMIN and BARANGAY_ADMIN the same view_records/view_history:true
    // default. Split the branch -- PNP_ADMIN is unaffected, BARANGAY_ADMIN
    // now always reads false for both regardless of custom_permissions,
    // mirroring backend.py's POLICE_ONLY_PERMISSIONS hard ban (which
    // applies unconditionally, override or not -- see require_permission's
    // 2026-09-22 comment for why an override can't reopen this).
    if (user.role === 'PNP_ADMIN' && !user.custom_permissions) {
      return {
        view_map: true, view_records: true, view_history: true,
        manage_cameras: false, confirm_dismiss_alerts: true, manage_notify_targets: true,
      };
    }
    // BUG FOUND 2026-10-01: this forced view_map/manage_cameras/
    // confirm_dismiss_alerts on even for an overridden BARANGAY_ADMIN (so a
    // revoked Incident Map still showed and then failed to load), and left
    // manage_notify_targets out of the automatic set, which the backend
    // grants every non-overridden admin.
    if (user.role === 'BARANGAY_ADMIN') {
      if (!user.custom_permissions) {
        return {
          view_map: true, manage_cameras: true, confirm_dismiss_alerts: true, manage_notify_targets: true,
          view_records: false, view_history: false,
        };
      }
      const raw = typeof user.permissions === 'string'
        ? (() => { try { return JSON.parse(user.permissions as string); } catch { return {}; } })()
        : (user.permissions ?? {});
      return { ...raw, view_records: false, view_history: false };
    }

    // PNP_OFFICER can never manage cameras either, whatever the stored blob
    // says -- a stale grant from before this rule must not resurrect it.
    if (user.role === 'PNP_OFFICER') {
      const raw = typeof user.permissions === 'string'
        ? (() => { try { return JSON.parse(user.permissions as string); } catch { return {}; } })()
        : (user.permissions ?? {});
      return { ...raw, manage_cameras: false };
    }

    // BARANGAY_STAFF: same defense as PNP_OFFICER above -- a view_history/
    // view_records grant issued before the 2026-09-22 restriction existed
    // must not keep showing a nav item that would now just 403.
    if (user.role === 'BARANGAY_STAFF') {
      const raw = typeof user.permissions === 'string'
        ? (() => { try { return JSON.parse(user.permissions as string); } catch { return {}; } })()
        : (user.permissions ?? {});
      return { ...raw, view_history: false, view_records: false };
    }

    if (!user.permissions) return {};
    if (typeof user.permissions === 'string') {
      try { return JSON.parse(user.permissions); } catch { return {}; }
    }
    return user.permissions;
  }, [user]);

  const can = (key: PermissionKey) => !!permissions[key];

  return { can, permissions, role: user?.role ?? null };
}