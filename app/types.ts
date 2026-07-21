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

export type UserRole = 'DEVTEAM' | 'PRECINCT_CAPTAIN' | 'POLICE' | 'BARANGAY_CAPTAIN' | 'BARANGAY';

export type User = {
  id: number;
  username: string;
  role: UserRole;
  barangayId: string;   // "Location", e.g. "cogon"
  assignment: string;
  parentAdminId?: number | null;
  permissions?: Record<string, boolean>;
};

export const ADMIN_ROLES: UserRole[] = ['PRECINCT_CAPTAIN', 'BARANGAY_CAPTAIN'];
export const STANDARD_ROLES: UserRole[] = ['POLICE', 'BARANGAY'];

export type Telemetry = {
  battery: number;
  solarV: number;
  tempCPU: number;
  tempESP: number;
  tempNeural: number;
  load: number;
};