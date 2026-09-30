import React, { useState } from 'react';
import {
  Star,
  MapPin,
  CheckCircle2,
  ShieldCheck,
  Coffee,
  Wifi,
  Sparkles,
  Bed,
  Users,
  ChevronDown,
  ChevronUp,
  ArrowRight,
} from 'lucide-react';
import { HotelOffer, HotelRoom } from '../../types/hotel';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';

interface HotelCardProps {
  hotel: HotelOffer;
  isSelected?: boolean;
  onSelect?: (hotel: HotelOffer, selectedRoom?: HotelRoom) => void;
  isSelecting?: boolean;
  onPlanTripWithHotel?: (hotel: HotelOffer) => void;
  nightsCount?: number;
}

export const HotelCard: React.FC<HotelCardProps> = ({
  hotel,
  isSelected = false,
  onSelect,
  isSelecting = false,
  onPlanTripWithHotel,
  nightsCount = 5,
}) => {
  const [selectedRoomId, setSelectedRoomId] = useState<string>(
    hotel.rooms?.[0]?.id || ''
  );
  const [isRoomsExpanded, setIsRoomsExpanded] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);

  const selectedRoom =
    hotel.rooms?.find((r) => r.id === selectedRoomId) || hotel.rooms?.[0];

  const effectiveTotal = selectedRoom
    ? selectedRoom.price_per_night * nightsCount
    : hotel.total_amount;

  const effectiveNightly = selectedRoom
    ? selectedRoom.price_per_night
    : Math.round(hotel.total_amount / (nightsCount || 1));

  return (
    <div
      className={`rounded-3xl border transition-all duration-300 overflow-hidden ${
        isSelected
          ? 'bg-[var(--surface)] border-[var(--primary)] shadow-md ring-2 ring-[var(--primary)]/20'
          : 'bg-[var(--surface)] border-[var(--border)] hover:border-[var(--border-strong)] shadow-xs hover:shadow-md'
      }`}
    >
      <div className="grid grid-cols-1 md:grid-cols-12 gap-0">
        {/* Left: Curated Property Photo (5 cols) */}
        <div className="md:col-span-5 relative min-h-[220px] md:min-h-full bg-[var(--surface-muted)] overflow-hidden">
          {hotel.image_url && !imageFailed ? (
            <img
              src={hotel.image_url}
              alt={hotel.name}
              onError={() => setImageFailed(true)}
              className="w-full h-full object-cover min-h-[220px] max-h-[300px] md:max-h-none transition-transform duration-500 hover:scale-105"
            />
          ) : (
            <div className="w-full h-full min-h-[220px] flex flex-col items-center justify-center p-6 text-center bg-gradient-to-br from-[var(--surface-muted)] to-[var(--sky-tint)] text-[var(--primary)]">
              <Sparkles className="w-8 h-8 mb-2 opacity-60 text-[var(--terracotta)]" />
              <span className="font-editorial text-lg font-bold text-[var(--text-primary)]">
                {hotel.name}
              </span>
              <span className="text-xs text-[var(--text-secondary)] mt-1 font-mono">
                {hotel.city} Boutique Retreat
              </span>
            </div>
          )}

          {/* Star rating pill overlay */}
          <div className="absolute top-3 left-3 bg-black/60 backdrop-blur-md text-white px-2.5 py-1 rounded-full text-xs flex items-center gap-1 shadow-xs">
            <div className="flex items-center text-amber-400">
              {Array.from({ length: hotel.stars || 4 }).map((_, i) => (
                <Star key={i} className="w-3 h-3 fill-current" />
              ))}
            </div>
            <span className="font-semibold ml-0.5">{hotel.stars}-Star Stay</span>
          </div>

          {/* Review Score Badge overlay */}
          <div className="absolute bottom-3 right-3 bg-[var(--surface)]/95 backdrop-blur-md px-2.5 py-1 rounded-xl shadow-md border border-[var(--border)] flex items-center gap-2">
            <span className="text-xs font-bold text-[var(--text-primary)]">
              {hotel.rating >= 9.5 ? 'Exceptional' : hotel.rating >= 9.0 ? 'Superb' : 'Fabulous'}
            </span>
            <span className="px-1.5 py-0.5 rounded-lg bg-[var(--primary)] text-white text-xs font-bold font-mono">
              {hotel.rating.toFixed(1)}
            </span>
          </div>
        </div>

        {/* Right: Property Details & Room Options (7 cols) */}
        <div className="md:col-span-7 p-6 sm:p-7 flex flex-col justify-between space-y-5">
          {/* Header & Location */}
          <div className="space-y-2">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h4 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)] leading-snug">
                  {hotel.name}
                </h4>
                <div className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)] mt-1">
                  <MapPin className="w-3.5 h-3.5 text-[var(--terracotta)] shrink-0" />
                  <span className="truncate">{hotel.address}</span>
                </div>
              </div>

              {isSelected && (
                <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold bg-[var(--primary)] text-white shrink-0 shadow-xs">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Selected Stay
                </span>
              )}
            </div>

            {/* Cancellation Policy and Badges */}
            <div className="flex flex-wrap items-center gap-2 pt-1 text-xs">
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--status-success)]/10 text-[var(--status-success)] border border-[var(--status-success)]/20">
                <ShieldCheck className="w-3 h-3" />
                {hotel.cancellation_policy}
              </span>
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--surface-muted)] text-[var(--text-secondary)] border border-[var(--border)]">
                <Coffee className="w-3 h-3 text-[var(--primary)]" />
                Breakfast Included
              </span>
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--surface-muted)] text-[var(--text-secondary)] border border-[var(--border)]">
                <Wifi className="w-3 h-3 text-[var(--primary)]" />
                Complimentary Wi-Fi
              </span>
            </div>
          </div>

          {/* Active Room Choice Preview */}
          {selectedRoom && (
            <div className="p-3.5 rounded-2xl bg-[var(--surface-muted)]/50 border border-[var(--border)]/80 text-xs space-y-2">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <span className="font-semibold text-[var(--text-primary)] block text-sm">
                    {selectedRoom.name}
                  </span>
                  <p className="text-[11px] text-[var(--text-secondary)] line-clamp-2 mt-0.5">
                    {selectedRoom.description}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <span className="font-bold text-sm text-[var(--primary)] font-mono">
                    ${selectedRoom.price_per_night}
                  </span>
                  <span className="text-[10px] text-[var(--text-muted)] block">/ night</span>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-3 pt-1 text-[11px] text-[var(--text-secondary)]">
                <span className="flex items-center gap-1">
                  <Bed className="w-3 h-3 text-[var(--primary)]" />
                  {selectedRoom.beds}
                </span>
                <span className="flex items-center gap-1">
                  <Users className="w-3 h-3 text-[var(--primary)]" />
                  Up to {selectedRoom.capacity} Guests
                </span>
              </div>
            </div>
          )}

          {/* Available Rooms Accordion Toggle (if multiple rooms) */}
          {hotel.rooms && hotel.rooms.length > 1 && (
            <div className="pt-1">
              <button
                type="button"
                onClick={() => setIsRoomsExpanded(!isRoomsExpanded)}
                className="text-xs text-[var(--primary)] hover:text-[var(--primary-hover)] font-medium flex items-center gap-1 transition-colors"
              >
                {isRoomsExpanded
                  ? 'Hide Room Categories'
                  : `Compare All ${hotel.rooms.length} Room Categories`}
                {isRoomsExpanded ? (
                  <ChevronUp className="w-3.5 h-3.5" />
                ) : (
                  <ChevronDown className="w-3.5 h-3.5" />
                )}
              </button>

              {isRoomsExpanded && (
                <div className="mt-3 space-y-2 pt-2 border-t border-[var(--border)]">
                  {hotel.rooms.map((room) => (
                    <div
                      key={room.id}
                      onClick={() => setSelectedRoomId(room.id)}
                      className={`p-3 rounded-xl border text-xs cursor-pointer transition-all flex items-center justify-between gap-3 ${
                        selectedRoomId === room.id
                          ? 'border-[var(--primary)] bg-[var(--primary-soft)]/20 shadow-xs'
                          : 'border-[var(--border)] hover:bg-[var(--surface-muted)]'
                      }`}
                    >
                      <div className="space-y-0.5">
                        <div className="font-semibold text-[var(--text-primary)]">
                          {room.name}
                        </div>
                        <div className="text-[11px] text-[var(--text-muted)] flex items-center gap-2">
                          <span>{room.beds}</span> • <span>Max {room.capacity} guests</span>
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        <span className="font-bold text-sm text-[var(--text-primary)] font-mono">
                          {formatCurrency(room.price_per_night, hotel.currency)}
                        </span>
                        <span className="text-[10px] text-[var(--text-muted)] block">/ night</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Price Summary & Selection Actions */}
          <div className="pt-4 border-t border-[var(--border)] flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="space-y-0.5">
              <div className="flex items-baseline gap-1.5">
                <span className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
                  {formatCurrency(effectiveTotal, hotel.currency)}
                </span>
                <span className="text-xs text-[var(--text-secondary)] font-mono">
                  total
                </span>
              </div>
              <p className="text-[11px] text-[var(--text-muted)]">
                {formatCurrency(effectiveNightly, hotel.currency)} / night for {nightsCount} {nightsCount === 1 ? 'night' : 'nights'} stay
              </p>
            </div>

            <div className="flex items-center gap-2.5">
              {onSelect && (
                <Button
                  variant={isSelected ? 'outline' : 'primary'}
                  size="md"
                  onClick={() => onSelect(hotel, selectedRoom)}
                  isLoading={isSelecting}
                  disabled={isSelected}
                  leftIcon={isSelected ? <CheckCircle2 className="w-4 h-4 text-[var(--primary)]" /> : undefined}
                  className={isSelected ? 'border-[var(--primary)] text-[var(--primary)]' : ''}
                >
                  {isSelected ? 'Stay Active' : 'Select for Trip'}
                </Button>
              )}

              {onPlanTripWithHotel && !onSelect && (
                <Button
                  variant="accent"
                  size="md"
                  onClick={() => onPlanTripWithHotel(hotel)}
                  rightIcon={<ArrowRight className="w-4 h-4" />}
                >
                  Plan Trip with Stay
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
