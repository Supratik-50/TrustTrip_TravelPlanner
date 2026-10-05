from __future__ import annotations
import os
from typing import Any
from .hotel_provider import HotelProviderError
from .liteapi_provider import LiteAPIProvider
from .hotelbeds_provider import HotelbedsProvider
from services.currency_converter import CurrencyConverter, CurrencyConversionError

class HotelService:
    def __init__(self):
        self.converter = CurrencyConverter()
        self.rate_cache = {}

    def _providers(self):
        providers=[]
        for name in ("liteapi", "hotelbeds"):
            try:
                providers.append(LiteAPIProvider() if name == "liteapi" else HotelbedsProvider())
            except HotelProviderError:
                continue
        return providers

    def _inr(self, offer):
        amount=offer.get("price"); cur=str(offer.get("currency") or "").upper()
        if amount is None or not cur: return offer
        if cur == "INR":
            offer.update(price_inr=round(float(amount),2), exchange_rate=1.0, exchange_rate_date=None, price_inr_source="provider price (INR)")
            return offer
        try:
            r=self.rate_cache.get(cur) or self.converter.get_rate(cur,"INR"); self.rate_cache[cur]=r
            offer.update(price_inr=round(float(amount)*float(r["rate"]),2), exchange_rate=float(r["rate"]), exchange_rate_date=r.get("date"), price_inr_source="reference FX conversion")
        except (CurrencyConversionError, ValueError, TypeError, KeyError):
            offer.update(price_inr=None, exchange_rate=None, exchange_rate_date=None, price_inr_source="INR conversion unavailable")
        return offer

    def _score(self, offer, budget=None, cheaper=False):
        price=offer.get("price_inr")
        if price is None: return -1.0
        score=0.0
        if budget:
            budget=float(budget); score += 0.55 if price <= budget else max(0.0, 0.55*(1-(price-budget)/max(budget,1)))
        else: score += 0.35
        rating=offer.get("rating")
        if rating is not None:
            try:
                r=float(rating); score += 0.25*min(1.0,r/10 if r>5 else r/5)
            except: pass
        if offer.get("refundable"): score += 0.10
        if offer.get("board_name") and "breakfast" in str(offer.get("board_name")).lower(): score += 0.10
        if cheaper: score += max(0, 0.20*(1/(1+price/10000)))
        return score

    def search(self, request):
        all_offers=[]; errors=[]
        providers=self._providers()
        if not providers:
            errors.append("No hotel provider is configured. Add LiteAPI and/or Hotelbeds credentials to .env.")
        for provider in providers:
            try: all_offers.extend(provider.search(request))
            except Exception as exc: errors.append(f"{provider.name}: {exc}")
        for o in all_offers:
            self._inr(o); o["recommendation_score"]=round(self._score(o, request.get("budget_inr"), request.get("cheaper_mode",False)),4)
        all_offers=[o for o in all_offers if o.get("price") is not None]
        all_offers.sort(key=lambda x:(x.get("recommendation_score",-1), -(1 if x.get("refundable") else 0), -(float(x.get("rating") or 0)), -1/ max(float(x.get("price_inr") or 10**12),1)), reverse=True)
        for i,o in enumerate(all_offers[:20]): o["is_recommended"]=i==0; o["recommendation_rank"]=i+1
        return {"offers":all_offers[:20],"count":len(all_offers),"errors":errors,"providers_used":sorted(set(o.get("provider") for o in all_offers))}

    def _provider_for(self, offer):
        p=offer.get("provider")
        if p=="liteapi": return LiteAPIProvider()
        if p=="hotelbeds": return HotelbedsProvider()
        raise HotelProviderError("Unknown hotel provider.")

    def prebook(self, offer):
        return self._provider_for(offer).prebook(offer)

    def book(self, offer, prebook, holder, guests, client_reference):
        return self._provider_for(offer).book(prebook, holder, guests, client_reference)

    def reconcile(self, offer: dict[str, Any], client_reference: str) -> tuple[str, dict[str, Any] | None]:
        try:
            provider = self._provider_for(offer)
            if hasattr(provider, "reconcile"):
                return provider.reconcile(client_reference)
            return ("UNCERTAIN", None)
        except Exception:
            return ("UNCERTAIN", None)
