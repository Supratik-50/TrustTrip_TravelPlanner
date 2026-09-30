import React, { useState, useEffect } from 'react';
import {
  ExternalLink,
  ShieldCheck,
  Lock,
  Plane,
  Clock,
  ArrowRight,
  CheckCircle2,
  X,
  RefreshCw,
} from 'lucide-react';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';

interface CheckoutHandoffModalProps {
  isOpen: boolean;
  onClose: () => void;
  origin: string;
  destination: string;
  date: string;
  amount: number;
  currency?: string;
  providerName?: string;
  onSimulateComplete: () => Promise<void>;
}

export const CheckoutHandoffModal: React.FC<CheckoutHandoffModalProps> = ({
  isOpen,
  onClose,
  origin,
  destination,
  date,
  amount,
  currency = 'USD',
  providerName = 'Duffel Verified Carrier Checkout',
  onSimulateComplete,
}) => {
  const [step, setStep] = useState<'initiating' | 'external_session' | 'completing'>('initiating');

  useEffect(() => {
    if (isOpen) {
      setStep('initiating');
      const timer = setTimeout(() => {
        setStep('external_session');
      }, 1200);
      return () => clearTimeout(timer);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleComplete = async () => {
    setStep('completing');
    try {
      await onSimulateComplete();
    } finally {
      onClose();
    }
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
      <div className="relative w-full max-w-lg bg-[var(--surface)] border border-[var(--border)] rounded-3xl shadow-2xl p-6 sm:p-8 space-y-6 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b border-[var(--border)] pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Lock className="w-3.5 h-3.5 text-[var(--primary)]" />
              <span className="text-xs font-mono uppercase tracking-wider text-[var(--primary)] font-bold">
                SECURE EXTERNAL HANDOFF
              </span>
            </div>
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              Airline Checkout
            </h3>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-full border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)] flex items-center justify-center transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Status Display */}
        {step === 'initiating' && (
          <div className="py-12 text-center space-y-3">
            <div className="w-12 h-12 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
              <RefreshCw className="w-6 h-6" />
            </div>
            <div className="font-editorial text-lg font-bold text-[var(--text-primary)]">
              Opening secure checkout session...
            </div>
            <p className="text-xs text-[var(--text-secondary)] font-light">
              Transferring verified flight snapshot parameters to partner carrier portal.
            </p>
          </div>
        )}

        {step === 'external_session' && (
          <div className="space-y-5">
            <div className="p-4 rounded-2xl bg-[var(--surface-muted)] border border-[var(--border)] space-y-2 text-xs">
              <div className="flex justify-between items-center text-[var(--text-secondary)]">
                <span>Partner Gateway:</span>
                <span className="font-mono text-[var(--text-primary)] font-medium">
                  {providerName}
                </span>
              </div>
              <div className="flex justify-between items-center text-[var(--text-secondary)]">
                <span>Flight Route:</span>
                <span className="font-semibold text-[var(--text-primary)]">
                  {origin} → {destination}
                </span>
              </div>
              <div className="flex justify-between items-center text-[var(--text-secondary)]">
                <span>Travel Date:</span>
                <span className="font-mono text-[var(--text-primary)]">{date}</span>
              </div>
              <div className="flex justify-between items-center pt-2 border-t border-[var(--border)]/60 text-sm font-bold">
                <span className="text-[var(--text-primary)]">Verified Total:</span>
                <span className="font-editorial text-[var(--primary)] text-base">
                  {formatCurrency(amount, currency)}
                </span>
              </div>
            </div>

            <div className="p-4 rounded-2xl bg-[var(--primary-soft)]/20 border border-[var(--primary)]/20 space-y-1.5 text-xs text-[var(--text-secondary)]">
              <div className="flex items-center gap-1.5 font-bold font-mono text-[var(--primary)]">
                <ShieldCheck className="w-4 h-4" />
                Zero Card Data Stored On TrustTrip
              </div>
              <p className="leading-relaxed font-light">
                Ticket issuance and payment processing occur directly on the airline partner's secure
                infrastructure. In production, this completes via Duffel Link.
              </p>
            </div>

            <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
              <Button variant="outline" size="md" onClick={onClose} className="w-full sm:w-auto">
                Cancel checkout
              </Button>

              <Button
                variant="primary"
                size="md"
                onClick={handleComplete}
                rightIcon={<ArrowRight className="w-4 h-4" />}
                className="w-full sm:w-auto shadow-xs"
              >
                Complete external checkout & return
              </Button>
            </div>
          </div>
        )}

        {step === 'completing' && (
          <div className="py-12 text-center space-y-3">
            <div className="w-12 h-12 rounded-full bg-[var(--status-success)]/10 text-[var(--status-success)] mx-auto flex items-center justify-center animate-spin">
              <RefreshCw className="w-6 h-6" />
            </div>
            <div className="font-editorial text-lg font-bold text-[var(--text-primary)]">
              Synchronizing verified ticket reference...
            </div>
            <p className="text-xs text-[var(--text-secondary)] font-light">
              Retrieving carrier booking code and locking voucher into your TrustTrip plan.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
