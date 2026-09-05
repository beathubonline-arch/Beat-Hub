import AsyncStorage from '@react-native-async-storage/async-storage';

export const API_BASE_URL = 'https://mybeathub.com/api/v1';
const TOKEN_KEY = 'beathub_access_token';

export async function getToken() {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export async function setToken(token: string | null) {
  if (token) await AsyncStorage.setItem(TOKEN_KEY, token);
  else await AsyncStorage.removeItem(TOKEN_KEY);
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  const text = await response.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!response.ok) throw new Error(data?.detail || `Request failed (${response.status})`);
  return data as T;
}

export type User = {
  id: string;
  email: string;
  username: string;
  role: string;
  verified: boolean;
  stage_name?: string | null;
  slug?: string | null;
};

export type Track = {
  id: string;
  title: string;
  slug: string;
  description?: string | null;
  genre?: string | null;
  bpm?: number | null;
  price: number;
  currency: string;
  sales_model: string;
  is_sold: boolean;
  artwork_url?: string | null;
  preview_url?: string | null;
  producer?: string | null;
  producer_slug?: string | null;
};

export type Order = {
  id: string;
  order_number: string;
  status: string;
  amount: number;
  currency: string;
  track_slug?: string | null;
  track_title?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
};
