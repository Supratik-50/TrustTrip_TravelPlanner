const TOKEN_KEY = 'trusttrip_auth_token';
const USER_KEY = 'trusttrip_user_cache';
const API_URL_KEY = 'trusttrip_custom_api_url';

export const storage = {
  getToken: (): string | null => {
    try {
      if (typeof window === 'undefined') return null;
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },

  setToken: (token: string): void => {
    try {
      if (typeof window === 'undefined') return;
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      // ignore storage errors
    }
  },

  clearToken: (): void => {
    try {
      if (typeof window === 'undefined') return;
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      // ignore storage errors
    }
  },

  getUser: <T = unknown>(): T | null => {
    try {
      if (typeof window === 'undefined') return null;
      const raw = localStorage.getItem(USER_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  },

  setUser: (user: unknown): void => {
    try {
      if (typeof window === 'undefined') return;
      localStorage.setItem(USER_KEY, JSON.stringify(user));
    } catch {
      // ignore storage errors
    }
  },

  clearUser: (): void => {
    try {
      if (typeof window === 'undefined') return;
      localStorage.removeItem(USER_KEY);
    } catch {
      // ignore storage errors
    }
  },

  getCustomApiUrl: (): string | null => {
    try {
      if (typeof window === 'undefined') return null;
      return localStorage.getItem(API_URL_KEY);
    } catch {
      return null;
    }
  },

  setCustomApiUrl: (url: string): void => {
    try {
      if (typeof window === 'undefined') return;
      localStorage.setItem(API_URL_KEY, url);
    } catch {
      // ignore
    }
  },

  clearAllAuth: (): void => {
    try {
      if (typeof window === 'undefined') return;
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    } catch {
      // ignore
    }
  }
};
