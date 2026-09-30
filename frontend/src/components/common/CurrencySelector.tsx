import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check } from 'lucide-react';
import { useCurrency } from '../../context/CurrencyContext';

interface CurrencySelectorProps {
  className?: string;
  variant?: 'compact' | 'expanded';
}

export const CurrencySelector: React.FC<CurrencySelectorProps> = ({
  className = '',
  variant = 'compact',
}) => {
  const { currency, currencyConfig, setCurrency, supportedCurrencies } = useCurrency();
  const [isOpen, setIsOpen] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listboxRef = useRef<HTMLDivElement>(null);

  // Close when clicked outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setFocusedIndex(-1);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        setIsOpen(true);
        const currentIndex = supportedCurrencies.findIndex((c) => c.code === currency);
        setFocusedIndex(currentIndex >= 0 ? currentIndex : 0);
      }
      return;
    }

    switch (e.key) {
      case 'Escape':
        e.preventDefault();
        setIsOpen(false);
        setFocusedIndex(-1);
        buttonRef.current?.focus();
        break;
      case 'ArrowDown':
        e.preventDefault();
        setFocusedIndex((prev) => (prev + 1) % supportedCurrencies.length);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setFocusedIndex((prev) => (prev - 1 + supportedCurrencies.length) % supportedCurrencies.length);
        break;
      case 'Enter':
      case ' ':
        e.preventDefault();
        if (focusedIndex >= 0 && focusedIndex < supportedCurrencies.length) {
          setCurrency(supportedCurrencies[focusedIndex].code);
          setIsOpen(false);
          setFocusedIndex(-1);
          buttonRef.current?.focus();
        }
        break;
      case 'Tab':
        setIsOpen(false);
        setFocusedIndex(-1);
        break;
    }
  };

  const handleSelect = (code: string) => {
    setCurrency(code);
    setIsOpen(false);
    setFocusedIndex(-1);
    buttonRef.current?.focus();
  };

  return (
    <div
      ref={containerRef}
      className={`relative inline-block text-left ${className}`}
      onKeyDown={handleKeyDown}
    >
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        aria-label={`Select currency, current preference is ${currencyConfig.name}`}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        title={`Global Currency Preference: ${currencyConfig.code} (${currencyConfig.symbol})`}
        className="px-2.5 py-1.5 rounded-lg text-xs font-mono font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-black/5 dark:hover:bg-white/5 border border-transparent hover:border-[var(--border)] transition-all flex items-center gap-1.5 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]"
      >
        <span className="text-[var(--primary)] font-bold">{currencyConfig.symbol}</span>
        <span className="tracking-tight">{currencyConfig.code}</span>
        <ChevronDown
          className={`w-3 h-3 opacity-60 transition-transform duration-150 ${
            isOpen ? 'rotate-180' : ''
          }`}
        />
      </button>

      {isOpen && (
        <div
          ref={listboxRef}
          role="listbox"
          aria-label="Supported Currencies"
          className="absolute right-0 mt-1.5 w-52 origin-top-right rounded-xl bg-[var(--surface)] shadow-xl border border-[var(--border)] py-1.5 z-50 animate-in fade-in zoom-in-95 duration-100 divide-y divide-[var(--border-subtle)]"
        >
          <div className="px-3 py-1 text-[10px] font-mono uppercase tracking-wider text-[var(--text-muted)] font-semibold">
            Global Currency
          </div>
          <div className="py-1">
            {supportedCurrencies.map((opt, idx) => {
              const isSelected = currency === opt.code;
              const isFocused = focusedIndex === idx;

              return (
                <button
                  key={opt.code}
                  role="option"
                  aria-selected={isSelected}
                  type="button"
                  onClick={() => handleSelect(opt.code)}
                  onMouseEnter={() => setFocusedIndex(idx)}
                  className={`w-full flex items-center justify-between px-3 py-2 text-xs transition-colors cursor-pointer text-left ${
                    isSelected
                      ? 'bg-[var(--primary-soft)] text-[var(--primary)] font-semibold'
                      : isFocused
                      ? 'bg-black/5 dark:bg-white/5 text-[var(--text-primary)]'
                      : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-black/5 dark:hover:bg-white/5'
                  }`}
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="w-4 text-center font-bold text-sm text-[var(--primary)] shrink-0">
                      {opt.symbol}
                    </span>
                    <div className="min-w-0 flex items-baseline gap-1.5">
                      <span className="font-mono font-bold tracking-tight text-xs text-[var(--text-primary)]">
                        {opt.code}
                      </span>
                      <span className="text-[11px] text-[var(--text-muted)] truncate">
                        {opt.name}
                      </span>
                    </div>
                  </div>
                  {isSelected && (
                    <Check className="w-3.5 h-3.5 text-[var(--primary)] shrink-0 ml-1.5" />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
