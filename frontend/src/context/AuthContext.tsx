import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { User, LoginCredentials, SignupCredentials } from '../types/auth';
import { api, setUnauthorizedHandler } from '../lib/apiClient';
import { storage } from '../lib/storage';
import { useNotification } from './NotificationContext';

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (credentials: LoginCredentials) => Promise<boolean>;
  signup: (credentials: SignupCredentials) => Promise<boolean>;
  logout: () => void;
  refreshMe: () => Promise<void>;
  clearError: () => void;
  isDemoMode: boolean;
  toggleDemoMode: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const token: string | null = null;
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isDemoMode, setIsDemoMode] = useState<boolean>(() => api.getDemoModeState());
  const { notify } = useNotification();

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Clear in-memory user state and storage even if backend logout fails
    } finally {
      storage.clearAllAuth();
      setUser(null);
      setError(null);
      notify('Signed out successfully', 'info');
    }
  }, [notify]);

  // Set up 401 unauthorized listener
  useEffect(() => {
    setUnauthorizedHandler(() => {
      storage.clearAllAuth();
      setUser((currentUser) => {
        if (currentUser) {
          notify('Your session expired. Please sign in again.', 'warning', 'Session Expired');
        }
        return null;
      });
    });

    return () => {
      setUnauthorizedHandler(null);
    };
  }, [notify]);

  // Initial user fetch on load via HttpOnly session cookie
  const refreshMe = useCallback(async () => {
    try {
      setIsLoading(true);
      const fetchedUser = await api.getMe();
      setUser(fetchedUser);
      storage.setUser(fetchedUser);
    } catch {
      storage.clearAllAuth();
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshMe();
  }, [refreshMe]);

  const login = async (credentials: LoginCredentials): Promise<boolean> => {
    try {
      setIsLoading(true);
      setError(null);
      const res = await api.login(credentials);
      
      if (res.user) {
        storage.setUser(res.user);
        setUser(res.user);
      } else {
        // Fetch user info via cookie session
        await refreshMe();
      }

      notify(`Welcome back, ${res.user?.email || credentials.email}!`, 'success');
      return true;
    } catch (err: any) {
      const msg = err.message || 'Failed to sign in. Please verify your credentials.';
      setError(msg);
      notify(msg, 'error', 'Sign In Failed');
      return false;
    } finally {
      setIsLoading(false);
    }
  };

  const signup = async (credentials: SignupCredentials): Promise<boolean> => {
    try {
      setIsLoading(true);
      setError(null);
      const res = await api.signup(credentials);

      if (res.user) {
        storage.setUser(res.user);
        setUser(res.user);
      } else {
        await refreshMe();
      }

      notify('Account created successfully. Welcome to TrustTrip!', 'success');
      return true;
    } catch (err: any) {
      const msg = err.message || 'Failed to create account.';
      setError(msg);
      notify(msg, 'error', 'Registration Failed');
      return false;
    } finally {
      setIsLoading(false);
    }
  };

  const clearError = () => setError(null);

  const toggleDemoMode = () => {
    const nextState = !isDemoMode;
    setIsDemoMode(nextState);
    api.setDemoMode(nextState);
    notify(
      nextState ? 'Switched to Demo Simulation Mode' : 'Switched to Live FastAPI Backend Mode',
      'info'
    );
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isAuthenticated: !!user,
        isLoading,
        error,
        login,
        signup,
        logout,
        refreshMe,
        clearError,
        isDemoMode,
        toggleDemoMode,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
