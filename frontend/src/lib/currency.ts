/**
 * Currency formatting utilities for TrustTrip
 * Formats amounts with proper Indian numbering system (₹1,25,000) for INR
 * and standard international formatting for other currencies.
 * 
 * CRITICAL FINANCIAL SAFETY RULE:
 * This utility formats the given amount in the specified currency.
 * It NEVER performs fake conversion or substitutes symbols without exchange rates.
 */

export const CURRENCY_STORAGE_KEY = 'trusttrip_currency_preference';

export interface CurrencyConfig {
  code: string;
  symbol: string;
  name: string;
  label: string;
  locale: string;
}

export const SUPPORTED_CURRENCIES: CurrencyConfig[] = [
  { code: 'INR', symbol: '₹', name: 'Indian Rupee', label: 'INR (₹) - Indian Rupee', locale: 'en-IN' },
  { code: 'USD', symbol: '$', name: 'US Dollar', label: 'USD ($) - US Dollar', locale: 'en-US' },
  { code: 'EUR', symbol: '€', name: 'Euro', label: 'EUR (€) - Euro', locale: 'de-DE' },
  { code: 'GBP', symbol: '£', name: 'British Pound', label: 'GBP (£) - British Pound', locale: 'en-GB' },
  { code: 'JPY', symbol: '¥', name: 'Japanese Yen', label: 'JPY (¥) - Japanese Yen', locale: 'ja-JP' },
];

/**
 * Determines the initial currency on startup:
 * 1. Read saved preference from localStorage
 * 2. If none, check browser language (en-IN -> INR)
 * 3. Default to INR
 */
export const getInitialCurrency = (): string => {
  if (typeof window === 'undefined') return 'INR';
  try {
    const saved = localStorage.getItem(CURRENCY_STORAGE_KEY);
    if (saved && SUPPORTED_CURRENCIES.some((c) => c.code === saved.toUpperCase())) {
      return saved.toUpperCase();
    }
  } catch {
    // ignore localStorage errors
  }

  if (typeof navigator !== 'undefined' && navigator.language) {
    const lang = navigator.language.toLowerCase();
    if (
      lang.includes('in') ||
      lang.startsWith('hi') ||
      lang.startsWith('bn') ||
      lang.startsWith('ta') ||
      lang.startsWith('te')
    ) {
      return 'INR';
    }
    if (lang.includes('us')) return 'USD';
    if (lang.includes('gb') || lang.includes('uk')) return 'GBP';
    if (lang.includes('jp') || lang.startsWith('ja')) return 'JPY';
    if (
      lang.includes('de') ||
      lang.includes('fr') ||
      lang.includes('es') ||
      lang.includes('it')
    ) {
      return 'EUR';
    }
  }

  return 'INR';
};

/**
 * Centralized currency formatter.
 * @param amount The numeric or numeric string amount to format
 * @param currency The source currency code (e.g. 'INR', 'USD'). If omitted, falls back to saved user preference or INR.
 */
export const formatCurrency = (
  amount: number | string | undefined | null,
  currency?: string
): string => {
  if (amount === undefined || amount === null || amount === '') {
    const fallbackCode = getActiveCurrencyPreference();
    const config = SUPPORTED_CURRENCIES.find((c) => c.code === fallbackCode) || SUPPORTED_CURRENCIES[0];
    return `${config.symbol}0`;
  }

  const numericAmount =
    typeof amount === 'string'
      ? parseFloat(amount.replace(/[^0-9.-]+/g, ''))
      : Number(amount);

  const fallbackCode = getActiveCurrencyPreference();
  const curr = (currency || fallbackCode).toUpperCase();
  const config = SUPPORTED_CURRENCIES.find((c) => c.code === curr);

  if (isNaN(numericAmount)) {
    return `${config?.symbol || '₹'}0`;
  }

  const locale = config?.locale || (curr === 'INR' ? 'en-IN' : 'en-US');

  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: curr,
      maximumFractionDigits: curr === 'JPY' ? 0 : 0,
    }).format(numericAmount);
  } catch {
    // Fallback if currency code is unrecognized
    const symbol =
      config?.symbol ||
      (curr === 'INR'
        ? '₹'
        : curr === 'EUR'
        ? '€'
        : curr === 'GBP'
        ? '£'
        : curr === 'JPY'
        ? '¥'
        : '$');
    return `${symbol}${numericAmount.toLocaleString()}`;
  }
};

/**
 * Returns the currently active currency preference from storage or default
 */
export const getActiveCurrencyPreference = (): string => {
  if (typeof window === 'undefined') return 'INR';
  try {
    const saved = localStorage.getItem(CURRENCY_STORAGE_KEY);
    if (saved && SUPPORTED_CURRENCIES.some((c) => c.code === saved.toUpperCase())) {
      return saved.toUpperCase();
    }
  } catch {
    // ignore
  }
  return 'INR';
};
