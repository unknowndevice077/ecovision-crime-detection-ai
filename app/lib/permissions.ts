// Single source of truth for what the permission checkboxes in
// DevteamView.tsx / AdminUsersView.tsx actually mean at the point the
// backend enforces them. Mirrors backend.py's require_permission() and
// BARANGAY_ONLY_PERMISSIONS exactly -- previously each view kept its own
// copy of PERMISSION_KEYS and rendered all five as plain editable
// checkboxes for every role, including "Manage Cameras" for PNP accounts,
// which require_permission() 403s unconditionally regardless of what's
// stored in user_permissions. The checkbox worked (it saved), the
// permission just never did anything -- which is worse than not having
// the control, because it looks like a promise the app doesn't keep.

export const PERMISSION_KEYS = [
  { key: "view_map", label: "View Crime Map" },
  { key: "view_records", label: "View Video Records" },
  { key: "view_history", label: "View Crime History" },
  { key: "manage_cameras", label: "Manage Cameras" },
  { key: "confirm_dismiss_alerts", label: "Confirm / Dismiss Alerts" },
] as const;

export type PermissionKey = (typeof PERMISSION_KEYS)[number]["key"];

// "editable"  -- a real DB-checked grant; the checkbox does what it says.
// "always"    -- this role gets it automatically (backend's admin bypass);
//                showing an editable checkbox implies it could be turned
//                off, and it can't.
// "banned"    -- the backend 403s on this role for this key no matter what
//                user_permissions says. Cameras are barangay property; no
//                PNP account, any tier, gets administrative control over
//                them -- see backend.py's BARANGAY_ONLY_PERMISSIONS comment.
export type PermissionStatus = "editable" | "always" | "banned";

const ADMIN_ROLES = new Set(["PNP_ADMIN", "BARANGAY_ADMIN"]);
const PNP_ROLES = new Set(["PNP_ADMIN", "PNP_OFFICER"]);
const BARANGAY_ONLY_PERMISSIONS = new Set<string>(["manage_cameras"]);

// customPermissions added 2026-09-04 alongside backend.py's override
// endpoint (POST /api/devteam/users/{id}/override_permissions):
// PNP_ADMIN/BARANGAY_ADMIN permissions used to be unconditionally "always"
// -- there was no state in which an admin's own permission checkbox could
// mean anything, because require_permission()'s admin bypass ignored
// user_permissions entirely. DevTeam can now flip a specific admin's
// custom_permissions flag (password-confirmed; see backend.py's
// AdminPermissionOverride) to make the backend defer to their explicit
// grants instead -- this parameter is that flag's mirror on the frontend,
// so the same "always" row becomes a real "editable" one for exactly the
// admin(s) DevTeam has opted into overriding, and only those. Defaults to
// false so every existing call site (Create User, where the account being
// created can't have been overridden yet; AdminUsersView editing a
// non-admin) is unaffected.
export function permissionStatus(role: string, key: string, customPermissions: boolean = false): PermissionStatus {
  if (BARANGAY_ONLY_PERMISSIONS.has(key) && PNP_ROLES.has(role)) return "banned";
  if (ADMIN_ROLES.has(role) && !customPermissions && !BARANGAY_ONLY_PERMISSIONS.has(key)) return "always";
  return "editable";
}

export function permissionRowsFor(role: string, customPermissions: boolean = false) {
  return PERMISSION_KEYS.map((p) => ({ ...p, status: permissionStatus(role, p.key, customPermissions) }));
}

// Strips anything the backend would ignore anyway before a create/save
// request goes out, so a checked-but-inert box (an "always" row left
// checked, a "banned" row that somehow got checked before a role switch)
// never gets written into user_permissions as a row that looks granted
// but is dead on arrival.
export function onlyEditablePermissions(role: string, draft: Record<string, boolean>, customPermissions: boolean = false) {
  const out: Record<string, boolean> = {};
  for (const [key, val] of Object.entries(draft)) {
    if (permissionStatus(role, key, customPermissions) === "editable") out[key] = val;
  }
  return out;
}

// One-line explainer for whatever mix of statuses a role actually has,
// shown above the checkbox list instead of leaving the disabled/locked
// rows to speak for themselves.
export function permissionNoteFor(role: string, customPermissions: boolean = false): string | null {
  const rows = permissionRowsFor(role, customPermissions);
  const hasBanned = rows.some((r) => r.status === "banned");
  const hasAlways = rows.some((r) => r.status === "always");
  if (hasBanned && hasAlways) {
    return "Admin-tier accounts get view/alert access automatically. Camera management is locked for every PNP account -- cameras are barangay property, not police administration.";
  }
  if (hasBanned) {
    return "Camera management is locked for PNP accounts -- cameras are barangay property, not police administration.";
  }
  if (hasAlways) {
    return "Admin-tier accounts get view/alert access automatically. Camera management still needs a grant, same as any standard account.";
  }
  if (customPermissions) {
    return "Overridden: this admin's access now comes from these checkboxes only, same as a standard account, until reset back to automatic.";
  }
  return null;
}
