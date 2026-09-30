// A camera's stream URL usually embeds its login (rtsp://user:pass@host/...).
// Lists show it with the password hidden; the full URL is only needed when
// editing, and only camera managers receive it at all (backend get_cameras).
export function maskStreamUrl(url: string | null | undefined): string {
  if (!url) return "";
  return url.replace(/^([a-z][a-z0-9+.-]*:\/\/[^:@/]+):[^@/]*@/i, "$1:••••@");
}
