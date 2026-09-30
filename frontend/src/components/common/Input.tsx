import React, { forwardRef } from 'react';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helperText?: string;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, helperText, leftIcon, rightIcon, id, className = '', ...props }, ref) => {
    const inputId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);

    return (
      <div className="w-full flex flex-col gap-1.5">
        {label && (
          <label
            htmlFor={inputId}
            className="text-xs font-semibold text-[var(--text-primary)] tracking-wide"
          >
            {label}
          </label>
        )}
        <div className="relative flex items-center">
          {leftIcon && (
            <div className="absolute left-3 text-[var(--text-secondary)] pointer-events-none shrink-0">
              {leftIcon}
            </div>
          )}
          <input
            id={inputId}
            ref={ref}
            className={`w-full bg-[var(--surface)] text-[var(--text-primary)] text-sm rounded-lg border transition-colors placeholder:text-[var(--text-secondary)]/50 py-2.5 px-3.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] focus-visible:ring-offset-1 disabled:bg-[var(--background)] disabled:opacity-60 disabled:cursor-not-allowed ${
              leftIcon ? 'pl-10' : ''
            } ${rightIcon ? 'pr-10' : ''} ${
              error
                ? 'border-[var(--status-danger)] focus-visible:ring-[var(--status-danger)]'
                : 'border-[var(--border)] hover:border-[var(--primary)]/60 focus-visible:border-[var(--primary)]'
            } ${className}`}
            {...props}
          />
          {rightIcon && (
            <div className="absolute right-3 text-[var(--text-secondary)] shrink-0">
              {rightIcon}
            </div>
          )}
        </div>
        {error ? (
          <span className="text-xs text-[var(--status-danger)] font-medium">{error}</span>
        ) : helperText ? (
          <span className="text-xs text-[var(--text-secondary)]">{helperText}</span>
        ) : null}
      </div>
    );
  }
);

Input.displayName = 'Input';
