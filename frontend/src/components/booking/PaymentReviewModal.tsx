import React, { useState } from 'react';
import {
  X,
  CreditCard,
  ShieldCheck,
  Building2,
  Lock,
  ArrowRight,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Wallet,
} from 'lucide-react';
import { TravelerContactInfo } from '../../types/bookingFrontend';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';
import { loadRazorpayScript } from '../../lib/razorpay';

interface PaymentReviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  hotelName: string;
  roomName: string;
  startDate: string;
  endDate: string;
  guestsCount: number;
  roomAmount: number;
  taxesAmount?: number;
  currency?: string;
  priceInr?: number;
  exchangeRate?: number;
  contact: TravelerContactInfo;
  onProcessPayment: (
    method: 'card' | 'upi' | 'netbanking' | 'pay_at_property',
    contact: TravelerContactInfo,
    paymentDetails?: { transaction_id?: string; razorpay_order_id?: string; razorpay_payment_id?: string; razorpay_signature?: string }
  ) => Promise<void>;
  onInitiateOrder?: () => Promise<{
    transaction_id: string;
    razorpay_order_id: string;
    amount_inr: number;
    currency: string;
    key_id: string;
    hotel_name?: string;
  }>;
}

export const PaymentReviewModal: React.FC<PaymentReviewModalProps> = ({
  isOpen,
  onClose,
  hotelName,
  roomName,
  startDate,
  endDate,
  guestsCount,
  roomAmount,
  taxesAmount = 0,
  currency = 'USD',
  priceInr,
  exchangeRate,
  contact,
  onProcessPayment,
  onInitiateOrder,
}) => {
  const [paymentMethod, setPaymentMethod] = useState<'card' | 'upi' | 'netbanking' | 'pay_at_property'>('card');
  const [processingState, setProcessingState] = useState<'idle' | 'processing' | 'success' | 'failed'>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [simulateFailure, setSimulateFailure] = useState(false);

  if (!isOpen) return null;

  const isForeign = currency.toUpperCase() !== 'INR';
  const payableAmountInr = priceInr != null && !isNaN(Number(priceInr)) ? priceInr : (!isForeign ? roomAmount : undefined);
  const buttonPayableText = isForeign && payableAmountInr != null
    ? formatCurrency(payableAmountInr, 'INR')
    : formatCurrency(roomAmount, currency);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setProcessingState('processing');
    setErrorMessage(null);

    try {
      if (simulateFailure) {
        await new Promise((r) => setTimeout(r, 800));
        setProcessingState('failed');
        setErrorMessage('Simulated network payment failure.');
        return;
      }

      if (onInitiateOrder) {
        // 1. Server-side order creation
        const orderData = await onInitiateOrder();
        if (!orderData || !orderData.razorpay_order_id) {
          throw new Error('Failed to create hotel payment order on server.');
        }

        // 2. Load official Razorpay Checkout SDK
        const scriptLoaded = await loadRazorpayScript();
        if (!scriptLoaded || !(window as any).Razorpay) {
          throw new Error('Razorpay Checkout SDK could not be loaded. Please check your internet connection.');
        }

        // 3. Launch official Checkout modal
        const rzp = new (window as any).Razorpay({
          key: orderData.key_id,
          amount: Math.round(orderData.amount_inr * 100),
          currency: orderData.currency || 'INR',
          name: 'TrustTrip',
          description: `Hotel Reservation - ${orderData.hotel_name || hotelName || 'Hotel'}`,
          order_id: orderData.razorpay_order_id,
          handler: async (response: {
            razorpay_payment_id: string;
            razorpay_order_id: string;
            razorpay_signature: string;
          }) => {
            try {
              setProcessingState('processing');
              await onProcessPayment(paymentMethod, contact, {
                transaction_id: orderData.transaction_id,
                razorpay_order_id: response.razorpay_order_id || orderData.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
              });
              setProcessingState('success');
              setTimeout(() => {
                onClose();
              }, 1000);
            } catch (err: any) {
              setProcessingState('failed');
              setErrorMessage(err.message || 'Payment verification or hotel booking failed.');
            }
          },
          modal: {
            ondismiss: () => {
              setProcessingState('idle');
            },
          },
          prefill: {
            name: `${contact.first_name || ''} ${contact.last_name || ''}`.trim() || undefined,
            email: contact.email || undefined,
            contact: contact.phone || undefined,
          },
          theme: {
            color: '#0d9488',
          },
        });

        rzp.on('payment.failed', (response: any) => {
          setProcessingState('failed');
          setErrorMessage(response?.error?.description || 'Payment was declined by payment gateway.');
        });

        rzp.open();
        return;
      }

      // Fallback for direct test calls without order initiation
      await onProcessPayment(paymentMethod, contact);
      setProcessingState('success');
      setTimeout(() => {
        onClose();
      }, 1000);
    } catch (err: any) {
      setProcessingState('failed');
      setErrorMessage(err.message || 'Payment processing failed.');
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
                SECURE STAY CONFIRMATION
              </span>
            </div>
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              Payment & Guarantee
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

        {/* PROCESSING ANIMATION */}
        {processingState === 'processing' && (
          <div className="py-12 text-center space-y-3">
            <div className="w-12 h-12 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
              <RefreshCw className="w-6 h-6" />
            </div>
            <div className="font-editorial text-xl font-bold text-[var(--text-primary)]">
              Confirming your stay...
            </div>
            <p className="text-xs text-[var(--text-secondary)] font-light">
              Connecting to verified property reservation ledger and locking room rate.
            </p>
          </div>
        )}

        {/* SUCCESS STATE */}
        {processingState === 'success' && (
          <div className="py-12 text-center space-y-3">
            <div className="w-12 h-12 rounded-full bg-[var(--status-success)]/10 text-[var(--status-success)] mx-auto flex items-center justify-center">
              <CheckCircle2 className="w-6 h-6" />
            </div>
            <div className="font-editorial text-xl font-bold text-[var(--status-success)]">
              Payment Confirmed!
            </div>
            <p className="text-xs text-[var(--text-secondary)] font-light">
              Stay reservation confirmed with property. Handing over confirmed voucher.
            </p>
          </div>
        )}

        {/* FAILED STATE */}
        {processingState === 'failed' && (
          <div className="space-y-4 py-4">
            <div className="p-4 rounded-2xl bg-[var(--status-danger)]/10 border border-[var(--status-danger)]/30 text-xs space-y-2">
              <div className="flex items-center gap-1.5 font-bold font-mono text-[var(--status-danger)]">
                <AlertTriangle className="w-4 h-4" />
                We couldn't complete the payment
              </div>
              <p className="text-[var(--text-primary)] leading-relaxed">
                {errorMessage || 'The transaction could not be completed by the banking network. No funds were charged.'}
              </p>
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <Button
                variant="outline"
                size="md"
                onClick={() => {
                  setSimulateFailure(false);
                  setErrorMessage(null);
                  setProcessingState('idle');
                }}
              >
                Try again
              </Button>
            </div>
          </div>
        )}

        {/* NORMAL PAYMENT FORM */}
        {processingState === 'idle' && (
          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Stay Summary */}
            <div className="p-4 rounded-2xl bg-[var(--surface-muted)] border border-[var(--border)] space-y-2 text-xs">
              <div className="flex justify-between items-center text-[var(--text-secondary)]">
                <span>Property:</span>
                <span className="font-semibold text-[var(--text-primary)]">{hotelName}</span>
              </div>
              <div className="flex justify-between items-center text-[var(--text-secondary)]">
                <span>Room Type:</span>
                <span className="font-mono text-[var(--text-primary)]">{roomName}</span>
              </div>
              <div className="flex justify-between items-center text-[var(--text-secondary)]">
                <span>Dates:</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {startDate} — {endDate} ({guestsCount} guests)
                </span>
              </div>
              <div className="pt-2 border-t border-[var(--border)]/60 flex justify-between items-center text-[var(--text-secondary)]">
                <span>Hotel price:</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {formatCurrency(roomAmount, currency)}
                </span>
              </div>
              {isForeign && exchangeRate != null && !isNaN(Number(exchangeRate)) && (
                <div className="flex justify-between items-center text-[var(--text-secondary)] text-[11px]">
                  <span>FX rate:</span>
                  <span className="font-mono text-[var(--text-primary)]">
                    1 {currency.toUpperCase()} = ₹{Number(exchangeRate).toFixed(2)}
                  </span>
                </div>
              )}
              <div className="pt-2 border-t border-[var(--border)]/60 flex justify-between items-baseline font-bold text-sm">
                <span className="text-[var(--text-primary)]">Amount to pay:</span>
                <span className="text-lg font-editorial text-[var(--primary)] font-bold">
                  {isForeign && payableAmountInr != null
                    ? formatCurrency(payableAmountInr, 'INR')
                    : formatCurrency(roomAmount, currency)}
                </span>
              </div>
              {isForeign && (
                <p className="text-[11px] text-[var(--text-muted)] italic text-right">
                  Payment processed in INR through Razorpay.
                </p>
              )}
            </div>

            {/* Payment Method Selector */}
            <div className="space-y-2">
              <label className="text-xs font-semibold uppercase tracking-wider font-mono text-[var(--text-secondary)] block">
                Payment Method Preference
              </label>

              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setPaymentMethod('card')}
                  className={`p-3 rounded-xl border text-left text-xs transition-all cursor-pointer ${
                    paymentMethod === 'card'
                      ? 'border-[var(--primary)] bg-[var(--primary-soft)]/20 font-semibold text-[var(--primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  <CreditCard className="w-4 h-4 mb-1" />
                  Credit / Debit Card
                </button>

                <button
                  type="button"
                  onClick={() => setPaymentMethod('upi')}
                  className={`p-3 rounded-xl border text-left text-xs transition-all cursor-pointer ${
                    paymentMethod === 'upi'
                      ? 'border-[var(--primary)] bg-[var(--primary-soft)]/20 font-semibold text-[var(--primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  <Wallet className="w-4 h-4 mb-1" />
                  UPI / Instant Transfer
                </button>

                <button
                  type="button"
                  onClick={() => setPaymentMethod('netbanking')}
                  className={`p-3 rounded-xl border text-left text-xs transition-all cursor-pointer ${
                    paymentMethod === 'netbanking'
                      ? 'border-[var(--primary)] bg-[var(--primary-soft)]/20 font-semibold text-[var(--primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  <Building2 className="w-4 h-4 mb-1" />
                  Net Banking
                </button>

                <button
                  type="button"
                  onClick={() => setPaymentMethod('pay_at_property')}
                  className={`p-3 rounded-xl border text-left text-xs transition-all cursor-pointer ${
                    paymentMethod === 'pay_at_property'
                      ? 'border-[var(--primary)] bg-[var(--primary-soft)]/20 font-semibold text-[var(--primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  <ShieldCheck className="w-4 h-4 mb-1" />
                  Pay At Property
                </button>
              </div>
            </div>

            {/* Zero card data guarantee note */}
            <div className="text-[11px] text-[var(--text-muted)] flex items-center gap-1.5 font-light">
              <ShieldCheck className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
              <span>Secured by official Razorpay Checkout. Zero sensitive card or credential data is stored on TrustTrip servers.</span>
            </div>

            {/* Actions */}
            <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
              <Button variant="outline" size="md" onClick={onClose} className="w-full sm:w-auto">
                Cancel
              </Button>

              <Button
                type="submit"
                variant="primary"
                size="md"
                rightIcon={<ArrowRight className="w-4 h-4" />}
                className="w-full sm:w-auto shadow-xs"
              >
                Confirm & Pay ({buttonPayableText})
              </Button>
            </div>

            {/* Developer Testing Toggle */}
            <div className="pt-2 text-right">
              <label className="inline-flex items-center gap-1.5 text-[10px] font-mono text-[var(--text-muted)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={simulateFailure}
                  onChange={(e) => setSimulateFailure(e.target.checked)}
                  className="rounded text-[var(--primary)] focus:ring-0"
                />
                Simulate network payment failure
              </label>
            </div>
          </form>
        )}
      </div>
    </div>
  );
};
