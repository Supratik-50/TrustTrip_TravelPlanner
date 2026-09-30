import React, { useState } from 'react';
import { ShieldCheck, Check, ArrowRight, ArrowLeft, Sparkles, CheckCircle2 } from 'lucide-react';
import { Button } from '../common/Button';

interface ApprovalCardProps {
  isApproved: boolean;
  approvedAt?: string;
  onApprove: () => Promise<void>;
  onContinueToVerification: () => void;
  onBackToPlanning: () => void;
}

export const ApprovalCard: React.FC<ApprovalCardProps> = ({
  isApproved,
  approvedAt,
  onApprove,
  onContinueToVerification,
  onBackToPlanning,
}) => {
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleApprove = async () => {
    setIsSubmitting(true);
    try {
      await onApprove();
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isApproved) {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--primary)]/40 shadow-sm space-y-6">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
              YOUR JOURNEY IS READY
            </span>
            <span className="text-xs text-[var(--status-success)] font-mono flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5" /> Approved
            </span>
          </div>

          <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
            Your plan has been approved and is ready for booking.
          </h3>

          <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
            Next, we'll verify live flight seat availability with the airline partner and prepare your
            boutique stay reservation.
          </p>
        </div>

        {/* Visual Progress Milestones */}
        <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/60 border border-[var(--border)] space-y-2 text-xs">
          <div className="flex items-center gap-2 text-[var(--status-success)] font-medium font-mono">
            <Check className="w-4 h-4" />
            <span>Journey reviewed & verified</span>
          </div>
          <div className="flex items-center gap-2 text-[var(--status-success)] font-medium font-mono">
            <Check className="w-4 h-4" />
            <span>Journey approved</span>
            {approvedAt && (
              <span className="text-[11px] text-[var(--text-muted)] font-normal">
                ({new Date(approvedAt).toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })})
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 text-[var(--primary)] font-medium font-mono">
            <span className="w-4 h-4 rounded-full border border-[var(--primary)] flex items-center justify-center text-[9px]">
              3
            </span>
            <span>Flight verification & secure checkout handoff</span>
          </div>
          <div className="flex items-center gap-2 text-[var(--text-muted)] font-mono">
            <span className="w-4 h-4 rounded-full border border-[var(--border)] flex items-center justify-center text-[9px]">
              4
            </span>
            <span>Stay reservation & confirmation</span>
          </div>
        </div>

        {/* Action Button */}
        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
          <span className="text-xs text-[var(--text-muted)] font-mono">
            Step 2: Airline Verification
          </span>

          <Button
            variant="primary"
            size="lg"
            onClick={onContinueToVerification}
            rightIcon={<ArrowRight className="w-4 h-4" />}
            className="w-full sm:w-auto shadow-xs"
          >
            Continue to Flight Verification
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--border)] shadow-xs space-y-6">
      <div className="space-y-2 border-b border-[var(--border)] pb-4">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-[var(--primary)]" />
          <span className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
            CONFIRM YOUR CHOICES
          </span>
        </div>

        <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
          Everything looks good?
        </h3>

        <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
          Please review your flights, stay, and traveler information above. When you approve this
          journey, we will verify real-time seat inventory and secure your selected dates.
        </p>
      </div>

      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
        <Button
          variant="outline"
          size="md"
          onClick={onBackToPlanning}
          leftIcon={<ArrowLeft className="w-4 h-4" />}
          className="w-full sm:w-auto order-2 sm:order-1"
        >
          Back to planning
        </Button>

        <Button
          variant="primary"
          size="lg"
          onClick={handleApprove}
          isLoading={isSubmitting}
          rightIcon={<ArrowRight className="w-4 h-4" />}
          className="w-full sm:w-auto order-1 sm:order-2 shadow-xs"
        >
          Approve this journey
        </Button>
      </div>
    </div>
  );
};
