import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants from 'expo-constants';

const configuredBase =
  process.env.EXPO_PUBLIC_API_BASE_URL ||
  (Constants.expoConfig?.extra?.apiBaseUrl as string | undefined) ||
  'https://mybeathub.com/api/v1';

export const API_BASE_URL = configuredBase.replace(/\/+$/, '');
const TOKEN_KEY = 'beathub_access_token';
let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

export async function getToken() {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export async function setToken(token: string | null) {
  if (token) await AsyncStorage.setItem(TOKEN_KEY, token);
  else await AsyncStorage.removeItem(TOKEN_KEY);
}

function errorMessage(data: any, fallback: string) {
  const detail = data?.detail;
  if (Array.isArray(detail)) {
    const text = detail.map((item: any) => item?.msg || item?.message).filter(Boolean).join('\n');
    if (text) return text;
  }
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (typeof data?.message === 'string' && data.message.trim()) return data.message;
  return fallback;
}

export async function apiResponse(
  path: string,
  options: RequestInit = {},
  timeoutMs = 25000,
): Promise<Response> {
  const token = await getToken();
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers, signal: controller.signal });
    if (response.status === 401) {
      await setToken(null);
      unauthorizedHandler?.();
    }
    return response;
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      throw new Error('BeatHub took too long to respond. Please try again.');
    }
    throw new Error('Unable to reach BeatHub. Check your internet connection and try again.');
  } finally {
    clearTimeout(timer);
  }
}

export async function api<T>(path: string, options: RequestInit = {}, timeoutMs = 25000): Promise<T> {
  const response = await apiResponse(path, options, timeoutMs);
  const text = await response.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }

  if (!response.ok) {
    if (response.status === 401) throw new Error('Your session has expired. Please sign in again.');
    throw new Error(errorMessage(data, `Request failed (${response.status})`));
  }
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
