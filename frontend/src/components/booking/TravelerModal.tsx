import React, { useState, useEffect } from 'react';
import {
  X,
  User,
  Users,
  Calendar,
  Mail,
  Phone,
  AlertCircle,
  CheckCircle2,
  ShieldCheck,
  ArrowRight,
} from 'lucide-react';
import { FlightPassenger } from '../../types/flight';
import { Button } from '../common/Button';

interface TravelerModalProps {
  isOpen: boolean;
  onClose: () => void;
  travelers: FlightPassenger[];
  travelersCount: number;
  onSave: (updatedTravelers: FlightPassenger[]) => void;
  defaultEmail?: string;
}

export const TravelerModal: React.FC<TravelerModalProps> = ({
  isOpen,
  onClose,
  travelers,
  travelersCount,
  onSave,
  defaultEmail = '',
}) => {
  const [formData, setFormData] = useState<FlightPassenger[]>([]);
  const [activePassengerIndex, setActivePassengerIndex] = useState(0);
  const [errors, setErrors] = useState<Record<number, Record<string, string>>>({});
  const [generalError, setGeneralError] = useState<string | null>(null);

  // Sync state whenever modal opens or travelers change
  useEffect(() => {
    if (!isOpen) return;

    const count = Math.max(1, Math.min(9, travelersCount || 1));
    const normalized: FlightPassenger[] = [];

    for (let i = 0; i < count; i++) {
      const existing = travelers[i];
      normalized.push({
        given_name: existing?.given_name || '',
        family_name: existing?.family_name || '',
        born_on: existing?.born_on || '',
        title: existing?.title || (i === 0 ? 'mr' : 'mr'),
        gender: existing?.gender || 'm',
        email: existing?.email !== undefined ? existing.email : (i === 0 ? defaultEmail : ''),
        phone_number: existing?.phone_number || '',
        type: existing?.type || 'adult',
      });
    }

    setFormData(normalized);
    setErrors({});
    setGeneralError(null);
    setActivePassengerIndex(0);
  }, [isOpen, travelers, travelersCount, defaultEmail]);

  if (!isOpen) return null;

  const handleFieldChange = (index: number, field: keyof FlightPassenger, value: string) => {
    setFormData((prev) => {
      const updated = [...prev];
      updated[index] = {
        ...updated[index],
        [field]: value,
      };
      return updated;
    });

    // Clear field-specific error
    setErrors((prev) => {
      if (!prev[index]?.[field]) return prev;
      const copy = { ...prev };
      const passengerErrors = { ...copy[index] };
      delete passengerErrors[field];
      copy[index] = passengerErrors;
      return copy;
    });
    setGeneralError(null);
  };

  const validateAll = (): boolean => {
    const newErrors: Record<number, Record<string, string>> = {};
    let hasError = false;

    formData.forEach((p, idx) => {
      const pErr: Record<string, string> = {};

      if (!p.given_name || !p.given_name.trim()) {
        pErr.given_name = 'First name is required.';
        hasError = true;
      } else if (p.given_name.trim().length > 40) {
        pErr.given_name = 'First name must be at most 40 characters.';
        hasError = true;
      }

      if (!p.family_name || !p.family_name.trim()) {
        pErr.family_name = 'Last name is required.';
        hasError = true;
      } else if (p.family_name.trim().length > 40) {
        pErr.family_name = 'Last name must be at most 40 characters.';
        hasError = true;
      }

      if (!p.born_on || !p.born_on.trim()) {
        pErr.born_on = 'Date of birth is required.';
        hasError = true;
      } else if (!/^\d{4}-\d{2}-\d{2}$/.test(p.born_on.trim())) {
        pErr.born_on = 'Date of birth must be YYYY-MM-DD.';
        hasError = true;
      } else {
        const dob = new Date(p.born_on.trim());
        const now = new Date();
        if (isNaN(dob.getTime()) || dob >= now) {
          pErr.born_on = 'Date of birth must be a valid date in the past.';
          hasError = true;
        }
      }

      // Email validation: required for lead passenger (idx === 0), optional for companions (idx > 0)
      if (idx === 0) {
        if (!p.email || !p.email.trim()) {
          pErr.email = 'Contact email is required for the lead passenger.';
          hasError = true;
        } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(p.email.trim())) {
          pErr.email = 'Please enter a valid email address.';
          hasError = true;
        }
      } else if (p.email && p.email.trim()) {
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(p.email.trim())) {
          pErr.email = 'Please enter a valid email address.';
          hasError = true;
        }
      }

      // Phone validation: required for lead passenger (idx === 0), optional for companions (idx > 0)
      const validatePhone = (rawPhone: string) => {
        const cleaned = rawPhone.trim().replace(/[\s\-()]/g, '');
        const formatted = cleaned.startsWith('00') ? '+' + cleaned.slice(2) : cleaned;
        if (/^\d{10}$/.test(formatted)) return true;
        return /^\+\d{8,15}$/.test(formatted);
      };

      if (idx === 0) {
        if (!p.phone_number || !p.phone_number.trim()) {
          pErr.phone_number = 'Contact phone number is required for the lead passenger.';
          hasError = true;
        } else if (!validatePhone(p.phone_number)) {
          pErr.phone_number = 'Please enter a valid phone number (e.g. +91 98765 43210).';
          hasError = true;
        }
      } else if (p.phone_number && p.phone_number.trim()) {
        if (!validatePhone(p.phone_number)) {
          pErr.phone_number = 'Please enter a valid phone number (e.g. +91 98765 43210).';
          hasError = true;
        }
      }

      if (Object.keys(pErr).length > 0) {
        newErrors[idx] = pErr;
      }
    });

    setErrors(newErrors);
    if (hasError) {
      setGeneralError('Please fix the highlighted errors before saving.');
      // Switch to first tab with errors
      const firstErrorIdx = Number(Object.keys(newErrors)[0]);
      if (!isNaN(firstErrorIdx)) {
        setActivePassengerIndex(firstErrorIdx);
      }
      return false;
    }
    return true;
  };

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    if (!validateAll()) return;

    const cleaned: FlightPassenger[] = formData.map((p) => ({
      given_name: p.given_name.trim(),
      family_name: p.family_name.trim(),
      born_on: p.born_on.trim(),
      title: p.title || 'mr',
      gender: p.gender || 'm',
      email: p.email?.trim() || null,
      phone_number: p.phone_number?.trim() || null,
      type: p.type || 'adult',
    }));

    onSave(cleaned);
    onClose();
  };

  const currentPassenger = formData[activePassengerIndex] || {
    given_name: '',
    family_name: '',
    born_on: '',
    title: 'mr',
    gender: 'm',
    email: '',
    phone_number: '',
  };
  const currentErrors = errors[activePassengerIndex] || {};

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
      <div className="relative w-full max-w-2xl bg-[var(--surface)] border border-[var(--border)] rounded-3xl shadow-2xl p-6 sm:p-8 space-y-6 animate-in fade-in zoom-in-95 duration-200 max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b border-[var(--border)] pb-4 shrink-0">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-[var(--primary)]" />
              <span className="text-xs font-mono uppercase tracking-wider text-[var(--primary)] font-bold">
                LEGAL TRAVELER IDENTITY
              </span>
            </div>
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              Who is Traveling?
            </h3>
            <p className="text-xs text-[var(--text-secondary)] font-light">
              Enter government-issued ID details for all {formData.length} passenger{formData.length > 1 ? 's' : ''}.
              This legal identity will be transmitted directly to airline ticketing and hotel reservations.
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-full border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)] flex items-center justify-center transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* General Error Banner */}
        {generalError && (
          <div className="p-3 rounded-2xl bg-[var(--status-danger)]/10 border border-[var(--status-danger)]/30 text-xs text-[var(--status-danger)] flex items-center gap-2 shrink-0">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{generalError}</span>
          </div>
        )}

        {/* Multi-Passenger Tab Switcher */}
        {formData.length > 1 && (
          <div className="flex items-center gap-2 overflow-x-auto pb-1 shrink-0 border-b border-[var(--border)]">
            {formData.map((p, idx) => {
              const isSelected = activePassengerIndex === idx;
              const hasTabError = Boolean(errors[idx] && Object.keys(errors[idx]).length > 0);
              const isFilled = p.given_name.trim() && p.family_name.trim() && p.born_on.trim();

              return (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setActivePassengerIndex(idx)}
                  className={`px-3.5 py-2 rounded-xl text-xs font-mono font-medium flex items-center gap-2 transition-all cursor-pointer whitespace-nowrap ${
                    isSelected
                      ? 'bg-[var(--primary)] text-white shadow-xs'
                      : 'bg-[var(--surface-muted)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  <Users className="w-3.5 h-3.5" />
                  <span>
                    Passenger {idx + 1}
                    {idx === 0 ? ' (Lead)' : ''}
                  </span>
                  {hasTabError ? (
                    <span className="w-2 h-2 rounded-full bg-red-400" />
                  ) : isFilled ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  ) : null}
                </button>
              );
            })}
          </div>
        )}

        {/* Active Passenger Form */}
        <form onSubmit={handleSave} className="space-y-5 overflow-y-auto pr-1">
          <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/40 border border-[var(--border)] space-y-4">
            {/* Section heading & Lead traveler indicator */}
            <div className="flex flex-wrap items-center justify-between gap-2 pb-1 border-b border-[var(--border)]/40">
              <span className="text-xs font-mono font-bold uppercase tracking-wider text-[var(--text-primary)] flex items-center gap-1.5 min-w-0">
                <User className="w-4 h-4 text-[var(--primary)] shrink-0" />
                <span className="truncate">
                  Passenger {activePassengerIndex + 1}
                  {activePassengerIndex === 0 ? ' — Primary Booker & Lead Passenger' : ''}
                </span>
              </span>
              <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-[var(--surface)] border border-[var(--border)] text-[var(--text-muted)] shrink-0">
                {activePassengerIndex === 0 ? 'Lead Traveler' : `Companion ${activePassengerIndex}`}
              </span>
            </div>

            {/* Row 1: Title (2 cols), Given Name (5 cols), Family Name (5 cols) */}
            <div className="grid grid-cols-1 sm:grid-cols-12 gap-3.5">
              <div className="sm:col-span-2 space-y-1.5">
                <label className="text-[11px] font-mono uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-1 select-none whitespace-nowrap">
                  Title <span className="text-red-400 font-semibold">*</span>
                </label>
                <select
                  value={currentPassenger.title || 'mr'}
                  onChange={(e) => handleFieldChange(activePassengerIndex, 'title', e.target.value)}
                  className="w-full h-10 px-3 py-2 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-xs focus:outline-hidden focus:border-[var(--primary)]"
                >
                  <option value="mr">Mr</option>
                  <option value="mrs">Mrs</option>
                  <option value="ms">Ms</option>
                  <option value="miss">Miss</option>
                  <option value="dr">Dr</option>
                  <option value="mx">Mx</option>
                </select>
              </div>

              <div className="sm:col-span-5 space-y-1.5">
                <label className="text-[11px] font-mono uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-1 select-none whitespace-nowrap">
                  First / Given Name <span className="text-red-400 font-semibold">*</span>
                </label>
                <input
                  type="text"
                  placeholder="e.g. Rahul"
                  value={currentPassenger.given_name || ''}
                  onChange={(e) => handleFieldChange(activePassengerIndex, 'given_name', e.target.value)}
                  className={`w-full h-10 px-3 py-2 rounded-xl border text-xs focus:outline-hidden ${
                    currentErrors.given_name
                      ? 'border-red-500 bg-red-500/5 text-[var(--text-primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] focus:border-[var(--primary)]'
                  }`}
                />
                {currentErrors.given_name && (
                  <span className="text-[10px] text-red-400 block">{currentErrors.given_name}</span>
                )}
              </div>

              <div className="sm:col-span-5 space-y-1.5">
                <label className="text-[11px] font-mono uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-1 select-none whitespace-nowrap">
                  Last / Family Name <span className="text-red-400 font-semibold">*</span>
                </label>
                <input
                  type="text"
                  placeholder="e.g. Sharma"
                  value={currentPassenger.family_name || ''}
                  onChange={(e) => handleFieldChange(activePassengerIndex, 'family_name', e.target.value)}
                  className={`w-full h-10 px-3 py-2 rounded-xl border text-xs focus:outline-hidden ${
                    currentErrors.family_name
                      ? 'border-red-500 bg-red-500/5 text-[var(--text-primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] focus:border-[var(--primary)]'
                  }`}
                />
                {currentErrors.family_name && (
                  <span className="text-[10px] text-red-400 block">{currentErrors.family_name}</span>
                )}
              </div>
            </div>

            {/* Row 2: Date of Birth (6 cols) & Gender (6 cols) */}
            <div className="grid grid-cols-1 sm:grid-cols-12 gap-3.5">
              <div className="sm:col-span-6 space-y-1.5">
                <label className="text-[11px] font-mono uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-1.5 select-none whitespace-nowrap">
                  <Calendar className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
                  Date of Birth (YYYY-MM-DD) <span className="text-red-400 font-semibold">*</span>
                </label>
                <input
                  type="date"
                  value={currentPassenger.born_on || ''}
                  onChange={(e) => handleFieldChange(activePassengerIndex, 'born_on', e.target.value)}
                  className={`w-full h-10 px-3 py-2 rounded-xl border text-xs focus:outline-hidden ${
                    currentErrors.born_on
                      ? 'border-red-500 bg-red-500/5 text-[var(--text-primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] focus:border-[var(--primary)]'
                  }`}
                />
                {currentErrors.born_on && (
                  <span className="text-[10px] text-red-400 block">{currentErrors.born_on}</span>
                )}
              </div>

              <div className="sm:col-span-6 space-y-1.5">
                <label className="text-[11px] font-mono uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-1 select-none whitespace-nowrap">
                  Gender <span className="text-red-400 font-semibold">*</span>
                </label>
                <div className="grid grid-cols-3 gap-2 h-10">
                  {[
                    { id: 'm', label: 'Male' },
                    { id: 'f', label: 'Female' },
                    { id: 'x', label: 'Other' },
                  ].map((g) => (
                    <button
                      key={g.id}
                      type="button"
                      onClick={() => handleFieldChange(activePassengerIndex, 'gender', g.id)}
                      className={`h-10 flex items-center justify-center rounded-xl border text-xs font-mono transition-all cursor-pointer ${
                        currentPassenger.gender === g.id
                          ? 'border-[var(--primary)] bg-[var(--primary-soft)]/20 font-semibold text-[var(--primary)]'
                          : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {g.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Row 3: Email (6 cols) & Phone (6 cols) (Mandatory for Passenger 1, optional for companions) */}
            <div className="grid grid-cols-1 sm:grid-cols-12 gap-3.5 pt-2 border-t border-[var(--border)]/60">
              <div className="sm:col-span-6 space-y-1.5">
                <label className="text-[11px] font-mono uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-1.5 select-none whitespace-nowrap">
                  <Mail className="w-3.5 h-3.5 text-[var(--text-muted)] shrink-0" />
                  Contact Email {activePassengerIndex === 0 ? <span className="text-red-400 font-semibold">*</span> : <span className="text-[var(--text-muted)] font-normal lowercase">(optional)</span>}
                </label>
                <input
                  type="email"
                  placeholder="traveler@example.com"
                  value={currentPassenger.email || ''}
                  onChange={(e) => handleFieldChange(activePassengerIndex, 'email', e.target.value)}
                  className={`w-full h-10 px-3 py-2 rounded-xl border text-xs focus:outline-hidden ${
                    currentErrors.email
                      ? 'border-red-500 bg-red-500/5 text-[var(--text-primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] focus:border-[var(--primary)]'
                  }`}
                />
                {currentErrors.email && (
                  <span className="text-[10px] text-red-400 block">{currentErrors.email}</span>
                )}
                {activePassengerIndex === 0 && (
                  <span className="text-[10px] text-[var(--text-muted)] block">
                    Tickets and property booking confirmations will be sent here.
                  </span>
                )}
              </div>

              <div className="sm:col-span-6 space-y-1.5">
                <label className="text-[11px] font-mono uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-1.5 select-none whitespace-nowrap">
                  <Phone className="w-3.5 h-3.5 text-[var(--text-muted)] shrink-0" />
                  Contact Phone {activePassengerIndex === 0 ? <span className="text-red-400 font-semibold">*</span> : <span className="text-[var(--text-muted)] font-normal lowercase">(optional)</span>}
                </label>
                <input
                  type="tel"
                  placeholder="+91 98765 43210"
                  value={currentPassenger.phone_number || ''}
                  onChange={(e) => handleFieldChange(activePassengerIndex, 'phone_number', e.target.value)}
                  className={`w-full h-10 px-3 py-2 rounded-xl border text-xs focus:outline-hidden ${
                    currentErrors.phone_number
                      ? 'border-red-500 bg-red-500/5 text-[var(--text-primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] focus:border-[var(--primary)]'
                  }`}
                />
                {currentErrors.phone_number && (
                  <span className="text-[10px] text-red-400 block">{currentErrors.phone_number}</span>
                )}
                {activePassengerIndex === 0 && (
                  <span className="text-[10px] text-[var(--text-muted)] block">
                    Operational flight notifications and schedule updates will be sent here.
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2 shrink-0">
            <div className="flex items-center gap-2">
              {activePassengerIndex > 0 && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setActivePassengerIndex((prev) => prev - 1)}
                >
                  Previous Passenger
                </Button>
              )}
              {activePassengerIndex < formData.length - 1 && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setActivePassengerIndex((prev) => prev + 1)}
                >
                  Next Passenger
                </Button>
              )}
            </div>

            <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
              <Button type="button" variant="outline" size="md" onClick={onClose}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                size="md"
                rightIcon={<ArrowRight className="w-4 h-4" />}
                className="shadow-xs"
              >
                Save Travelers ({formData.length})
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
