// Calls fn again when it rejects, up to n attempts.
export function retry<T>(fn: () => Promise<T>, n = 3): Promise<T> {
  return fn().catch((err) => (n > 1 ? retry(fn, n - 1) : Promise.reject(err)));
}
