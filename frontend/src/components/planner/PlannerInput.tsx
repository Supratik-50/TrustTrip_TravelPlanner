import React, { useState } from 'react';
import { Send, Sparkles, Compass, Utensils, Landmark, TreePine, ShoppingBag, Moon } from 'lucide-react';
import { Button } from '../common/Button';
import { TripDetail } from '../../types/trip';
import { MapPreview } from '../travel/MapPreview';

interface PlannerInputProps {
  trip: TripDetail;
  onSubmitPrompt: (prompt: string) => void;
  isLoading: boolean;
}

export const PlannerInput: React.FC<PlannerInputProps> = ({
  trip,
  onSubmitPrompt,
  isLoading,
}) => {
  const [prompt, setPrompt] = useState(
    'Relaxed days, exceptional authentic regional food, quiet neighborhood walks, historic gardens, and one memorable evening river stroll...'
  );

  const styleSuggestions = [
    { label: 'Food & Wine', icon: <Utensils className="w-3 h-3" /> },
    { label: 'Museums & Art', icon: <Landmark className="w-3 h-3" /> },
    { label: 'Nature & Walks', icon: <TreePine className="w-3 h-3" /> },
    { label: 'Artisan Markets', icon: <ShoppingBag className="w-3 h-3" /> },
    { label: 'Quiet Evenings', icon: <Moon className="w-3 h-3" /> },
  ];

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || isLoading) return;
    onSubmitPrompt(prompt.trim());
  };

  const appendSuggestion = (label: string) => {
    setPrompt((prev) => `${prev.trim()} Also weave in ${label.toLowerCase()}.`);
  };

  return (
    <div className="space-y-6">
      {/* Editorial Travel Idea Card */}
      <form
        onSubmit={handleSubmit}
        className="rounded-3xl border border-[var(--border)] bg-[var(--surface)] p-6 sm:p-7 shadow-xs space-y-5"
      >
        <div className="space-y-1">
          <div className="text-xs font-mono font-semibold uppercase tracking-wider text-[var(--primary)] flex items-center gap-1.5">
            <Compass className="w-3.5 h-3.5" />
            <span>Trip Philosophy & Pace</span>
          </div>
          <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
            What kind of journey are you imagining?
          </h3>
          <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
            Tell TrustTrip how you want your days to feel. We will balance timing, dining,
            and neighborhood proximity.
          </p>
        </div>

        {/* Natural Narrative Input */}
        <div className="relative">
          <textarea
            rows={4}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Relaxed days, great food, museums, beautiful neighborhoods and one special day trip..."
            className="w-full text-sm sm:text-base p-4 rounded-2xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]/50 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] resize-none leading-relaxed font-light"
          />
        </div>

        {/* Visual Travel Suggestions */}
        <div className="space-y-2">
          <span className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider">
            You might want to include:
          </span>
          <div className="flex flex-wrap gap-2">
            {styleSuggestions.map((item) => (
              <button
                key={item.label}
                type="button"
                onClick={() => appendSuggestion(item.label)}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-[var(--background)] hover:bg-[var(--primary-soft)] text-[var(--text-primary)] hover:text-[var(--primary)] border border-[var(--border)] transition-colors cursor-pointer"
              >
                {item.icon}
                <span>+ {item.label}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="pt-2">
          <Button
            type="submit"
            variant="accent"
            size="lg"
            isLoading={isLoading}
            className="w-full shadow-md font-semibold"
            rightIcon={<Send className="w-4 h-4" />}
          >
            {trip.plan ? 'Re-shape Travel Itinerary' : 'Compose Travel Itinerary'}
          </Button>
        </div>
      </form>

      {/* Subtle Map & Location Context */}
      <MapPreview destination={trip.destination} />

      {/* Travel Weather Context Placeholder */}
      <div className="p-4 rounded-2xl bg-[var(--surface)] border border-[var(--border)] space-y-1.5">
        <div className="text-[11px] font-mono text-[var(--text-secondary)] uppercase tracking-wider">
          Seasonality & Atmosphere
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="font-medium text-[var(--text-primary)]">
            {trip.destination} Travel Climate
          </span>
          <span className="text-[var(--primary)] font-semibold">
            Autumn Season
          </span>
        </div>
        <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed">
          Mild daytime temperatures, crisp evenings. Comfortable walking attire is recommended.
        </p>
      </div>
    </div>
  );
};
