// A camera's stream URL usually embeds its login (rtsp://user:pass@host/...).
// Lists show it with the password hidden; the full URL is only needed when
// editing, and only camera managers receive it at all (backend get_cameras).
export function maskStreamUrl(url: string | null | undefined): string {
  if (!url) return "";
  return url.replace(/^([a-z][a-z0-9+.-]*:\/\/[^:@/]+):[^@/]*@/i, "$1:••••@");
}

// True when a registered camera's URL is the stream the AI core is watching.
// The core only ever reports its source with the whole login replaced by
// "***" (main.py _describe_source), so compare both in that form.
export function sameStreamSource(url: string | null | undefined, coreSource: string | null | undefined): boolean {
  if (!url || !coreSource) return false;
  const norm = (s: string) => s.trim().replace(/:\/\/[^/@]*@/, "://***@").replace(/\/+$/, "").toLowerCase();
  return norm(url) === norm(coreSource);
}
