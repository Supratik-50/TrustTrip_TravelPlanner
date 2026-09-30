import React, { useState } from 'react';
import { Compass, ArrowRight, Sparkles, MapPin } from 'lucide-react';
import { INDIA_TRAVEL_STORIES, IndiaTravelStory } from '../../lib/destinationImages';

interface IndiaTravelStoriesProps {
  onSelectStory: (destination: string) => void;
}

export const IndiaTravelStories: React.FC<IndiaTravelStoriesProps> = ({ onSelectStory }) => {
  const [failedImages, setFailedImages] = useState<Record<string, boolean>>({});

  const handleImageError = (id: string) => {
    setFailedImages((prev) => ({ ...prev, [id]: true }));
  };

  return (
    <section className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-2">
        <div>
          <div className="text-xs font-semibold text-[var(--primary)] uppercase tracking-wider font-mono flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-[var(--coral)]" />
            <span>Editorial Visual Inspiration</span>
          </div>
          <h2 className="text-2xl sm:text-3xl font-bold text-[var(--text-primary)] font-editorial tracking-tight mt-1">
            Journeys Across India
          </h2>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] mt-0.5">
            Story-led travel ideas shaped around unhurried pacing, living culture, and regional geography.
          </p>
        </div>
      </div>

      {/* Grid of Editorial Stories */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {INDIA_TRAVEL_STORIES.map((story: IndiaTravelStory) => {
          const hasError = failedImages[story.id];

          return (
            <div
              key={story.id}
              onClick={() => onSelectStory(story.destination)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === 'Enter' && onSelectStory(story.destination)}
              className="group flex flex-col rounded-3xl overflow-hidden border border-[var(--border)] bg-[var(--surface)] shadow-xs hover:shadow-md transition-all duration-300 cursor-pointer text-left"
            >
              {/* Image Container with scrim and tag */}
              <div className="relative h-56 w-full overflow-hidden bg-[var(--background)]">
                {!hasError ? (
                  <img
                    src={story.imageUrl}
                    alt={story.title}
                    onError={() => handleImageError(story.id)}
                    className="w-full h-full object-cover object-center group-hover:scale-105 transition-transform duration-700 ease-out"
                    loading="lazy"
                    referrerPolicy="no-referrer"
                  />
                ) : (
                  <div className="w-full h-full bg-gradient-to-tr from-[#16272B] via-[#2C7A78] to-[#C96F52] flex flex-col items-center justify-center p-4 text-white text-center">
                    <MapPin className="w-6 h-6 text-[#E7A07F] mb-1" />
                    <span className="font-editorial text-lg font-bold">{story.title}</span>
                  </div>
                )}

                {/* Subtle Contrast Scrim */}
                <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/25 to-transparent group-hover:from-black/85 transition-colors" />

                {/* Editorial Tag Overlay */}
                <div className="absolute top-4 left-4 z-10">
                  <span className="text-[10px] font-mono font-medium tracking-wider uppercase text-white bg-black/40 backdrop-blur-md px-2.5 py-1 rounded-full border border-white/15">
                    {story.tag}
                  </span>
                </div>

                {/* Bottom Overlay Title on Image */}
                <div className="absolute bottom-4 left-4 right-4 z-10">
                  <h3 className="text-xl font-bold font-editorial text-white leading-tight drop-shadow-xs group-hover:text-[#E7A07F] transition-colors">
                    {story.title}
                  </h3>
                  <div className="text-xs text-white/90 font-light mt-0.5">
                    {story.subtitle}
                  </div>
                </div>
              </div>

              {/* Card Body */}
              <div className="p-5 flex-1 flex flex-col justify-between space-y-4">
                <p className="text-xs text-[var(--text-secondary)] font-light leading-relaxed">
                  {story.description}
                </p>

                <div className="pt-2 border-t border-[var(--border-subtle)] flex items-center justify-between text-xs font-semibold text-[var(--primary)] group-hover:text-[var(--primary-hover)]">
                  <span>Explore this route</span>
                  <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 transition-transform" />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
};
