/**
 * Client-side real-time filtering and sorting for cached daily scan catalogs.
 * Mirrors backend/main.py matching rules so refining filters updates results
 * immediately without re-scraping sellers.
 */

const EPSILON = 1e-6;
const NON_CAD_CURRENCY_RX = /\b(?:USD|GBP|EUR|AUD|NZD)\b|US\$|AU\$|NZ\$|[£€]/i;
const CAD_AMOUNT_RX = /(?:CAD\s*\$?|(?:CA|C)?\$)?\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)\s*(?:CAD)?/i;

export const TOPS_FILTER_RANGES = [
  { key: 'p2p', label: 'Pit to pit', min: 'p2pMin', max: 'p2pMax' },
  { key: 'length', label: 'Length', min: 'lengthMin', max: 'lengthMax' },
];

export const BOTTOMS_FILTER_RANGES = [
  { key: 'waist', label: 'Waist', min: 'waistMin', max: 'waistMax' },
  { key: 'inseam', label: 'Inseam', min: 'inseamMin', max: 'inseamMax' },
  { key: 'rise', label: 'Rise', min: 'riseMin', max: 'riseMax' },
  { key: 'legOpening', label: 'Leg opening', min: 'legOpeningMin', max: 'legOpeningMax' },
];

const toFinitePositive = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const num = Number(value);
  return Number.isFinite(num) && num > 0 ? num : null;
};

const readBoundNumber = (value) => {
  const raw = String(value ?? '').trim();
  if (!raw) return null;
  const num = Number(raw);
  return Number.isFinite(num) && num >= 0 && num <= 1000 ? num : null;
};

export const readPriceCapValue = (value) => {
  const raw = String(value ?? '').trim();
  if (!raw) return null;
  if (!/^\d*(?:\.\d{0,2})?$/.test(raw)) return null;
  const num = Number(raw);
  return Number.isFinite(num) && num >= 0 && num <= 10000 ? num : null;
};

export function parsePriceCad(item) {
  if (typeof item?.priceCad === 'number' && Number.isFinite(item.priceCad)) {
    return item.priceCad;
  }
  const currency = String(item?.priceCurrency || '').trim().toUpperCase();
  if (currency && currency !== 'CAD') {
    return null;
  }
  const raw = String(item?.price ?? '').trim();
  if (!raw || NON_CAD_CURRENCY_RX.test(raw)) {
    return null;
  }
  const match = CAD_AMOUNT_RX.exec(raw);
  if (!match) return null;
  const amount = Number(match[1].replace(/,/g, ''));
  return Number.isFinite(amount) ? amount : null;
}

export function parseFootwearSize(item) {
  if (typeof item?.sizeValue === 'number' && Number.isFinite(item.sizeValue)) {
    return item.sizeValue;
  }
  const text = String(item?.sizeLabel || '').trim().toUpperCase();
  if (!text) return null;
  let match = /\bUS\s*(\d+(?:\.\d+)?)\b/.exec(text);
  if (!match) {
    if (/\b(?:UK|EU|EUR|IT|FR|JP|CM)\b/.test(text)) return null;
    match = /\b(\d+(?:\.\d+)?)\b/.exec(text);
  }
  if (!match) return null;
  const num = Number(match[1]);
  return Number.isFinite(num) ? num : null;
}

export function matchesItemKeyword(item, keyword) {
  const tokens = String(keyword || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return true;
  const haystack = [
    item?.title,
    item?.sizeLabel,
    item?.seller,
    item?.url,
    item?.price,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return tokens.every((token) => haystack.includes(token));
}

const getRangeBounds = (filters, minKey, maxKey, { ordered = false } = {}) => {
  const lower = readBoundNumber(filters?.[minKey]);
  const upper = readBoundNumber(filters?.[maxKey]);
  if (ordered) {
    if (lower === null || upper === null || lower > upper) return null;
    return [lower, upper];
  }
  if (lower !== null && upper !== null) {
    return [Math.min(lower, upper), Math.max(lower, upper)];
  }
  if (lower !== null || upper !== null) {
    return [lower, upper];
  }
  return null;
};

const inBounds = (value, bounds) => {
  if (!bounds) return true;
  const [lower, upper] = bounds;
  if (lower !== null && value < lower - EPSILON) return false;
  if (upper !== null && value > upper + EPSILON) return false;
  return true;
};

export function matchesItemFilters(item, page, filters, options = {}) {
  if (!item) return false;
  const { isRetailer = false, keyword = '' } = options;

  if (!matchesItemKeyword(item, keyword)) {
    return false;
  }

  const maxPriceCad = readPriceCapValue(filters?.maxPriceCad);
  if (maxPriceCad !== null) {
    const priceCad = parsePriceCad(item);
    if (priceCad === null || priceCad > maxPriceCad + EPSILON) {
      return false;
    }
  }

  if (!page) return true;

  if (page.mode === 'measurements') {
    const p2pBounds = getRangeBounds(filters, 'p2pMin', 'p2pMax', { ordered: true });
    const lengthBounds = getRangeBounds(filters, 'lengthMin', 'lengthMax', { ordered: true });
    if (!p2pBounds || !lengthBounds) return false;

    const p2p = toFinitePositive(item.p2p);
    const length = toFinitePositive(item.length);
    if (isRetailer) {
      return p2p !== null && length !== null && inBounds(p2p, p2pBounds) && inBounds(length, lengthBounds);
    }
    let matchedAny = false;
    if (p2p !== null) {
      matchedAny = true;
      if (!inBounds(p2p, p2pBounds)) return false;
    }
    if (length !== null) {
      matchedAny = true;
      if (!inBounds(length, lengthBounds)) return false;
    }
    return matchedAny;
  }

  if (page.mode === 'bottomMeasurements') {
    const waist = toFinitePositive(item.waist);
    if (isRetailer && waist === null) return false;
    let matchedAny = false;
    for (const { key, min, max } of BOTTOMS_FILTER_RANGES) {
      const bounds = getRangeBounds(filters, min, max);
      if (!bounds) continue;
      const value = toFinitePositive(item[key]);
      if (value === null) continue;
      matchedAny = true;
      if (!inBounds(value, bounds)) return false;
    }
    return matchedAny;
  }

  if (page.mode === 'sizeRange') {
    const bounds = getRangeBounds(filters, 'min', 'max');
    const sizeValue = parseFootwearSize(item);
    if (sizeValue === null) return false;
    return inBounds(sizeValue, bounds);
  }

  return true;
}

export function filterRowResults(row, page, filters, options = {}) {
  const { isRetailer = false, keyword = '' } = options;
  const catalog = Array.isArray(row?.catalog) ? row.catalog : [];
  if (row?.hasCatalog || catalog.length > 0) {
    return catalog.filter((item) => matchesItemFilters(item, page, filters, { isRetailer, keyword }));
  }
  const legacyResults = Array.isArray(row?.results) ? row.results : [];
  if (!keyword) return legacyResults;
  return legacyResults.filter((item) => matchesItemKeyword(item, keyword));
}

export function sortResultsList(items, sortBy = 'default') {
  if (!Array.isArray(items) || items.length <= 1 || sortBy === 'default') {
    return items || [];
  }
  const copy = items.map((item, index) => ({ item, index }));
  if (sortBy === 'price_asc') {
    copy.sort((a, b) => {
      const pa = parsePriceCad(a.item);
      const pb = parsePriceCad(b.item);
      if (pa !== null && pb !== null && pa !== pb) return pa - pb;
      if (pa !== null && pb === null) return -1;
      if (pa === null && pb !== null) return 1;
      return a.index - b.index;
    });
  } else if (sortBy === 'newest') {
    copy.sort((a, b) => {
      const aa = Number(a.item?.ageDays);
      const ab = Number(b.item?.ageDays);
      const hasA = Number.isFinite(aa);
      const hasB = Number.isFinite(ab);
      if (hasA && hasB && aa !== ab) return aa - ab;
      if (hasA && !hasB) return -1;
      if (!hasA && hasB) return 1;
      return a.index - b.index;
    });
  }
  return copy.map((entry) => entry.item);
}

export function sortSellerEntriesByHits(entries) {
  return [...entries].sort((a, b) => {
    const hitDiff = b.displayedResults.length - a.displayedResults.length;
    if (hitDiff !== 0) return hitDiff;
    return a.originalIndex - b.originalIndex;
  });
}
