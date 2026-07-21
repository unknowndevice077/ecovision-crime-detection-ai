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
    <div className={`p-4 bg-black/20 border border-white/5 rounded-2xl animate-pulse ${className}`}>
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="h-3 w-1/3 bg-white/10 rounded" />
          <div className="h-2.5 w-2/3 bg-white/5 rounded" />
        </div>
        <div className="h-8 w-16 bg-white/5 rounded-xl shrink-0" />
      </div>
    </div>
  );
}

export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonRow key={i} />
      ))}
    </div>
  );
}

export function SkeletonCard({ className = "" }: { className?: string }) {
  return (
    <div className={`bg-[#11141b] border border-white/5 rounded-2xl p-4 animate-pulse ${className}`}>
      <div className="h-4 w-4 bg-white/10 rounded mb-4" />
      <div className="h-6 w-12 bg-white/10 rounded mb-2" />
      <div className="h-2 w-20 bg-white/5 rounded" />
    </div>
  );
}