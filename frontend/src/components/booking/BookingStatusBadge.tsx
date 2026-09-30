import React from 'react';
import {
  CheckCircle2,
  Clock,
  AlertTriangle,
  XCircle,
  ExternalLink,
  ShieldCheck,
  RefreshCw,
} from 'lucide-react';

export type BookingStatusType =
  | 'selected'
  | 'approved'
  | 'verifying'
  | 'ready'
  | 'handed_off'
  | 'pending'
  | 'confirmed'
  | 'failed'
  | 'cancelled'
  | 'expired';

interface BookingStatusBadgeProps {
  status: BookingStatusType;
  labelOverride?: string;
  className?: string;
}

export const BookingStatusBadge: React.FC<BookingStatusBadgeProps> = ({
  status,
  labelOverride,
  className = '',
}) => {
  const getMeta = () => {
    switch (status) {
      case 'confirmed':
        return {
          icon: <CheckCircle2 className="w-3.5 h-3.5 text-[var(--status-success)]" />,
          label: 'Confirmed & Guaranteed',
          textClass: 'text-[var(--status-success)]',
        };
      case 'approved':
        return {
          icon: <ShieldCheck className="w-3.5 h-3.5 text-[var(--primary)]" />,
          label: 'Plan Approved',
          textClass: 'text-[var(--primary)]',
        };
      case 'verifying':
        return {
          icon: <RefreshCw className="w-3.5 h-3.5 text-[var(--primary)] animate-spin" />,
          label: 'Checking Live Inventory...',
          textClass: 'text-[var(--primary)]',
        };
      case 'ready':
        return {
          icon: <CheckCircle2 className="w-3.5 h-3.5 text-[var(--primary)]" />,
          label: 'Ready for Checkout',
          textClass: 'text-[var(--primary)]',
        };
      case 'handed_off':
        return {
          icon: <ExternalLink className="w-3.5 h-3.5 text-[var(--terracotta)]" />,
          label: 'In External Checkout',
          textClass: 'text-[var(--terracotta)]',
        };
      case 'selected':
        return {
          icon: <span className="w-2 h-2 rounded-full bg-[var(--primary)]" />,
          label: 'Offer Selected',
          textClass: 'text-[var(--text-primary)]',
        };
      case 'pending':
        return {
          icon: <Clock className="w-3.5 h-3.5 text-[var(--text-muted)]" />,
          label: 'Pending Verification',
          textClass: 'text-[var(--text-secondary)]',
        };
      case 'expired':
        return {
          icon: <Clock className="w-3.5 h-3.5 text-[var(--status-warning)]" />,
          label: 'Fare Hold Expired',
          textClass: 'text-[var(--status-warning)]',
        };
      case 'failed':
        return {
          icon: <XCircle className="w-3.5 h-3.5 text-[var(--status-danger)]" />,
          label: 'Verification Failed',
          textClass: 'text-[var(--status-danger)]',
        };
      case 'cancelled':
        return {
          icon: <XCircle className="w-3.5 h-3.5 text-[var(--text-muted)]" />,
          label: 'Cancelled',
          textClass: 'text-[var(--text-muted)]',
        };
      default:
        return {
          icon: <span className="w-2 h-2 rounded-full bg-[var(--text-muted)]" />,
          label: status,
          textClass: 'text-[var(--text-secondary)]',
        };
    }
  };

  const meta = getMeta();

  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-mono font-medium ${meta.textClass} ${className}`}
    >
      {meta.icon}
      <span>{labelOverride || meta.label}</span>
    </span>
  );
};
