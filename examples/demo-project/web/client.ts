import { retry } from './retry';
// Fetches a user by id.
export async function getUserById(id: string) { return retry(() => fetch(`/u/${id}`)); }
export class HttpClient {
  get(url: string) { return fetch(url); }
}
export const buildUrl = (base: string, p: string) => base + p;
export interface UserDto { id: string }
