// URL search-param helpers: change one param, keep the others.

export function withParam(p: URLSearchParams, name: string, value: string): URLSearchParams {
  const next = new URLSearchParams(p);
  next.set(name, value);
  return next;
}

export function withoutParam(p: URLSearchParams, name: string): URLSearchParams {
  const next = new URLSearchParams(p);
  next.delete(name);
  return next;
}
