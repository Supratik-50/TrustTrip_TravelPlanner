import React, { createContext, useContext, useState, useCallback } from 'react';
import { CheckCircle2, AlertCircle, AlertTriangle, Info, X } from 'lucide-react';

export type NotificationType = 'success' | 'error' | 'warning' | 'info';

export interface NotificationItem {
  id: string;
  type: NotificationType;
  title?: string;
  message: string;
}

interface NotificationContextType {
  notifications: NotificationItem[];
  notify: (message: string, type?: NotificationType, title?: string, duration?: number) => void;
  removeNotification: (id: string) => void;
}

const NotificationContext = createContext<NotificationContextType | undefined>(undefined);

export const NotificationProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);

  const removeNotification = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const notify = useCallback(
    (message: string, type: NotificationType = 'info', title?: string, duration = 4500) => {
      const id = `${Date.now()}_${Math.random().toString(36).substring(2, 6)}`;
      const newItem: NotificationItem = { id, type, title, message };

      setNotifications((prev) => [...prev.slice(-3), newItem]);

      if (duration > 0) {
        setTimeout(() => {
          removeNotification(id);
        }, duration);
      }
    },
    [removeNotification]
  );

  return (
    <NotificationContext.Provider value={{ notifications, notify, removeNotification }}>
      {children}
      {/* Toast Notification Container */}
      <div
        className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-md w-full px-4 pointer-events-none"
        aria-live="polite"
      >
        {notifications.map((n) => {
          const isError = n.type === 'error';
          const isSuccess = n.type === 'success';
          const isWarning = n.type === 'warning';

          const borderBg = isError
            ? 'border-[#C94C4C] bg-[#FFF8F8] text-[#14213D]'
            : isSuccess
            ? 'border-[#27866A] bg-[#F4F9F6] text-[#14213D]'
            : isWarning
            ? 'border-[#C58A27] bg-[#FFFBF0] text-[#14213D]'
            : 'border-[#0F766E] bg-[#F7F5F0] text-[#14213D]';

          return (
            <div
              key={n.id}
              className={`pointer-events-auto flex items-start justify-between gap-3 p-3.5 rounded-lg border shadow-sm transition-all duration-200 ${borderBg}`}
            >
              <div className="flex items-start gap-2.5 pt-0.5">
                {isError && <AlertCircle className="w-4 h-4 text-[#C94C4C] shrink-0 mt-0.5" />}
                {isSuccess && <CheckCircle2 className="w-4 h-4 text-[#27866A] shrink-0 mt-0.5" />}
                {isWarning && <AlertTriangle className="w-4 h-4 text-[#C58A27] shrink-0 mt-0.5" />}
                {!isError && !isSuccess && !isWarning && <Info className="w-4 h-4 text-[#0F766E] shrink-0 mt-0.5" />}
                <div>
                  {n.title && <div className="text-xs font-semibold">{n.title}</div>}
                  <div className="text-xs text-[#5F6B7A] leading-relaxed">{n.message}</div>
                </div>
              </div>
              <button
                type="button"
                onClick={() => removeNotification(n.id)}
                className="text-[#5F6B7A] hover:text-[#14213D] transition-colors p-1"
                aria-label="Close notification"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </NotificationContext.Provider>
  );
};

export const useNotification = (): NotificationContextType => {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error('useNotification must be used within a NotificationProvider');
  }
  return context;
};
