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

export type User = {
  username: string;
  role: 'POLICE' | 'BARANGAY';
  assignment: string;
};

export type Telemetry = {
  battery: number;
  solarV: number;
  tempCPU: number;
  tempESP: number;
  tempNeural: number;
  load: number;
};