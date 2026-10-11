/** Where to go after sign-in: only same-origin paths; anything else (or nothing) goes home. */
export function safeNext(next: string | null): string {
  if (!next?.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  return next;
}
