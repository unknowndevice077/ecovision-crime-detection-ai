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
  // BUG FOUND 2026-09-22: backend.py's VALID_PERMISSION_KEYS has always had
  // 6 keys -- this list (and usePermissions.ts's own separate copy) only
  // ever had 5, missing manage_notify_targets entirely. Not just cosmetic:
  // onlyEditablePermissions() strips anything not in permissionStatus's
  // key set before a create/save request goes out, so a manage_notify_
  // targets grant could never actually be applied through either Create
  // User or the permission-editor modal, no matter what -- the checkbox
  // for it simply didn't exist to check.
  { key: "manage_notify_targets", label: "Manage Responder Notifications" },
] as const;

export type PermissionKey = (typeof PERMISSION_KEYS)[number]["key"];

// Dimensions each permission can be narrowed along -- mirrors backend.py's
// RESOURCE_DIMENSIONS. No selection on a dimension = everything.
export type ResourceDimension = "camera" | "crime_type" | "channel";
export const RESOURCE_DIMENSIONS: Record<string, ResourceDimension[]> = {
  view_map: ["camera", "crime_type"],
  manage_cameras: ["camera"],
  view_records: ["crime_type"],
  view_history: ["crime_type"],
  confirm_dismiss_alerts: ["crime_type"],
  manage_notify_targets: ["channel"],
};

// Mirrors backend.py's CRIME_TYPES / NO_INCIDENT / NOTIFY_CHANNELS.
export const CRIME_TYPE_OPTIONS = [
  { id: "ASSAULT", label: "Assault" },
  { id: "ARMED THREAT", label: "Armed threat" },
  { id: "ROBBERY", label: "Robbery" },
  { id: "THEFT", label: "Theft" },
  { id: "PHYSICAL VIOLENCE", label: "Physical violence" },
  { id: "VANDALISM", label: "Vandalism" },
  { id: "HARDWARE_PANIC_INTERRUPT", label: "Panic button" },
];
export const NO_INCIDENT_OPTION = { id: "NO_INCIDENT", label: "Footage with no incident (24/7)" };
export const CHANNEL_OPTIONS = [
  { id: "telegram", label: "Telegram" },
  { id: "sms", label: "SMS" },
];

export const DIMENSION_LABELS: Record<ResourceDimension, { title: string; noun: string; plural: string }> = {
  camera: { title: "Cameras", noun: "camera", plural: "cameras" },
  crime_type: { title: "Crime types", noun: "crime type", plural: "crime types" },
  channel: { title: "Channels", noun: "channel", plural: "channels" },
};

// Fixed options for a non-camera dimension (cameras come from the account's
// jurisdiction instead).
export function dimensionOptions(key: string, dim: ResourceDimension) {
  if (dim === "crime_type") return key === "view_records" ? [...CRIME_TYPE_OPTIONS, NO_INCIDENT_OPTION] : CRIME_TYPE_OPTIONS;
  if (dim === "channel") return CHANNEL_OPTIONS;
  return [];
}

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
const BARANGAY_ROLES = new Set(["BARANGAY_ADMIN", "BARANGAY_STAFF"]);
const BARANGAY_ONLY_PERMISSIONS = new Set<string>(["manage_cameras"]);
// Added 2026-09-22 (explicit user request): the deep crime-history archive
// and video record vault are police-only now, mirrors backend.py's
// POLICE_ONLY_PERMISSIONS exactly -- barangay accounts (admin tier
// included) never get these two, full stop.
const POLICE_ONLY_PERMISSIONS = new Set<string>(["view_records", "view_history"]);

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
  if (POLICE_ONLY_PERMISSIONS.has(key) && BARANGAY_ROLES.has(role)) return "banned";
  // Every key an admin's side can hold is automatic until overridden --
  // exactly backend require_permission(). This used to leave manage_cameras
  // out for BARANGAY_ADMIN, so the form showed an unticked box for access the
  // captain actually had, and an override silently dropped it.
  if (ADMIN_ROLES.has(role) && !customPermissions) return "always";
  return "editable";
}

// Keys a role's side can never hold aren't listed at all -- a greyed-out
// "not for this side" row is just noise on a form that can't change it.
export function permissionRowsFor(role: string, customPermissions: boolean = false) {
  return PERMISSION_KEYS
    .map((p) => ({ ...p, status: permissionStatus(role, p.key, customPermissions) }))
    .filter((p) => p.status !== "banned");
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
  if (permissionRowsFor(role, customPermissions).some((r) => r.status === "always")) {
    return "Admin-tier accounts get view/alert access automatically.";
  }
  if (customPermissions) {
    return "Overridden: this admin's access comes from these checkboxes only, same as a standard account, until reset back to automatic.";
  }
  return null;
}
