import React from 'react';
import { Compass, ArrowRight } from 'lucide-react';
import { Button } from '../common/Button';

interface EmptyStateProps {
  title?: string;
  description?: string;
  actionText?: string;
  onAction?: () => void;
  icon?: React.ReactNode;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title = 'Your next journey starts here.',
  description = 'Create a trip and let TrustTrip help you plan the details with verified timelines and intelligence.',
  actionText = 'Create your first trip',
  onAction,
  icon,
}) => {
  return (
    <div className="py-16 px-6 text-center max-w-lg mx-auto bg-[var(--surface)] rounded-2xl border border-[var(--border)] shadow-xs">
      <div className="w-14 h-14 mx-auto mb-4 rounded-2xl bg-[var(--primary-soft)] text-[var(--primary)] flex items-center justify-center">
        {icon || <Compass className="w-7 h-7" />}
      </div>
      <h3 className="text-xl font-bold text-[var(--text-primary)] font-editorial tracking-tight">
        {title}
      </h3>
      <p className="text-xs sm:text-sm text-[var(--text-secondary)] mt-2 leading-relaxed">
        {description}
      </p>
      {onAction && (
        <div className="mt-6">
          <Button
            variant="primary"
            size="md"
            onClick={onAction}
            rightIcon={<ArrowRight className="w-4 h-4" />}
          >
            {actionText}
          </Button>
        </div>
      )}
    </div>
  );
};
