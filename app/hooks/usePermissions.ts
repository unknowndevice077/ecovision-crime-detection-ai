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
  | 'confirm_dismiss_alerts';

type StoredUser = {
  role: string;
  permissions?: string | Record<string, boolean>; // backend sends JSON string
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
    // DEVTEAM / admin tiers (CAPTAIN roles) implicitly have full access --
    // the permissions blob only applies to standard POLICE/BARANGAY accounts
    // an admin created and scoped down.
    if (user.role === 'DEVTEAM' || user.role === 'PRECINCT_CAPTAIN' || user.role === 'BARANGAY_CAPTAIN') {
      return {
        view_map: true, view_records: true, view_history: true,
        manage_cameras: true, confirm_dismiss_alerts: true,
      };
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