/**
 * Axios 实例与统一错误处理。
 *
 * 认证信息保存在 HttpOnly Cookie 中；写操作额外携带自定义安全标头。
 * 访问令牌过期时自动调用 /auth/refresh 并重放原请求（只重试一次）。
 */

import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios';
import type { ApiErrorBody } from '@/types';

/**
 * API 基地址。
 *
 * 默认按「与前端同源、同一基路径」推导：前端构建在 `/` 时得到 `/api/v1`，
 * 构建在 `/wendai/` 时得到 `/wendai/api/v1`。
 *
 * 这一点很关键：如果应用挂在父站点子路径下而 API 仍用根路径 `/api/v1`，
 * 请求会被父站点上其他应用的接口接走（实测返回 502），登录与注册全部失败。
 *
 * 需要指向独立后端域名时，显式设置 `VITE_API_BASE_URL` 覆盖。
 */
function resolveApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  if (configured && configured.trim()) return configured.trim();

  const base = import.meta.env.BASE_URL || '/';
  const normalised = base.endsWith('/') ? base : `${base}/`;
  return `${normalised}api/v1`;
}

export const API_BASE_URL = resolveApiBaseUrl();

export class ApiError extends Error {
  code: string;
  status: number;
  details: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || '请求失败');
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code || 'UNKNOWN';
    this.details = body.details || {};
  }
}

export const AI_UNAVAILABLE_CODE = 'AI_UNAVAILABLE';

export const AI_FALLBACK_MESSAGE = '智能服务暂时不可用，请手动完成当前操作。';

export function isAiUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.code === AI_UNAVAILABLE_CODE;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === AI_UNAVAILABLE_CODE) return AI_FALLBACK_MESSAGE;
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return '网络异常，请稍后重试';
}

const CSRF_HEADER = 'X-Requested-With';
const CSRF_VALUE = 'XMLHttpRequest';

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };

export const http: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30_000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

http.interceptors.request.use((config) => {
  const method = (config.method || 'get').toLowerCase();
  if (!['get', 'head', 'options'].includes(method)) {
    config.headers.set(CSRF_HEADER, CSRF_VALUE);
  }
  return config;
});

let refreshPromise: Promise<void> | null = null;

async function refreshSession(): Promise<void> {
  if (!refreshPromise) {
    refreshPromise = axios
      .post(
        `${API_BASE_URL}/auth/refresh`,
        {},
        {
          withCredentials: true,
          headers: { 'Content-Type': 'application/json', [CSRF_HEADER]: CSRF_VALUE },
        },
      )
      .then(() => undefined)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

function isAuthEndpoint(url: string | undefined): boolean {
  if (!url) return false;
  return (
    url.includes('/auth/login') ||
    url.includes('/auth/register') ||
    url.includes('/auth/refresh') ||
    url.includes('/auth/logout')
  );
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorBody>) => {
    const config = error.config as RetriableConfig | undefined;
    const status = error.response?.status;
    const skipRefresh = !config || isAuthEndpoint(config.url);

    if (status === 401 && config && !config._retried && !skipRefresh) {
      config._retried = true;
      try {
        await refreshSession();
        return http.request(config as AxiosRequestConfig);
      } catch {
        // 刷新失败 -> 交给调用方处理（通常跳转登录）
      }
    }

    const body: ApiErrorBody =
      error.response?.data && typeof error.response.data === 'object'
        ? error.response.data
        : { code: 'NETWORK_ERROR', message: '网络异常，请稍后重试' };

    return Promise.reject(new ApiError(error.response?.status ?? 0, body));
  },
);

export async function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const response = await http.get<T>(url, config);
  return response.data;
}

export async function post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const response = await http.post<T>(url, data, config);
  return response.data;
}

export async function patch<T>(url: string, data?: unknown): Promise<T> {
  const response = await http.patch<T>(url, data);
  return response.data;
}

export async function del<T>(url: string): Promise<T> {
  const response = await http.delete<T>(url);
  return response.data;
}

export async function upload<T>(url: string, formData: FormData): Promise<T> {
  const response = await http.post<T>(url, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}
