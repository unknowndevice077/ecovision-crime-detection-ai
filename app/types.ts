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
};

export type Camera = {
  id: string;
  name: string;
  url: string;
  status: 'online' | 'offline';
};

export type UserRole = 'DEVTEAM' | 'PNP_ADMIN' | 'PNP_OFFICER' | 'BARANGAY_ADMIN' | 'BARANGAY_STAFF';

export type User = {
  id: number;
  username: string;
  role: UserRole;
  barangayId: string;   // "Location", e.g. "cogon"
  assignment: string;
  parentAdminId?: number | null;
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