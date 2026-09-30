export interface ApiError {
  message: string;
  statusCode: number;
  detail?: string | Record<string, unknown> | Array<unknown>;
  code?: string;
}

export interface ApiResponse<T> {
  data?: T;
  error?: ApiError;
  status: number;
}

export interface BackendHealth {
  status: 'ok' | 'healthy' | 'ready' | 'degraded' | 'offline';
  timestamp?: string;
  version?: string;
  database?: string;
  service?: string;
}
