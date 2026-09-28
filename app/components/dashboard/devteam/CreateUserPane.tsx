"use client";

import { useEffect, useMemo, useState } from 'react';
import { KeyRound, Save, UserPlus, X } from 'lucide-react';
import PermissionTree, { CameraScopes, scopeProblem, scopesForSave } from './PermissionTree';
import RoleEditor, { RoleWarnings } from './RoleEditor';
import {
  CREATABLE_ROLES, PNP_ROLES, CameraRow, CustomRole, FieldInput, ManagedUser, PaneHeader, PendingLocation,
  SectionLabel, SelectInput, Station, authHeaders, camerasInScope, inputClass, labelClass, roleStyle,
} from './shared';
import { permissionStatus, PERMISSION_KEYS } from '../../../lib/permissions';
import { positionsForRole } from '../../../lib/positions';

const EMPTY_FORM = {
  username: '', password: '', role: 'PNP_ADMIN', barangay_id: '', station_id: '', parent_admin_id: '',
  custom_role_id: '', assignment: '', display_title: '',
  full_name: '', birthdate: '', home_address: '', contact_number: '', position: '',
};

export default function CreateUserPane({
  apiUrl, users, stations, cameras, allLocations, customRoles, fetchCustomRoles, flash, onCreated,
}: {
  apiUrl: string; users: ManagedUser[]; stations: Station[]; cameras: CameraRow[]; allLocations: PendingLocation[];
  customRoles: CustomRole[]; fetchCustomRoles: () => void; flash: (m: string) => void; onCreated: () => void;
}) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [perms, setPerms] = useState<Record<string, boolean>>({});
  const [scopes, setScopes] = useState<CameraScopes>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [addRoleOpen, setAddRoleOpen] = useState(false);
  // A role just made in the "+ Add role" modal isn't in customRoles until
  // the list refetch lands -- apply it once it shows up.
  const [pendingRoleId, setPendingRoleId] = useState<string | null>(null);

  useEffect(() => { fetchCustomRoles(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (patch: Partial<typeof EMPTY_FORM>) => setForm(prev => ({ ...prev, ...patch }));
  const isPnp = PNP_ROLES.includes(form.role);
  const isStandard = form.role === 'PNP_OFFICER' || form.role === 'BARANGAY_STAFF';

  const barangays = useMemo(() => allLocations.filter(l => l.status === 'approved' || !l.status), [allLocations]);
  const coveringStation = useMemo(
    () => (!isPnp && form.barangay_id ? stations.find(s => s.barangay_ids.includes(form.barangay_id)) : undefined),
    [isPnp, form.barangay_id, stations]);

  const scopeCameras = useMemo(
    () => camerasInScope(form.role, form.barangay_id, form.station_id, stations, cameras),
    [form.role, form.barangay_id, form.station_id, stations, cameras]);

  const eligibleParents = useMemo(() => {
    if (!isStandard) return [];
    const captainRole = form.role === 'PNP_OFFICER' ? 'PNP_ADMIN' : 'BARANGAY_ADMIN';
    return users.filter(u => u.role === captainRole &&
      (isPnp ? (!form.station_id || u.station_id === form.station_id) : (!form.barangay_id || u.barangay_id === form.barangay_id)));
  }, [users, form.role, form.station_id, form.barangay_id, isPnp, isStandard]);

  const rolesForSide = customRoles.filter(r => !r.org_type || r.org_type === (isPnp ? 'police' : 'barangay'));

  const switchRole = (role: string) => {
    set({ role, parent_admin_id: '', custom_role_id: '', position: '' });
    setPerms({});
    setScopes({});
  };

  // Picking a role pre-fills the tree with its preset; the operator can
  // still adjust any box before creating (explicit values win server-side).
  const applyCustomRole = (id: string) => {
    set({ custom_role_id: id });
    const role = customRoles.find(r => r.id === id);
    if (!role) return;
    const next: Record<string, boolean> = {};
    (role.permission_defaults || []).forEach(d => {
      if (!d.resource_type && permissionStatus(form.role, d.permission_key) === 'editable') next[d.permission_key] = true;
    });
    setPerms(next);
    setScopes({});
  };

  useEffect(() => {
    if (pendingRoleId && customRoles.some(r => r.id === pendingRoleId)) {
      applyCustomRole(pendingRoleId);
      setPendingRoleId(null);
    }
  }, [customRoles, pendingRoleId]); // eslint-disable-line react-hooks/exhaustive-deps

  const reset = () => { setForm(EMPTY_FORM); setPerms({}); setScopes({}); setError(''); };

  const create = async () => {
    setError('');
    if (!form.username.trim() || !form.password.trim()) return setError('Username and password are required.');
    if (!form.full_name.trim()) return setError('Full name is required.');
    if (!form.assignment.trim()) return setError('Assignment is required.');
    if (isPnp && !form.station_id) return setError('A police station is required for PNP roles.');
    if (!isPnp && !form.barangay_id) return setError('A barangay is required for barangay roles.');
    if (!isPnp && !coveringStation && !form.station_id) return setError('This barangay has no police station yet -- pick one to cover it.');
    const problem = scopeProblem(form.role, perms, scopes);
    if (problem) return setError(problem);

    // Every editable key sent explicitly (true or false) so a key the
    // operator unticked stays off even when a custom role would grant it.
    const permissions: Record<string, boolean> = {};
    PERMISSION_KEYS.forEach(p => {
      if (permissionStatus(form.role, p.key) === 'editable') permissions[p.key] = !!perms[p.key];
    });
    const saveScopes = scopesForSave(form.role, perms, scopes);
    const camera_scopes = Object.fromEntries(Object.entries(saveScopes).filter(([, v]) => v !== null));

    setBusy(true);
    try {
      const res = await fetch(`${apiUrl}/api/devteam/users`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({
          username: form.username.trim(),
          password: form.password,
          role: form.role,
          barangay_id: isPnp ? null : form.barangay_id,
          station_id: isPnp ? form.station_id : (form.station_id || null),
          assignment: form.assignment.trim(),
          display_title: form.display_title.trim() || null,
          parent_admin_id: form.parent_admin_id ? Number(form.parent_admin_id) : null,
          custom_role_id: form.custom_role_id || null,
          permissions,
          camera_scopes: Object.keys(camera_scopes).length ? camera_scopes : null,
          full_name: form.full_name.trim(),
          birthdate: form.birthdate || null,
          home_address: form.home_address.trim() || null,
          contact_number: form.contact_number.trim() || null,
          position: form.position || null,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok) {
        flash(`${form.username} created.`);
        reset();
        onCreated();
      } else setError(d.detail || 'Could not create account.');
    } catch {
      setError('Backend connection failure.');
    } finally {
      setBusy(false);
    }
  };

  const grantedPerms = Object.fromEntries(PERMISSION_KEYS.map(p => [p.key, !!perms[p.key]]));

  return (
    <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 gap-6 px-7 pb-7 pt-4">
      {/* LEFT — who the account is and where it sits */}
      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        <PaneHeader icon={<UserPlus size={12} />} title="Credentials" />
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-5 space-y-4">
          <div className="grid grid-cols-2 gap-2">
            {CREATABLE_ROLES.map(r => {
              const active = form.role === r.role;
              const style = roleStyle(r.role);
              return (
                <button
                  key={r.role}
                  onClick={() => switchRole(r.role)}
                  className={`flex items-center gap-2.5 px-3 py-2.5 border text-left transition-colors ${active ? `${style.border} ${style.bg}` : 'border-[var(--line)] hover:border-[var(--line-2)]'}`}
                >
                  <span className={`text-[8px] font-bold px-1.5 py-1 border ${style.border} ${style.text}`}>{r.code}</span>
                  <span className={`text-[10px] tracking-wide uppercase ${active ? style.text : 'text-[var(--text)]'}`}>{r.label}</span>
                </button>
              );
            })}
          </div>

          <div>
            <SectionLabel>Login</SectionLabel>
            <div className="space-y-3">
              <FieldInput label="Username *" value={form.username} onChange={v => set({ username: v })} />
              <FieldInput label="Password *" type="password" value={form.password} onChange={v => set({ password: v })} />
            </div>
          </div>

          <div>
            <SectionLabel>Personal record</SectionLabel>
            <div className="space-y-3">
              <FieldInput label="Full name *" value={form.full_name} onChange={v => set({ full_name: v })} placeholder="e.g. Juan Dela Cruz" />
              <div className="grid grid-cols-2 gap-3">
                <FieldInput label="Birthdate" type="date" value={form.birthdate} onChange={v => set({ birthdate: v })} />
                <FieldInput label="Contact number" value={form.contact_number} onChange={v => set({ contact_number: v })} placeholder="09XX-XXX-XXXX" />
              </div>
              <FieldInput label="Residence" value={form.home_address} onChange={v => set({ home_address: v })} placeholder="House no., street, barangay, city" />
            </div>
          </div>

          <div>
            <SectionLabel>Placement</SectionLabel>
            <div className="space-y-3">
              <SelectInput label="Position" value={form.position} onChange={v => set({ position: v })} options={positionsForRole(form.role)} />
              <FieldInput label="Assignment *" value={form.assignment} onChange={v => set({ assignment: v })} placeholder="e.g. Patrol Unit 3" />
              <FieldInput label="Display title (optional)" value={form.display_title} onChange={v => set({ display_title: v })} placeholder="e.g. Assistant Captain" />

              {isPnp ? (
                <SelectInput
                  label="Police station * — sees every barangay in its jurisdiction"
                  value={form.station_id}
                  onChange={v => { set({ station_id: v, parent_admin_id: '' }); setScopes({}); }}
                  placeholder={stations.length ? 'select a station…' : 'no stations yet — add one in Stations'}
                  options={stations.map(s => ({ value: s.id, label: `${s.name} (${s.barangay_ids.length} barangay${s.barangay_ids.length === 1 ? '' : 's'})` }))}
                />
              ) : (
                <>
                  <SelectInput
                    label="Barangay * — scoped to exactly this one"
                    value={form.barangay_id}
                    onChange={v => { set({ barangay_id: v, station_id: '', parent_admin_id: '' }); setScopes({}); }}
                    placeholder={barangays.length ? 'select a barangay…' : 'no barangays yet — add one in Stations'}
                    options={barangays.map(l => ({ value: l.id, label: l.city_municipality ? `${l.name} — ${l.city_municipality}` : l.name }))}
                  />
                  {form.barangay_id && (coveringStation ? (
                    <p className="text-[9px] text-[var(--text-3)]">Covered by <span className="text-[var(--text-2)]">{coveringStation.name}</span>.</p>
                  ) : (
                    <SelectInput
                      label="Police station — this barangay has none yet, pick one"
                      value={form.station_id}
                      onChange={v => set({ station_id: v })}
                      options={stations.map(s => ({ value: s.id, label: s.name }))}
                    />
                  ))}
                </>
              )}

              {isStandard && (
                <div>
                  <label className={labelClass}>Reports to (blank = the location&apos;s captain)</label>
                  <select value={form.parent_admin_id} onChange={e => set({ parent_admin_id: e.target.value })} className={inputClass}>
                    <option value="">Auto-attach to location captain</option>
                    {eligibleParents.map(p => <option key={p.id} value={p.id}>{p.full_name || p.username} ({p.role.replace(/_/g, ' ')})</option>)}
                  </select>
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="shrink-0 border-t border-[var(--line)] p-4 space-y-2">
          {error && <p className="text-[10px] text-[var(--critical)] uppercase tracking-wide">{error}</p>}
          <button
            onClick={create}
            disabled={busy}
            className="w-full py-2.5 bg-[var(--accent)] text-[#fff] text-[10px] font-bold tracking-[0.15em] uppercase disabled:opacity-50 transition-opacity hover:opacity-90 flex items-center justify-center gap-2"
          >
            <Save size={12} /> {busy ? 'Creating…' : 'Create account'}
          </button>
        </div>
      </div>

      {/* RIGHT — exactly what the account may do, down to each camera */}
      <div className="min-h-0 flex flex-col border border-[var(--line)]">
        <PaneHeader icon={<KeyRound size={12} />} title="Permissions" />
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-5 space-y-4">
          {isStandard && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-[8px] tracking-[0.15em] uppercase text-[var(--text-2)]">Custom role — pre-fills the tree below</label>
                <button onClick={() => setAddRoleOpen(true)} className="text-[9px] tracking-[0.1em] uppercase text-[var(--accent)] hover:opacity-80">+ Add role</button>
              </div>
              <select value={form.custom_role_id} onChange={e => applyCustomRole(e.target.value)} className={inputClass}>
                <option value="">No custom role — plain {isPnp ? 'PNP Officer' : 'Barangay Staff'}</option>
                {rolesForSide.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
          )}

          <PermissionTree
            role={form.role}
            perms={perms}
            onPermsChange={setPerms}
            scopes={scopes}
            onScopesChange={setScopes}
            cameras={scopeCameras}
            cameraHint={isPnp ? 'Pick a station on the left to choose its cameras.' : 'Pick a barangay on the left to choose its cameras.'}
            subject={form.full_name.trim() || form.username.trim() || 'This account'}
          />
          {isStandard && <RoleWarnings perms={grantedPerms} />}
          <p className="text-[9px] leading-relaxed text-[var(--text-3)]">
            Camera permissions expand into the cameras this account can reach. Leave them at &ldquo;all&rdquo; or narrow them to specific cameras.
          </p>
        </div>
      </div>

      {addRoleOpen && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4 bg-[var(--bg)]/85">
          <div className="bg-[var(--panel)] border border-[var(--line)] w-full max-w-md max-h-[88vh] overflow-y-auto custom-scrollbar font-mono">
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-[var(--panel-2)]">
              <span className="text-[10px] tracking-[0.15em] uppercase text-[var(--text)]">New custom role</span>
              <button onClick={() => setAddRoleOpen(false)}><X size={15} className="text-[var(--text-2)] hover:text-[var(--text)]" /></button>
            </div>
            <div className="p-5">
              <RoleEditor
                apiUrl={apiUrl}
                flash={flash}
                onCreated={(id) => {
                  setAddRoleOpen(false);
                  setPendingRoleId(id);
                  fetchCustomRoles();
                }}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
