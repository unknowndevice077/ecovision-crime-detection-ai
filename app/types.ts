// app/types.ts
export type Alert = {
  id: string;
  type: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  location: string;
  area: string;
  timestamp: string;
  confidence: number;
  status: 'pending' | 'confirmed' | 'dismissed';
  screenshot_path?: string | null;
};

export type Camera = {
  id: string;
  name: string;
  url: string;
  status: 'online' | 'offline';
};

export type UserRole = 'DEVTEAM' | 'PNP_ADMIN' | 'PNP_OFFICER' | 'BARANGAY_ADMIN' | 'BARANGAY_STAFF';

// Not actually used to type currentUser anywhere (page.tsx keeps it as
// `any`), which is exactly why nothing caught this: the shape here was
// camelCase while the backend (_row_to_user_dict_base in backend.py) and
// localStorage's stored 'ecoUser' are snake_case. That mismatch is what
// made page.tsx's currentUser.barangayId always read undefined -- fixed
// there (2026-08-19), and fixed here so this type documents reality
// instead of the bug.
export type User = {
  id: number;
  username: string;
  role: UserRole;
  barangay_id: string;   // "Location", e.g. "cogon" -- null/absent for PNP roles
  station_id?: string | null;
  assignment: string;
  parent_admin_id?: number | null;
  permissions?: Record<string, boolean>;
};

export const ADMIN_ROLES: UserRole[] = ['PNP_ADMIN', 'BARANGAY_ADMIN'];
export const STANDARD_ROLES: UserRole[] = ['PNP_OFFICER', 'BARANGAY_STAFF'];
export const PNP_ROLES: UserRole[] = ['PNP_ADMIN', 'PNP_OFFICER'];
export const BARANGAY_ROLES: UserRole[] = ['BARANGAY_ADMIN', 'BARANGAY_STAFF'];

export type Telemetry = {
  battery: number;
  solarV: number;
  tempCPU: number;
  tempESP: number;
  tempNeural: number;
  load: number;
};