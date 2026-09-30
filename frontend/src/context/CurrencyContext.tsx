import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import {
  SUPPORTED_CURRENCIES,
  CurrencyConfig,
  CURRENCY_STORAGE_KEY,
  getInitialCurrency,
  formatCurrency as baseFormatCurrency,
} from '../lib/currency';

export interface CurrencyContextType {
  currency: string;
  currencyConfig: CurrencyConfig;
  setCurrency: (code: string) => void;
  formatCurrency: (amount: number | string | undefined | null, sourceCurrency?: string) => string;
  formatAmount: (amount: number | string | undefined | null, sourceCurrency?: string) => string;
  supportedCurrencies: CurrencyConfig[];
}

const CurrencyContext = createContext<CurrencyContextType | undefined>(undefined);

export const CurrencyProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [currency, setCurrencyState] = useState<string>(getInitialCurrency);

  const setCurrency = useCallback((newCurrency: string) => {
    const upper = newCurrency.toUpperCase();
    if (SUPPORTED_CURRENCIES.some((c) => c.code === upper)) {
      setCurrencyState(upper);
      try {
        localStorage.setItem(CURRENCY_STORAGE_KEY, upper);
      } catch {
        // ignore storage errors
      }
    }
  }, []);

  // Synchronize across tabs if storage event fires
  useEffect(() => {
    const handleStorage = (e: StorageEvent) => {
      if (e.key === CURRENCY_STORAGE_KEY && e.newValue) {
        const upper = e.newValue.toUpperCase();
        if (SUPPORTED_CURRENCIES.some((c) => c.code === upper)) {
          setCurrencyState(upper);
        }
      }
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  // Formatter bound to current context preference if sourceCurrency is omitted
  const formatWithPreference = useCallback(
    (amount: number | string | undefined | null, sourceCurrency?: string): string => {
      // If sourceCurrency is explicitly provided (authoritative backend currency), use it!
      // Otherwise, use the user's selected global currency preference.
      return baseFormatCurrency(amount, sourceCurrency || currency);
    },
    [currency]
  );

  const currencyConfig =
    SUPPORTED_CURRENCIES.find((c) => c.code === currency) || SUPPORTED_CURRENCIES[0];

  return (
    <CurrencyContext.Provider
      value={{
        currency,
        currencyConfig,
        setCurrency,
        formatCurrency: formatWithPreference,
        formatAmount: formatWithPreference,
        supportedCurrencies: SUPPORTED_CURRENCIES,
      }}
    >
      {children}
    </CurrencyContext.Provider>
  );
};

export const useCurrency = (): CurrencyContextType => {
  const context = useContext(CurrencyContext);
  if (!context) {
    throw new Error('useCurrency must be used within a CurrencyProvider');
  }
  return context;
};
