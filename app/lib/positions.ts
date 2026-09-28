// Positions an applicant or account holder can claim, per side. Shown to
// DevTeam when reviewing an application ("who is this person there?").
export const BARANGAY_POSITIONS = [
  'Punong Barangay (Barangay Captain)',
  'Barangay Kagawad (Councilor)',
  'Barangay Secretary',
  'Barangay Treasurer',
  'Chief Tanod (Barangay Peacekeeping)',
  'Barangay Tanod',
  'SK Chairperson',
  'Barangay Staff',
];

export const PNP_POSITIONS = [
  'Chief of Police / Station Commander',
  'Deputy Chief of Police',
  'Precinct Commander (PCP)',
  'Operations Officer',
  'Investigation Officer',
  'Patrol Officer',
  'Desk Officer',
];

export function positionsForRole(role: string): string[] {
  return role.startsWith('PNP') ? PNP_POSITIONS : BARANGAY_POSITIONS;
}
