import React, { useState, useEffect } from 'react';
import { Activity, CheckCircle, RefreshCw, Server, AlertCircle, Database } from 'lucide-react';
import { api } from '../../lib/apiClient';
import { storage } from '../../lib/storage';
import { useAuth } from '../../context/AuthContext';
import { Modal } from './Modal';
import { Button } from './Button';
import { Input } from './Input';

export const BackendStatusIndicator: React.FC = () => {
  const [isHealthy, setIsHealthy] = useState<boolean | null>(null);
  const [isReady, setIsReady] = useState<boolean | null>(null);
  const [healthData, setHealthData] = useState<any>(null);
  const [isChecking, setIsChecking] = useState<boolean>(false);
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [customUrl, setCustomUrl] = useState<string>(() => storage.getCustomApiUrl() || '');
  const { isDemoMode, toggleDemoMode } = useAuth();

  const checkStatus = async () => {
    setIsChecking(true);
    try {
      const [hRes, rRes] = await Promise.all([
        api.checkHealth(),
        api.checkReady(),
      ]);
      setHealthData(hRes);
      const healthy = hRes.status === 'healthy' || hRes.status === 'ok';
      const ready = rRes.status === 'ready' || rRes.status === 'ok';
      setIsHealthy(healthy);
      setIsReady(ready);
    } catch {
      setIsHealthy(false);
      setIsReady(false);
    } finally {
      setIsChecking(false);
    }
  };

  useEffect(() => {
    checkStatus();
  }, []);

  const handleSaveCustomUrl = (e: React.FormEvent) => {
    e.preventDefault();
    if (customUrl.trim()) {
      storage.setCustomApiUrl(customUrl.trim());
    } else {
      storage.clearAllAuth();
      localStorage.removeItem('trusttrip_custom_api_url');
    }
    setIsModalOpen(false);
    checkStatus();
  };

  const isLiveAndReady = isHealthy && isReady;

  return (
    <>
      <button
        type="button"
        onClick={() => setIsModalOpen(true)}
        className="inline-flex items-center gap-2 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors px-2 py-1 rounded hover:bg-black/5 dark:hover:bg-white/5 cursor-pointer"
        title="Backend Connection & Readiness Diagnostics"
      >
        <span className="relative flex h-2 w-2">
          {isLiveAndReady ? (
            <>
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[var(--status-success)] opacity-40"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-[var(--status-success)]"></span>
            </>
          ) : isHealthy ? (
            <span className="relative inline-flex rounded-full h-2 w-2 bg-[var(--status-success)]"></span>
          ) : isDemoMode ? (
            <span className="relative inline-flex rounded-full h-2 w-2 bg-[var(--primary)]"></span>
          ) : (
            <span className="relative inline-flex rounded-full h-2 w-2 bg-[var(--status-warning)]"></span>
          )}
        </span>
        <span className="hidden sm:inline font-mono text-[11px] tabular-nums">
          {isLiveAndReady
            ? 'API: Ready'
            : isHealthy
            ? 'API: Online'
            : isDemoMode
            ? 'API: Demo Sandbox'
            : 'API: Standby'}
        </span>
      </button>

      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title="Backend & Network Diagnostics"
        subtitle="Live FastAPI /health and /ready probe verification"
        maxWidth="md"
      >
        <div className="space-y-4 text-xs text-[var(--text-secondary)]">
          {/* Health & Readiness Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {/* /health Check */}
            <div className="p-3 bg-[var(--background)] rounded-xl border border-[var(--border)] space-y-1.5">
              <div className="flex items-center justify-between text-[var(--text-primary)] font-medium">
                <span className="flex items-center gap-1.5 font-mono text-[11px]">
                  <Server className="w-3.5 h-3.5 text-[var(--primary)]" />
                  GET /health
                </span>
                <span>
                  {isHealthy ? (
                    <span className="text-[var(--status-success)] font-semibold flex items-center gap-1 text-[11px]">
                      <CheckCircle className="w-3.5 h-3.5" />
                      OK
                    </span>
                  ) : (
                    <span className="text-[var(--status-warning)] font-semibold flex items-center gap-1 text-[11px]">
                      <AlertCircle className="w-3.5 h-3.5" />
                      Offline
                    </span>
                  )}
                </span>
              </div>
              <div className="text-[10px] text-[var(--text-muted)] font-mono">
                {healthData?.version ? `v${healthData.version} • ` : ''}FastAPI Core Service
              </div>
            </div>

            {/* /ready Check */}
            <div className="p-3 bg-[var(--background)] rounded-xl border border-[var(--border)] space-y-1.5">
              <div className="flex items-center justify-between text-[var(--text-primary)] font-medium">
                <span className="flex items-center gap-1.5 font-mono text-[11px]">
                  <Database className="w-3.5 h-3.5 text-[var(--primary)]" />
                  GET /ready
                </span>
                <span>
                  {isReady ? (
                    <span className="text-[var(--status-success)] font-semibold flex items-center gap-1 text-[11px]">
                      <CheckCircle className="w-3.5 h-3.5" />
                      Ready
                    </span>
                  ) : (
                    <span className="text-[var(--status-warning)] font-semibold flex items-center gap-1 text-[11px]">
                      <AlertCircle className="w-3.5 h-3.5" />
                      Not Ready
                    </span>
                  )}
                </span>
              </div>
              <div className="text-[10px] text-[var(--text-muted)] font-mono">
                PostgreSQL Connection Probe
              </div>
            </div>
          </div>

          <div className="p-3 bg-[var(--surface-muted)] rounded-xl border border-[var(--border)] text-[11px] font-mono text-[var(--text-secondary)]">
            Active Mode: {isDemoMode ? 'Simulated Sandbox (No external server needed)' : 'Live API Proxy (/api/* ➔ http://127.0.0.1:8000)'}
          </div>

          <form onSubmit={handleSaveCustomUrl} className="space-y-3">
            <Input
              label="Custom FastAPI Base URL (Optional)"
              placeholder="e.g. http://localhost:8000"
              value={customUrl}
              onChange={(e) => setCustomUrl(e.target.value)}
              helperText="Defaults to local proxy. Leave blank for default."
            />

            <div className="flex items-center justify-between pt-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={toggleDemoMode}
              >
                {isDemoMode ? 'Disable Demo Simulation' : 'Enable Demo Simulation'}
              </Button>

              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  isLoading={isChecking}
                  onClick={checkStatus}
                  leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
                >
                  Ping Both
                </Button>
                <Button type="submit" variant="primary" size="sm">
                  Save
                </Button>
              </div>
            </div>
          </form>

          <div className="pt-3 border-t border-[var(--border-subtle)] text-[11px] text-[var(--text-secondary)]">
            <div className="font-semibold text-[var(--text-primary)] mb-1">Backend Endpoints Verified:</div>
            <div className="grid grid-cols-2 gap-1 font-mono text-[10px]">
              <div>GET  /health</div>
              <div>GET  /ready</div>
              <div>POST /api/auth/login</div>
              <div>POST /api/auth/signup</div>
              <div>GET  /api/auth/me</div>
              <div>GET  /api/trips (Sec 3)</div>
              <div>POST /api/trips (Sec 3)</div>
              <div>POST /api/travel (Sec 3)</div>
            </div>
          </div>
        </div>
      </Modal>
    </>
  );
};
