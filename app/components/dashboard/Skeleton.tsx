// app/components/dashboard/Skeleton.tsx
//
// Replaces the "h-48 flex items-center justify-center ... animate-pulse"
// full-panel loading blocks used in HistoryView, AdminUsersView, RecordsView,
// DevteamView. Those replace the ENTIRE panel with centered text, so every
// poll/refetch causes a jarring layout swap. These skeletons match the
// final row shape so the panel never visibly "resets."
import React from 'react';

export function SkeletonRow({ className = "" }: { className?: string }) {
  return (
    <div
      className={`p-3 border animate-pulse ${className}`}
      style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
    >
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="h-3 w-1/3" style={{ background: 'var(--line-2)' }} />
          <div className="h-2.5 w-2/3" style={{ background: 'var(--line)' }} />
        </div>
        <div className="h-7 w-16 shrink-0" style={{ background: 'var(--line)' }} />
      </div>
    </div>
  );
}

export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonRow key={i} />
      ))}
    </div>
  );
}

export function SkeletonCard({ className = "" }: { className?: string }) {
  return (
    <div
      className={`border p-3 animate-pulse ${className}`}
      style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
    >
      <div className="h-4 w-4 mb-3" style={{ background: 'var(--line-2)' }} />
      <div className="h-6 w-12 mb-2" style={{ background: 'var(--line-2)' }} />
      <div className="h-2 w-20" style={{ background: 'var(--line)' }} />
    </div>
  );
}
