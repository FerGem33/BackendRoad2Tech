"""Cached catalog lookups and tolerant name -> id resolution for tool parameters."""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from functools import cached_property

from ..data.repository import Category, DataRepository, Product, Store, Supplier, SupplierOffer


class NotFoundError(LookupError):
    """A store, product or draft that does not exist (mapped to HTTP 404)."""


# Common Spanish shopper words -> catalog (English) vocabulary, so "cerveza" finds beer.
SYNONYMS = {
    "cerveza": "beer",
    "cervezas": "beer",
    "refresco": "soda",
    "refrescos": "soda",
    "agua": "water",
    "cafe": "coffee",
    "papas": "chips",
    "botana": "snack",
    "botanas": "snacks",
    "hielo": "ice",
    "leche": "milk",
    "pan": "bread",
    "cigarros": "cigarettes",
    "cigarro": "cigarettes",
    "dulces": "candy",
    "dulce": "candy",
    "helado": "ice cream",
    "helados": "ice cream",
    "energetica": "energy",
    "energeticas": "energy",
    "flores": "flowers",
    "fruta": "fruit",
    "frutas": "fruit",
    "yogur": "yogurt",
    "jugo": "juice",
    "jugos": "juice",
    "galletas": "cookies",
    "chocolate": "chocolate",
    "sopa": "soup",
    "rosas": "roses",
    "tamales": "tamale",
    "rosca": "three kings",
    "platano": "banana",
    "manzana": "apple",
}

# Extra search words per category for items whose names do not say what they are (e.g. beer brands).
CATEGORY_TAGS = {
    "C01": "soda drink beverage",
    "C02": "beer alcohol drink",
    "C03": "snack",
    "C04": "candy sweet",
    "C05": "food meal lunch",
    "C06": "coffee hot drink",
    "C07": "dairy",
    "C08": "bread bakery sweet",
    "C10": "frozen ice cream",
    "C11": "ice",
    "C12": "cigarettes tobacco",
    "C18": "fruit fresh",
    "C20": "flowers gift",
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass
class Catalog:
    repo: DataRepository

    @cached_property
    def stores(self) -> dict[str, Store]:
        return {s.store_id: s for s in self.repo.stores()}

    @cached_property
    def products(self) -> dict[str, Product]:
        return {p.product_id: p for p in self.repo.products()}

    @cached_property
    def categories(self) -> dict[str, Category]:
        return {c.category_id: c for c in self.repo.categories()}

    @cached_property
    def suppliers(self) -> dict[str, Supplier]:
        return {s.supplier_id: s for s in self.repo.suppliers()}

    @cached_property
    def offers_by_product(self) -> dict[str, list[SupplierOffer]]:
        grouped: dict[str, list[SupplierOffer]] = {}
        for offer in self.repo.offers():
            grouped.setdefault(offer.product_id, []).append(offer)
        return grouped

    def category_of(self, product_id: str) -> Category:
        return self.categories[self.products[product_id].category_id]

    def unit_cost(self, product_id: str, supplier_id: str | None = None) -> float:
        """Cost of the supplier that delivered the latest lot, else the cheapest offer."""
        offers = self.offers_by_product.get(product_id, [])
        if supplier_id:
            for offer in offers:
                if offer.supplier_id == supplier_id:
                    return offer.unit_cost
        if not offers:
            return round(self.products[product_id].retail_price * 0.7, 2)
        return min(o.unit_cost for o in offers)

    # ----------------------------------------------------------- resolution
    def resolve_store(self, value: str | None) -> Store:
        if not value:
            raise NotFoundError("A store is required (for example S001 or 'OXXO Mirasierra').")
        key = value.strip().upper()
        if key in self.stores:
            return self.stores[key]
        match = re.fullmatch(r"S?0*(\d{1,3})", key)
        if match:
            candidate = f"S{int(match.group(1)):03d}"
            if candidate in self.stores:
                return self.stores[candidate]
        wanted = _normalize(value).removeprefix("oxxo ").strip()
        names = {_normalize(s.store_name).removeprefix("oxxo ").strip(): s for s in self.stores.values()}
        names.update({_normalize(s.colonia): s for s in self.stores.values()})
        for name, store in names.items():
            if wanted and (wanted == name or wanted in name):
                return store
        close = difflib.get_close_matches(wanted, list(names), n=1, cutoff=0.6)
        if close:
            return names[close[0]]
        raise NotFoundError(f"Store '{value}' was not found. Use an id between S001 and S025.")

    def resolve_product(self, value: str | None) -> Product:
        if not value:
            raise NotFoundError("A product is required (for example P001 or 'Coca-Cola 600 ml').")
        key = value.strip().upper()
        if key in self.products:
            return self.products[key]
        match = re.fullmatch(r"P?0*(\d{1,3})", key)
        if match:
            candidate = f"P{int(match.group(1)):03d}"
            if candidate in self.products:
                return self.products[candidate]
        ranked = self.search_products(value, limit=1)
        if ranked:
            return ranked[0]
        raise NotFoundError(f"Product '{value}' was not found in the catalog.")

    def search_products(self, query: str, limit: int = 8, require_word_match: bool = False) -> list[Product]:
        """Rank products by word hits in name/brand/category plus fuzzy similarity of the name."""
        wanted = " ".join(SYNONYMS.get(token, token) for token in _normalize(query).split())
        if not wanted:
            return []
        tokens = wanted.split()
        scored: list[tuple[float, Product]] = []
        for product in self.products.values():
            category = self.categories[product.category_id].category_name
            tags = CATEGORY_TAGS.get(product.category_id, "")
            haystack = _normalize(f"{product.product_name} {product.brand} {category} {tags}")
            name = _normalize(product.product_name)
            words = haystack.split()
            hits = sum(1 for t in tokens if any(w == t or (len(t) >= 3 and w.startswith(t)) for w in words))
            ratio = difflib.SequenceMatcher(None, wanted, name).ratio()
            popularity = 0.15 * min(1.0, product.base_daily_demand / 30)  # prefer the common pack size
            score = hits / len(tokens) + ratio * 0.5 + (1.0 if wanted in name else 0.0) + popularity
            if hits or (ratio >= 0.6 and not require_word_match):
                scored.append((score, product))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [product for _, product in scored[:limit]]
