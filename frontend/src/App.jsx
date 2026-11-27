import { useEffect, useRef, useState } from 'react';
import { ArrowDownUp, ArrowUpRight, ChevronDown, Footprints, ImageOff, ListFilter, Plus, RotateCcw, ScanLine, Search, Shapes, Shirt, Square, UsersRound, X } from 'lucide-react';
import {
  cancelSearch,
  loadRetailSources,
  makeSearchId,
  startSearchBatch,
  streamSearch,
} from './hooks/useStream';
import {
  DEFAULT_FOLLOWING_ACCOUNTS,
  getSellerAdditionsAfterVersion,
  getSellerRemovalsAfterVersion,
  DEFAULT_SELLER_ADDITIONS_VERSION,
} from './data/defaultSellers';
import {
  CATEGORY_FILTER_DEFAULTS_SIGNATURE,
  CATEGORY_PAGES,
  CATEGORY_PAGE_MAP,
  DEFAULT_CATEGORY_PAGE_ID,
  getDefaultPageFilters,
  isCategoryPageId,
} from './data/categoryPages';
import { RETAIL_ACCOUNTS, RETAIL_BY_USERNAME, RETAIL_CATEGORIES } from './data/retailSources';
import './App.css';
import './index.css';

const FOLLOWING_STORAGE_KEY = 'debot.followingAccounts.v1';
const SELLER_ADDITIONS_STORAGE_KEY = 'debot.sellerAdditions.version';
const ACTIVE_PAGE_STORAGE_KEY = 'debot.categoryPage.v1';
const PAGE_FILTERS_STORAGE_KEY = 'debot.categoryFilters.v1';
const PAGE_FILTERS_VERSION_STORAGE_KEY = 'debot.categoryFilters.defaults.v1';
const PAGE_WORKSPACES_STORAGE_KEY = 'debot.pageWorkspaces.v1';
const LOW_PARSE_RETRY_RATIO = 0.9;
const MAX_LOW_PARSE_RETRIES = 1;

const CATEGORY_ICONS = { tops: Shirt, bottoms: ArrowDownUp, footwear: Footprints, accessories: Shapes };
const TOPS_RANGES = [
  { label: 'Pit to pit', min: 'p2pMin', max: 'p2pMax' },
  { label: 'Length', min: 'lengthMin', max: 'lengthMax' },
];
const BOTTOMS_RANGES = [
  { key: 'waist', label: 'Waist', min: 'waistMin', max: 'waistMax' },
  { key: 'inseam', label: 'Inseam', min: 'inseamMin', max: 'inseamMax' },
  { key: 'rise', label: 'Rise', min: 'riseMin', max: 'riseMax' },
  { key: 'legOpening', label: 'Leg opening', min: 'legOpeningMin', max: 'legOpeningMax' },
];
const LEGACY_BOTTOMS_RANGES = [
  ...BOTTOMS_RANGES.filter(({ key }) => key !== 'inseam' && key !== 'rise'),
  { key: 'inseamRise', min: 'inseamRiseMin', max: 'inseamRiseMax' },
];

const isLegacyBottomsSearch = (pageId, filters) =>
  pageId === 'bottoms' && (filters?.inseamRiseMin != null || filters?.inseamRiseMax != null);

const createProgressState = (overrides = {}) => ({
  processed: 0,
  total: 0,
  matches: 0,
  phase: 'idle',
  message: '',
  stopReason: null,
  retryAttempt: null,
  retryTotalAttempts: null,
  retryDelaySeconds: null,
  retryAvailableAt: null,
  ...overrides,
});

const getRetryCountdownSeconds = (progress, nowMs) => {
  const retryAtMs = Date.parse(progress?.retryAvailableAt || '');
  if (Number.isFinite(retryAtMs)) {
    return Math.max(0, Math.ceil((retryAtMs - nowMs) / 1000));
  }

  return progress?.retryDelaySeconds || 0;
};

const getSellerStateTone = (sellerRow) => {
  if (['queued', 'waiting_rate_limit'].includes(sellerRow?.progress?.phase)) {
    return 'queued';
  }

  if (sellerRow?.loading) {
    return 'loading';
  }

  if (sellerRow?.error) {
    return 'error';
  }

  if (sellerRow?.processed) {
    return 'complete';
  }

  return 'idle';
};

const getSellerStateLabel = (sellerRow) => {
  if (sellerRow?.progress?.phase === 'waiting_rate_limit') {
    return 'Cooldown';
  }
  if (sellerRow?.progress?.phase === 'queued') {
    return 'Queued';
  }

  if (sellerRow?.loading) {
    return 'Scanning';
  }

  if (sellerRow?.error) {
    return 'Issue';
  }

  if (sellerRow?.processed) {
    if (sellerRow?.progress?.stopReason === 'age_window') {
      return 'Age cutoff';
    }
    return (sellerRow?.results?.length || 0) > 0 ? 'Matched' : 'Complete';
  }

  return 'Ready';
};

const formatSellerStatusLabel = (sellerRow, queuePosition = null) => {
  const progress = sellerRow?.progress;
  const hits = Number(progress?.matches);
  const processed = Number(progress?.processed) || 0;
  const total = Number(progress?.total) || 0;
  const safeHits = Number.isFinite(hits) ? hits : (sellerRow?.results?.length || 0);

  if (progress?.phase === 'waiting_rate_limit') {
    return 'Waiting for Depop cooldown';
  }
  if (progress?.phase === 'queued') {
    return queuePosition ? `Queue position ${queuePosition}` : 'Waiting for the global queue';
  }

  if (sellerRow?.loading && processed === 0 && total === 0 && safeHits === 0) {
    return 'Collecting live listings';
  }

  return `${safeHits} matches • ${processed} parsed / ${total} collected`;
};

const getSellerProgressDetails = (sellerRow, nowMs, queuePosition = null) => {
  const progress = sellerRow?.progress;
  if (!progress) {
    return null;
  }

  if (['rate_limited', 'waiting_rate_limit'].includes(progress.phase) && progress.retryAvailableAt) {
    const secondsRemaining = getRetryCountdownSeconds(progress, nowMs);
    const duration = secondsRemaining >= 60
      ? `${Math.floor(secondsRemaining / 60)}m ${secondsRemaining % 60}s`
      : `${secondsRemaining}s`;
    const attempt = progress.retryAttempt || 1;
    const totalAttempts = progress.retryTotalAttempts || 1;
    return {
      primary: progress.phase === 'waiting_rate_limit'
        ? `Cooldown ${duration}`
        : `Cooldown ${duration} • retry ${attempt}/${totalAttempts}`,
      secondary: progress.message || 'Paused while retrying this seller.',
    };
  }

  if (progress.phase === 'queued') {
    return {
      primary: queuePosition ? `Queued • slot ${queuePosition}` : 'Queued',
      secondary: progress.message || 'Waiting for global cooldown to end.',
    };
  }

  if (progress.phase === 'done' && progress.stopReason === 'age_window') {
    return {
      primary: 'Listing-age filter applied',
      secondary: '',
    };
  }

  const message = progress.message;
  if (typeof message === 'string' && message.trim()) {
    return {
      primary: message.trim(),
      secondary: '',
    };
  }

  return null;
};

const normalizeSellerUsername = (value = '') => {
  let normalized = String(value || '').trim();
  if (!normalized) {
    return '';
  }

  normalized = normalized.replace(/^https?:\/\/(www\.)?depop\.com\//i, '');
  normalized = normalized.split('?')[0];
  normalized = normalized.replace(/^@+/, '');
  normalized = normalized.replace(/^\/+|\/+$/g, '');
  normalized = normalized.split('/')[0];

  return normalized.toLowerCase();
};

const normalizeSellerDisplayName = (value = '') => String(value || '').trim();

const normalizeSellerAccount = (account) => {
  const username = normalizeSellerUsername(account?.username ?? account?.seller ?? '');
  if (!username) {
    return null;
  }

  const name = normalizeSellerDisplayName(account?.name ?? account?.displayName ?? '');
  return {
    username,
    name: name || username,
  };
};

const dedupeSellerAccounts = (accounts) => {
  const seen = new Set();
  const deduped = [];

  for (const account of accounts) {
    const normalized = normalizeSellerAccount(account);
    if (!normalized || seen.has(normalized.username)) {
      continue;
    }

    seen.add(normalized.username);
    deduped.push(normalized);
  }

  return deduped;
};

const getDefaultSellerAccounts = () => dedupeSellerAccounts(DEFAULT_FOLLOWING_ACCOUNTS);

const currentResultDay = () => {
  const now = new Date();
  return `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
};

const createSellerRow = (account, existingRow = null) => ({
  seller: account.username,
  displayName: account.name || account.username,
  results: (existingRow?.results || []).filter((item) =>
    !item.seller || normalizeSellerUsername(item.seller) === account.username
  ),
  resultsDay: existingRow?.resultsDay || '',
  resultsFilters: existingRow?.resultsFilters || null,
  loading: existingRow?.loading || false,
  processed: existingRow?.processed || false,
  error: existingRow?.error || null,
  errorCode: existingRow?.errorCode || null,
  searchId: existingRow?.searchId || '',
  controller: existingRow?.controller || null,
  progress: existingRow?.progress || null,
  searchTask: existingRow?.searchTask || null,
});

const buildSellerRows = (accounts, existingRows = []) => {
  const existingByUsername = new Map(existingRows.map((row) => [row.seller, row]));
  return dedupeSellerAccounts(accounts).map((account) =>
    createSellerRow(account, existingByUsername.get(account.username))
  );
};

const buildPageWorkspaces = (accounts, existingWorkspaces = {}) =>
  Object.fromEntries(
    CATEGORY_PAGES.map((page) => [
      page.id,
      {
        sellerRows: buildSellerRows(
          RETAIL_CATEGORIES.has(page.id) ? [...RETAIL_ACCOUNTS, ...accounts] : accounts,
          existingWorkspaces[page.id]?.sellerRows || [],
        ),
      },
    ])
  );

const readStoredPageWorkspaces = (accounts) => {
  if (typeof window === 'undefined') {
    return buildPageWorkspaces(accounts);
  }

  try {
    const raw = window.localStorage.getItem(PAGE_WORKSPACES_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== 'object') {
      return buildPageWorkspaces(accounts);
    }

    const storedWorkspaces = Object.fromEntries(
      CATEGORY_PAGES.map((page) => {
        const rows = Array.isArray(parsed[page.id]?.sellerRows)
          ? parsed[page.id].sellerRows
          : [];
        const retailerRows = RETAIL_CATEGORIES.has(page.id) && Array.isArray(parsed[page.id]?.retailerRows)
          ? parsed[page.id].retailerRows
          : [];
        return [
          page.id,
          {
            sellerRows: [...rows, ...retailerRows].map((row) => ({
              ...row,
              results: Array.isArray(row?.results) ? row.results : [],
              loading: Boolean(row?.searchId) || Boolean(row?.loading),
              processed: Boolean(row?.processed),
              searchId: String(row?.searchId || ''),
              controller: null,
              searchTask: row?.searchTask && typeof row.searchTask === 'object'
                ? row.searchTask
                : null,
            })),
          },
        ];
      })
    );

    return buildPageWorkspaces(accounts, storedWorkspaces);
  } catch (error) {
    console.warn('[App] Failed to read saved search results:', error);
    return buildPageWorkspaces(accounts);
  }
};

const serializePageWorkspaces = (workspaces) => JSON.stringify(
  Object.fromEntries(
    CATEGORY_PAGES.map((page) => {
      const rows = (workspaces[page.id]?.sellerRows || []).map((row) => ({
        seller: row.seller,
        displayName: row.displayName,
        results: row.results,
        resultsDay: row.resultsDay,
        resultsFilters: row.resultsFilters,
        loading: row.loading,
        processed: row.processed,
        error: row.error,
        errorCode: row.errorCode,
        searchId: row.searchId,
        progress: row.progress,
        searchTask: row.searchTask,
      }));
      return [page.id, {
        sellerRows: rows.filter((row) => !RETAIL_BY_USERNAME.has(row.seller)),
        ...(RETAIL_CATEGORIES.has(page.id) ? { retailerRows: rows.filter((row) => RETAIL_BY_USERNAME.has(row.seller)) } : {}),
      }];
    })
  )
);

const normalizeMeasurementValue = (value, fallback) => {
  const clean = String(value ?? '').trim();
  return clean || fallback;
};

const readRangeNumber = (value) => {
  const raw = String(value ?? '').trim();
  const number = Number(raw);
  return raw && Number.isFinite(number) && number >= 0 && number <= 1000 ? number : null;
};

const getTopsRange = (filters, { min, max }) => {
  const lower = readRangeNumber(filters[min]);
  const upper = readRangeNumber(filters[max]);
  return lower !== null && upper !== null && lower <= upper ? [lower, upper] : null;
};

const normalizeRangeValue = (value, options, fallback) => {
  const clean = String(value ?? '').trim();
  return options.includes(clean) ? clean : fallback;
};

const readPriceCap = (value) => {
  const raw = String(value ?? '').trim();
  const amount = Number(raw);
  return raw && /^\d*(?:\.\d{0,2})?$/.test(raw) && Number.isFinite(amount) && amount <= 10000
    ? amount : null;
};

const normalizePageFilters = (pageId, rawFilters = null) => {
  const page = CATEGORY_PAGE_MAP[pageId];
  const defaults = getDefaultPageFilters(pageId);
  const source = rawFilters && typeof rawFilters === 'object' ? rawFilters : {};
  const rawPrice = String(source.maxPriceCad ?? defaults.maxPriceCad).trim();
  const maxPriceCad = String(readPriceCap(rawPrice) ?? rawPrice);

  if (!page) {
    return defaults;
  }

  if (page.mode === 'measurements') {
    const measurements = Object.fromEntries(TOPS_RANGES.flatMap(({ min, max }, index) => {
      const targetKey = index === 0 ? 'first' : 'second';
      const center = readRangeNumber(source[targetKey]);
      const tolerance = readRangeNumber(source[`${targetKey}Tolerance`]);
      const legacy = center !== null && tolerance !== null;
      return [
        [min, normalizeMeasurementValue(source[min], legacy ? String(Math.max(0, center - tolerance)) : defaults[min])],
        [max, normalizeMeasurementValue(source[max], legacy ? String(Math.min(1000, center + tolerance)) : defaults[max])],
      ];
    }));
    return { ...measurements, maxPriceCad };
  }

  if (page.mode === 'sizeRange') {
    return {
      min: normalizeRangeValue(source.min, page.sizeOptions, defaults.min),
      max: normalizeRangeValue(source.max, page.sizeOptions, defaults.max),
      maxPriceCad,
    };
  }

  if (page.mode === 'bottomMeasurements') {
    const measurements = Object.fromEntries(BOTTOMS_RANGES.flatMap(({ min, max }) => [
      [min, normalizeMeasurementValue(source[min], defaults[min])],
      [max, normalizeMeasurementValue(source[max], defaults[max])],
    ]));
    return { ...measurements, maxPriceCad };
  }

  return { ...defaults, maxPriceCad };
};

const getDefaultFiltersByPage = () =>
  Object.fromEntries(CATEGORY_PAGES.map((page) => [page.id, getDefaultPageFilters(page.id)]));

const readStoredSellerAccounts = () => {
  if (typeof window === 'undefined') {
    return getDefaultSellerAccounts();
  }

  try {
    const raw = window.localStorage.getItem(FOLLOWING_STORAGE_KEY);
    if (!raw) {
      return getDefaultSellerAccounts();
    }

    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return getDefaultSellerAccounts();
    }

    const normalized = dedupeSellerAccounts(parsed);
    const savedVersion = Number(window.localStorage.getItem(SELLER_ADDITIONS_STORAGE_KEY)) || 0;
    if (savedVersion >= DEFAULT_SELLER_ADDITIONS_VERSION) {
      return normalized;
    }
    const removals = new Set(getSellerRemovalsAfterVersion(savedVersion));
    return dedupeSellerAccounts([...normalized, ...getSellerAdditionsAfterVersion(savedVersion)])
      .filter((account) => !removals.has(account.username));
  } catch (error) {
    console.warn('[App] Failed to read saved seller accounts:', error);
    return getDefaultSellerAccounts();
  }
};

const readStoredPageFilters = () => {
  if (typeof window === 'undefined') {
    return getDefaultFiltersByPage();
  }

  try {
    const storedDefaultsSignature = window.localStorage.getItem(PAGE_FILTERS_VERSION_STORAGE_KEY);
    const raw = window.localStorage.getItem(PAGE_FILTERS_STORAGE_KEY);
    if (!raw) {
      return getDefaultFiltersByPage();
    }

    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      return getDefaultFiltersByPage();
    }

    let previousDefaults = [];
    if (storedDefaultsSignature && storedDefaultsSignature !== CATEGORY_FILTER_DEFAULTS_SIGNATURE) {
      try {
        const records = JSON.parse(storedDefaultsSignature);
        if (Array.isArray(records)) previousDefaults = records;
      } catch {
        // Legacy signatures may not contain the previous default values.
      }
    }
    return Object.fromEntries(CATEGORY_PAGES.map((page) => {
      const saved = parsed[page.id];
      const previous = previousDefaults.find((record) => record?.id === page.id)?.defaults;
      const unchanged = saved && previous && Object.entries(previous).every(([key, value]) => saved[key] === value);
      return [page.id, normalizePageFilters(page.id, unchanged ? null : saved)];
    }));
  } catch (error) {
    console.warn('[App] Failed to read page filters:', error);
    return getDefaultFiltersByPage();
  }
};

const resolvePageId = (value) => (isCategoryPageId(value) ? value : DEFAULT_CATEGORY_PAGE_ID);

const readInitialPageId = () => {
  if (typeof window === 'undefined') {
    return DEFAULT_CATEGORY_PAGE_ID;
  }

  const hashValue = window.location.hash.replace(/^#/, '').trim();
  if (isCategoryPageId(hashValue)) {
    return hashValue;
  }

  try {
    return resolvePageId(window.localStorage.getItem(ACTIVE_PAGE_STORAGE_KEY));
  } catch {
    return DEFAULT_CATEGORY_PAGE_ID;
  }
};

const buildSellerPageHref = (sellerUsername, page) => {
  const params = new URLSearchParams({ sort: 'recent', gender: 'male' });
  if (!Array.isArray(page.group) && page.group) {
    params.set('groups', page.group);
  }

  return `https://www.depop.com/${sellerUsername}/?${params.toString()}`;
};

const parseMeasurementNumber = (value, fallback) => {
  const parsed = parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const getResultAgeDays = (result) => {
  const rawAge = result?.ageDays ?? result?.age_days ?? result?.daysOld;
  const parsedAge = Number(rawAge);
  if (Number.isFinite(parsedAge)) {
    return parsedAge;
  }

  const rawListedAt = result?.listedAt ?? result?.listed_at ?? result?.createdAt ?? result?.created_at;
  const listedAtMs = Date.parse(rawListedAt || '');
  if (!Number.isFinite(listedAtMs)) {
    return null;
  }

  return Math.max((Date.now() - listedAtMs) / 86400000, 0);
};

const readResultMeasurement = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
};
const resultMeasurementFormat = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 2 });
const TOPS_RESULT_MEASUREMENTS = [
  { key: 'p2p', label: 'Pit to pit' },
  { key: 'length', label: 'Length' },
];

function ResultCard({ result, category }) {
  const fields = category === 'bottoms' ? BOTTOMS_RANGES : TOPS_RESULT_MEASUREMENTS;
  const measurements = fields.flatMap(({ key, label }) => {
    const value = readResultMeasurement(result[key]);
    return value === null ? [] : [{ key, label, value }];
  });
  const inseam = readResultMeasurement(result.inseam);
  const rise = readResultMeasurement(result.rise);
  if (category === 'bottoms' && inseam !== null && rise !== null) {
    measurements.push({ key: 'inseamRise', label: 'Inseam + rise', value: inseam + rise });
  }
  const age = getResultAgeDays(result);

  return (
    <a className="result-card card m-zoom" href={result.url || undefined} target="_blank" rel="noreferrer">
      <div className="result-media">
        {result.image ? (
          <img className="result-img frame__img" src={result.image} alt={result.title ? '' : 'Depop listing photo'} loading="lazy" />
        ) : (
          <div className="result-img placeholder" aria-hidden="true"><ImageOff size={24} /></div>
        )}
      </div>
      <div className="result-info">
        {result.price && <div className="result-price">{result.price}</div>}
        {result.title && <div className="result-title">{result.title}</div>}
        {(result.sizeLabel || age !== null) && (
          <div className="result-details">
            {result.sizeLabel && <span>Size {result.sizeLabel}</span>}
            {age !== null && <span>{Math.round(age)}d old</span>}
          </div>
        )}
        {measurements.length > 0 && (
          <dl className="result-measurements">
            {measurements.map(({ key, label, value }) => (
              <div key={key} className={key === 'inseamRise' ? 'result-measurement-total' : undefined}>
                <dt>{label}</dt>
                <dd>{resultMeasurementFormat.format(value)}&Prime;</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </a>
  );
}

const buildSearchPayload = (page, filters, sellerUsername, searchId) => {
  const retailer = RETAIL_BY_USERNAME.get(sellerUsername);
  const payload = {
    category: page.id,
    ...(retailer ? { retailSource: retailer.id, groups: page.id } : { seller: sellerUsername, groups: page.group }),
    gender: 'male',
    maxItems: 40,
    maxLinks: 1000,
    maxScrolls: 16,
    parseWorkers: 4,
    searchId,
  };
  const maxPriceCad = readPriceCap(filters.maxPriceCad);
  if (maxPriceCad !== null) payload.maxPriceCad = maxPriceCad;

  if (page.mode === 'measurements') {
    const [p2p, length] = TOPS_RANGES.map((range) =>
      getTopsRange(filters, range) ?? getTopsRange(page.defaults, range)
    );
    payload.measurements = {
      first: (p2p[0] + p2p[1]) / 2,
      second: (length[0] + length[1]) / 2,
    };
    payload.p2pTolerance = (p2p[1] - p2p[0]) / 2;
    payload.lengthTolerance = (length[1] - length[0]) / 2;
  } else if (page.mode === 'sizeRange') {
    const minSize = parseMeasurementNumber(filters.min, parseMeasurementNumber(page.defaults.min, null));
    const maxSize = parseMeasurementNumber(filters.max, parseMeasurementNumber(page.defaults.max, null));

    payload.sizeRange = {
      min: Math.min(minSize, maxSize),
      max: Math.max(minSize, maxSize),
      system: page.sizeSystem || null,
    };
  } else if (page.mode === 'bottomMeasurements') {
    const normalizeMinMax = (minValue, maxValue, fallbackMin, fallbackMax) => {
      const min = parseMeasurementNumber(minValue, parseMeasurementNumber(fallbackMin, null));
      const max = parseMeasurementNumber(maxValue, parseMeasurementNumber(fallbackMax, null));
      return {
        min: Math.min(min, max),
        max: Math.max(min, max),
      };
    };

    const legacy = isLegacyBottomsSearch(page.id, filters);
    const ranges = legacy ? LEGACY_BOTTOMS_RANGES : BOTTOMS_RANGES;
    payload.bottomsMeasurements = Object.fromEntries(ranges.map(({ key, min, max }) => [
      key,
      normalizeMinMax(filters[min], filters[max], page.defaults[min] ?? '42', page.defaults[max] ?? '44'),
    ]));
  }

  return payload;
};

function ReconnectSearches({ onReconnect }) {
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) {
      return;
    }
    startedRef.current = true;
    onReconnect();
  }, [onReconnect]);

  return null;
}

function RangeField({ label, min, max, filters, onChange, invalid = false }) {
  return (
    <fieldset className="filter-range" data-fit-range={min}>
      <legend>{label} <span>(in)</span></legend>
      <div className="filter-range-pair">
        <label className="range-input">
          <span>Min</span>
          <input className="input-dark" aria-label={`${label} minimum`} inputMode="decimal"
            value={filters[min]} aria-invalid={invalid || undefined}
            onChange={(event) => onChange(min, event.target.value)} />
        </label>
        <span className="range-separator" aria-hidden="true">–</span>
        <label className="range-input">
          <span>Max</span>
          <input className="input-dark" aria-label={`${label} maximum`} inputMode="decimal"
            value={filters[max]} aria-invalid={invalid || undefined}
            onChange={(event) => onChange(max, event.target.value)} />
        </label>
      </div>
      {invalid && <p className="range-error" role="alert">Enter a valid min and max, in order.</p>}
    </fieldset>
  );
}

function TopsFitMarks() {
  return (
    <>
      <path className="garment" d="M86 20 66 24 44 45 61 64 73 55 73 109 151 109 151 55 163 64 180 45 158 24 138 20 128 20c-1 9-7 14-16 14s-15-5-16-14Z" />
      <path className="garment-seam" d="M73 102h78M50 48l15 15m94 0 15-15M96 20c2 7 7 11 16 11s14-4 16-11" />
      <g data-fit-mark="p2pMin">
        <path className="fit-mark" d="M75 56h73M75 52v8m73-8v8M80 53l-5 3 5 3m63-6 5 3-5 3" />
        <text x="112" y="50" textAnchor="middle">P2P</text>
      </g>
      <g data-fit-mark="lengthMin">
        <path className="garment-seam" d="M152 20h31m-30 89h30" />
        <path className="fit-mark" d="M188 20v89m-5-89h10m-10 89h10m-8-84 3-5 3 5m-6 79 3 5 3-5" />
        <text x="198" y="68">LEN</text>
      </g>
    </>
  );
}

function BottomsFitMarks() {
  return (
    <>
      <path className="garment" d="M73 26h78l7 28 2 80h-39l-9-66-9 66H64l2-80Z" />
      <path className="garment-seam" d="M71 34h82M75 34c0 10 4 16 13 18m61-18c0 10-4 16-13 18m-17-18v16c0 5-3 9-7 10m-47 68h39m16 0h40" />
      <g data-fit-mark="waistMin">
        <path className="fit-mark" d="M73 17h78M73 13v8m78-8v8M78 14l-5 3 5 3m68-6 5 3-5 3" />
        <text x="112" y="10" textAnchor="middle">WAIST &#215;2</text>
      </g>
      <g data-fit-mark="riseMin">
        <path className="garment-seam" d="M63 47h42" />
        <path className="fit-mark" d="M105 26v42m-4-42h8m-8 42h8m-7-37 3-5 3 5m-6 32 3 5 3-5" />
        <text x="59" y="50" textAnchor="end">RISE</text>
      </g>
      <g data-fit-mark="inseamMin">
        <path className="garment-seam" d="M132 98h32" />
        <path className="fit-mark" d="m117 68 10 66m-14-66h8m2 66h8m-17-61 3-5 4 4m2 57 4 5 2-5" />
        <text x="168" y="101">INSEAM</text>
      </g>
      <g data-fit-mark="legOpeningMin">
        <path className="fit-mark" d="M121 144h39m-39-4v8m39-8v8m-34-7-5 3 5 3m29-6 5 3-5 3" />
        <text x="140.5" y="159" textAnchor="middle">LEG OPENING</text>
      </g>
    </>
  );
}

function FitGuide({ category }) {
  const isBottoms = category === 'bottoms';
  return (
    <div className="fit-guide">
      <svg viewBox={isBottoms ? '0 0 224 164' : '0 0 224 128'} fill="none"
        className={`fit-diagram${isBottoms ? ' fit-diagram--bottoms' : ''}`}
        role="img" aria-labelledby="fit-guide-title" aria-describedby="fit-guide-description">
        <title id="fit-guide-title">{isBottoms ? 'Bottoms measurement guide' : 'Tops measurement guide'}</title>
        <desc id="fit-guide-description">
          {isBottoms
            ? 'Double the flat waistband width for waist size. Measure front rise from waistband to crotch, inseam from crotch to hem, and leg opening flat across one cuff.'
            : 'Measure pit to pit across the chest and length from the shoulder to the hem.'}
        </desc>
        {isBottoms ? <BottomsFitMarks /> : <TopsFitMarks />}
        <path className="fit-diagram-cross" d="M15 19h12m-6-6v12m-6 84h12m-6-6v12" />
      </svg>
    </div>
  );
}

function App() {
  const [initialSellerAccounts] = useState(readStoredSellerAccounts);
  const searchRegistryRef = useRef(new Map());
  const searchQueueRef = useRef([]);
  const batchCounterRef = useRef(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [sellerManagerOpen, setSellerManagerOpen] = useState(false);
  const sellerManagerRef = useRef(null);
  const [sellerView, setSellerView] = useState('all');
  const [retailSources, setRetailSources] = useState(null);
  const [sellerQuery, setSellerQuery] = useState('');
  const [expandedResults, setExpandedResults] = useState({});
  const [activePageId, setActivePageId] = useState(() => readInitialPageId());
  const [sellerAccounts, setSellerAccounts] = useState(initialSellerAccounts);
  const [pageFilters, setPageFilters] = useState(() => readStoredPageFilters());
  const [pageWorkspaces, setPageWorkspaces] = useState(() =>
    readStoredPageWorkspaces(initialSellerAccounts)
  );
  const pageWorkspacesRef = useRef(pageWorkspaces);
  const [sellerForm, setSellerForm] = useState({
    username: '',
    displayName: '',
    error: '',
  });
  const [queueState, setQueueState] = useState({
    active: [],
    pending: [],
  });

  const sellerAccountsSnapshot = JSON.stringify(sellerAccounts);
  const pageFiltersSnapshot = JSON.stringify(pageFilters);
  const activePage = CATEGORY_PAGE_MAP[activePageId] || CATEGORY_PAGE_MAP[DEFAULT_CATEGORY_PAGE_ID];
  const hasRetailers = RETAIL_CATEGORIES.has(activePage.id);
  const hasFitGuide = activePage.id === 'tops' || activePage.id === 'bottoms';
  const sellerRows = pageWorkspaces[activePage.id]?.sellerRows || [];
  const currentFilters = pageFilters[activePage.id] || getDefaultPageFilters(activePage.id);
  const invalidTopsRanges = activePage.mode === 'measurements'
    && TOPS_RANGES.some((range) => !getTopsRange(currentFilters, range));
  const invalidPriceCap = Boolean(currentFilters.maxPriceCad.trim()) && readPriceCap(currentFilters.maxPriceCad) === null;
  const invalidSearchFilters = invalidTopsRanges || invalidPriceCap;
  const matchView = sellerView === 'matched';
  const checkingRetailers = hasRetailers && retailSources === null;
  const canSearchRow = (username) => {
    const retailer = RETAIL_BY_USERNAME.get(username);
    return !retailer || retailSources?.[retailer.id] === true;
  };
  const sellerCount = sellerRows.length;
  const activeSearchCount = sellerRows.filter((row) => row.loading).length;
  const queuedCountForPage = sellerRows.filter((row) => row.progress?.phase === 'queued').length;
  const totalHits = sellerRows.reduce((sum, row) => sum + row.results.length, 0);
  const pageIsLoading = activeSearchCount > 0 || queuedCountForPage > 0;
  const queuePositionByKey = new Map(
    queueState.pending.map((task, index) => [task.key, index + 1])
  );
  const sellerSearchQuery = sellerQuery.trim().toLowerCase().replace(/^@/, '');
  const matchedSellerCount = sellerRows.filter((row) => row.results.length > 0).length;
  const visibleSellerRows = sellerRows.filter((row) =>
    (!matchView || row.results.length > 0)
    && (!sellerSearchQuery || [row.seller, row.displayName].some((value) =>
      String(value || '').toLowerCase().includes(sellerSearchQuery)))
  );

  useEffect(() => {
    if (!hasRetailers) return undefined;
    const controller = new AbortController();
    loadRetailSources(controller.signal)
      .then((sources) => setRetailSources(Object.fromEntries(sources.map(({ id, enabled }) => [id, enabled]))))
      .catch(() => {
        if (!controller.signal.aborted) setRetailSources({});
      });
    return () => controller.abort();
  }, [hasRetailers]);

  useEffect(() => {
    pageWorkspacesRef.current = pageWorkspaces;
  }, [pageWorkspaces]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined;
    }

    const timeoutId = window.setTimeout(() => {
      try {
        window.localStorage.setItem(
          PAGE_WORKSPACES_STORAGE_KEY,
          serializePageWorkspaces(pageWorkspaces)
        );
      } catch (error) {
        console.warn('[App] Failed to persist search results:', error);
      }
    }, 150);

    return () => window.clearTimeout(timeoutId);
  }, [pageWorkspaces]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined;
    }

    const flushWorkspace = () => {
      try {
        window.localStorage.setItem(
          PAGE_WORKSPACES_STORAGE_KEY,
          serializePageWorkspaces(pageWorkspacesRef.current)
        );
      } catch (error) {
        console.warn('[App] Failed to flush search results:', error);
      }
    };

    window.addEventListener('pagehide', flushWorkspace);
    return () => window.removeEventListener('pagehide', flushWorkspace);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    try {
      window.localStorage.setItem(FOLLOWING_STORAGE_KEY, sellerAccountsSnapshot);
      window.localStorage.setItem(SELLER_ADDITIONS_STORAGE_KEY, String(DEFAULT_SELLER_ADDITIONS_VERSION));
    } catch (error) {
      console.warn('[App] Failed to persist seller accounts:', error);
    }
  }, [sellerAccountsSnapshot]);

  useEffect(() => {
    setPageWorkspaces((prev) => buildPageWorkspaces(sellerAccounts, prev));
  }, [sellerAccountsSnapshot, sellerAccounts]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    try {
      window.localStorage.setItem(
        PAGE_FILTERS_VERSION_STORAGE_KEY,
        CATEGORY_FILTER_DEFAULTS_SIGNATURE
      );
      window.localStorage.setItem(PAGE_FILTERS_STORAGE_KEY, pageFiltersSnapshot);
    } catch (error) {
      console.warn('[App] Failed to persist page filters:', error);
    }
  }, [pageFiltersSnapshot]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    try {
      window.localStorage.setItem(ACTIVE_PAGE_STORAGE_KEY, activePageId);
    } catch (error) {
      console.warn('[App] Failed to save active page:', error);
    }
    const nextHash = `#${activePageId}`;
    if (window.location.hash !== nextHash) {
      window.history.replaceState(null, '', nextHash);
    }
  }, [activePageId]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined;
    }

    const handleHashChange = () => {
      const nextPageId = resolvePageId(window.location.hash.replace(/^#/, '').trim());
      setActivePageId(nextPageId);
    };

    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  useEffect(() => {
    const dialog = sellerManagerRef.current;
    if (sellerManagerOpen && !dialog.open) dialog.showModal();
    else if (!sellerManagerOpen && dialog.open) dialog.close();
  }, [sellerManagerOpen]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);

    return () => window.clearInterval(intervalId);
  }, []);

  const getStreamErrorDetails = (error) => {
    if (typeof error === 'string') {
      return { message: error, code: null };
    }

    return {
      message: error?.message || 'Stream error',
      code: error?.code || null,
    };
  };

  const searchKeyFor = (pageId, sellerUsername) => `${pageId}::${sellerUsername}`;

  const shouldRetryLowParse = (task, summary) => {
    if (!task || task.source !== 'batch' || RETAIL_BY_USERNAME.has(task.sellerUsername)) {
      return false;
    }

    if ((task.lowParseRetryCount || 0) >= MAX_LOW_PARSE_RETRIES) {
      return false;
    }

    const stopReason = String(summary?.stopReason || '').trim();
    if (stopReason === 'age_window' || stopReason === 'match_limit') {
      return false;
    }

    const processed = Number(summary?.processed);
    const total = Number(summary?.total);
    if (!Number.isFinite(processed) || !Number.isFinite(total) || total <= 0) {
      return false;
    }

    return processed / total < LOW_PARSE_RETRY_RATIO;
  };

  const cloneQueueTask = (task) => {
    if (!task) {
      return null;
    }

    return {
      ...task,
      filters: task.filters ? { ...task.filters } : null,
    };
  };

  const syncQueueState = () => {
    setQueueState({
      active: Array.from(searchRegistryRef.current.values()).map((entry) => cloneQueueTask(entry.task)),
      pending: searchQueueRef.current.map(cloneQueueTask),
    });
  };

  const nextBatchId = (prefix = 'batch') => {
    batchCounterRef.current += 1;
    return `${prefix}-${Date.now().toString(36)}-${batchCounterRef.current.toString(36)}`;
  };

  const updateSellerRow = (pageId, sellerUsername, updates) => {
    const prev = pageWorkspacesRef.current;
    const next = {
      ...prev,
      [pageId]: {
        sellerRows: prev[pageId].sellerRows.map((row) =>
          row.seller === sellerUsername ? { ...row, ...updates } : row
        ),
      },
    };
    pageWorkspacesRef.current = next;
    setPageWorkspaces(next);
  };

  const addSellerResult = (pageId, sellerUsername, item) => {
    if (item.seller && normalizeSellerUsername(item.seller) !== sellerUsername) {
      return;
    }
    const prev = pageWorkspacesRef.current;
    const next = {
      ...prev,
      [pageId]: {
        sellerRows: prev[pageId].sellerRows.map((row) => {
          if (row.seller !== sellerUsername) {
            return row;
          }

          if (row.results.some((result) => result.url === item.url)) {
            return { ...row, results: row.results.map((result) => result.url === item.url ? item : result) };
          }

          return { ...row, results: [...row.results, item] };
        }),
      },
    };
    pageWorkspacesRef.current = next;
    setPageWorkspaces(next);
  };

  const getSellerRow = (pageId, sellerUsername) =>
    pageWorkspacesRef.current[pageId]?.sellerRows.find((row) => row.seller === sellerUsername);

  const isSellerScheduled = (pageId, sellerUsername) => {
    const searchKey = searchKeyFor(pageId, sellerUsername);
    if (searchRegistryRef.current.has(searchKey)) {
      return true;
    }

    return searchQueueRef.current.some((task) => task.key === searchKey);
  };

  const resetQueuedSellerRow = (task, message = '') => {
    updateSellerRow(task.pageId, task.sellerUsername, {
      loading: false,
      controller: null,
      searchId: '',
      searchTask: null,
      processed: false,
      error: null,
      errorCode: null,
      progress: message ? createProgressState({ phase: 'idle', message }) : null,
    });
  };

  const queueLowParseRetry = (task, summary) => {
    const processed = Number(summary?.processed) || 0;
    const total = Number(summary?.total) || 0;
    const retryTask = {
      ...task,
      searchId: makeSearchId(),
      resetResults: false,
      lowParseRetryCount: (task.lowParseRetryCount || 0) + 1,
    };

    if (!searchQueueRef.current.some((queuedTask) => queuedTask.key === retryTask.key)) {
      searchQueueRef.current = [...searchQueueRef.current, retryTask];
    }

    updateSellerRow(task.pageId, task.sellerUsername, {
      loading: false,
      controller: null,
      searchId: retryTask.searchId,
      searchTask: cloneQueueTask(retryTask),
      processed: false,
      error: null,
      errorCode: null,
      progress: createProgressState({
        phase: 'queued',
        message: `Retrying after an incomplete scan (${processed}/${total} parsed).`,
      }),
    });
    syncQueueState();
  };

  const removeQueuedTasks = (predicate, options = {}) => {
    const removed = [];
    searchQueueRef.current = searchQueueRef.current.filter((task) => {
      if (predicate(task)) {
        removed.push(task);
        return false;
      }

      return true;
    });

    if (removed.length > 0) {
      removed.forEach((task) => resetQueuedSellerRow(task, options.message || ''));
      syncQueueState();
    }

    return removed;
  };

  const finishSellerSearch = (pageId, sellerUsername, searchId, updates) => {
    const searchKey = searchKeyFor(pageId, sellerUsername);
    const activeSearch = searchRegistryRef.current.get(searchKey);
    if (!activeSearch || activeSearch.searchId !== searchId) {
      return false;
    }

    searchRegistryRef.current.delete(searchKey);
    updateSellerRow(pageId, sellerUsername, {
      controller: null,
      searchId: '',
      searchTask: null,
      ...updates,
    });
    syncQueueState();
    return true;
  };

  const handleSellerFormInput = (field, value) => {
    setSellerForm((prev) => ({
      ...prev,
      [field]: value,
      error: '',
    }));
  };

  const handlePageFilterChange = (pageId, field, value) => {
    setPageFilters((prev) => ({
      ...prev,
      [pageId]: {
        ...prev[pageId],
        [field]: value,
      },
    }));
  };

  const switchPage = (pageId) => {
    const nextPageId = resolvePageId(pageId);
    setActivePageId(nextPageId);
    if (typeof window !== 'undefined') {
      window.location.hash = nextPageId;
    }
  };

  const addSellerAccount = () => {
    const username = normalizeSellerUsername(sellerForm.username);
    const displayName = normalizeSellerDisplayName(sellerForm.displayName);

    if (!/^[a-z0-9._-]+$/.test(username)) {
      setSellerForm((prev) => ({
        ...prev,
        error: 'Enter a seller username to add.',
      }));
      return;
    }

    const existingAccount = sellerAccounts.find((account) => account.username === username);
    if (existingAccount && (!displayName || displayName === existingAccount.name)) {
      setSellerForm((prev) => ({
        ...prev,
        error: 'That seller is already in your list.',
      }));
      return;
    }

    setSellerAccounts((prev) => existingAccount
      ? prev.map((account) => account.username === username ? { ...account, name: displayName } : account)
      : [...prev, { username, name: displayName || username }]);

    setSellerForm({
      username: '',
      displayName: '',
      error: '',
    });
  };

  const cancelSellerSearch = async (pageId, sellerUsername) => {
    const searchKey = searchKeyFor(pageId, sellerUsername);
    const removedQueued = removeQueuedTasks((task) => task.key === searchKey);
    if (removedQueued.length > 0) {
      return;
    }

    const activeSearch = searchRegistryRef.current.get(searchKey);
    const sellerRow = getSellerRow(pageId, sellerUsername);
    const searchId = activeSearch?.searchId || sellerRow?.searchId;

    if (!searchId && !activeSearch?.controller) {
      return;
    }

    try {
      await cancelSearch(searchId);
    } catch (error) {
      updateSellerRow(pageId, sellerUsername, { error: error.message });
      return;
    }

    if (activeSearch?.controller) {
      try {
        activeSearch.controller.abort();
      } catch (error) {
        console.warn('[Cancel] Failed to abort controller:', error);
      }
    }

    searchRegistryRef.current.delete(searchKey);
    updateSellerRow(pageId, sellerUsername, {
      loading: false,
      controller: null,
      searchId: '',
      searchTask: null,
      processed: false,
      error: null,
      errorCode: null,
      progress: null,
    });

    syncQueueState();
    resumePendingTasks();
  };

  const cancelSellerAcrossPages = async (sellerUsername) => {
    removeQueuedTasks((task) => task.sellerUsername === sellerUsername);

    const activePages = CATEGORY_PAGES
      .map((page) => page.id)
      .filter((pageId) => searchRegistryRef.current.has(searchKeyFor(pageId, sellerUsername)));

    if (activePages.length === 0) {
      return;
    }

    await Promise.all(
      activePages.map((pageId) => cancelSellerSearch(pageId, sellerUsername))
    );
  };

  const removeSellerAccount = async (sellerUsername) => {
    const normalizedUsername = normalizeSellerUsername(sellerUsername);
    if (!normalizedUsername) {
      return;
    }

    await cancelSellerAcrossPages(normalizedUsername);
    setSellerAccounts((prev) =>
      prev.filter((account) => account.username !== normalizedUsername)
    );
  };

  const resetSellerAccounts = async () => {
    const defaultAccounts = getDefaultSellerAccounts();
    const defaultUsernames = new Set(defaultAccounts.map((account) => account.username));
    const removedUsernames = sellerAccounts
      .filter((account) => !defaultUsernames.has(account.username))
      .map((account) => account.username);

    if (removedUsernames.length > 0) {
      await Promise.all(removedUsernames.map((username) => cancelSellerAcrossPages(username)));
    }

    setSellerAccounts(defaultAccounts);
    setSellerForm({
      username: '',
      displayName: '',
      error: '',
    });
  };

  const resumePendingTasks = () => {
    const activeKinds = new Set(Array.from(searchRegistryRef.current.values(), ({ task }) =>
      RETAIL_BY_USERNAME.has(task.sellerUsername)));
    const nextIndex = searchQueueRef.current.findIndex((task) =>
      !activeKinds.has(RETAIL_BY_USERNAME.has(task.sellerUsername)));
    if (nextIndex === -1) {
      syncQueueState();
      return;
    }

    const [nextTask] = searchQueueRef.current.splice(nextIndex, 1);
    syncQueueState();
    void executeSellerSearch(nextTask).then((outcome) => handleSearchCompletion(nextTask, outcome));
  };

  const handleSearchCompletion = () => {
    syncQueueState();
    resumePendingTasks();
  };

  const executeSellerSearch = async (incomingTask, registration = null) => {
    const task = {
      ...incomingTask,
      searchId: incomingTask.searchId || makeSearchId(),
      resetResults: incomingTask.resetResults !== false,
    };
    const { pageId, sellerUsername, filters } = task;
    const searchKey = searchKeyFor(pageId, sellerUsername);
    if (searchRegistryRef.current.has(searchKey)) {
      return { status: 'skipped', code: null };
    }

    const page = CATEGORY_PAGE_MAP[pageId];
    const controller = new AbortController();
    const searchId = task.searchId;
    const payload = buildSearchPayload(page, filters, sellerUsername, searchId);
    let outcome = { status: 'done', code: null };
    let finalized = false;

    searchRegistryRef.current.set(searchKey, { searchId, controller, task });
    const sellerRowUpdate = {
      loading: true,
      error: null,
      errorCode: null,
      searchId,
      controller,
      searchTask: cloneQueueTask(task),
      processed: false,
      progress: createProgressState({ phase: 'starting' }),
    };
    const existingRow = getSellerRow(pageId, sellerUsername);
    const day = currentResultDay();
    const savedFilters = existingRow?.resultsFilters;
    const keepResults = task.resetResults === false || (
      existingRow?.resultsDay === day
      && savedFilters
      && !isLegacyBottomsSearch(pageId, savedFilters)
      && JSON.stringify(normalizePageFilters(pageId, {
        ...savedFilters, maxPriceCad: savedFilters.maxPriceCad ?? '',
      })) === JSON.stringify(filters)
    );
    sellerRowUpdate.resultsDay = day;
    sellerRowUpdate.resultsFilters = filters;
    if (!keepResults) {
      sellerRowUpdate.results = [];
    }
    updateSellerRow(pageId, sellerUsername, sellerRowUpdate);
    syncQueueState();
    if (!registration) {
      try {
        window.localStorage.setItem(PAGE_WORKSPACES_STORAGE_KEY, serializePageWorkspaces(pageWorkspacesRef.current));
      } catch (error) {
        console.warn('[Search] Failed to save search ID:', error);
      }
    }

    try {
      await registration;
    } catch (error) {
      finishSellerSearch(pageId, sellerUsername, searchId, {
        loading: false, processed: false, error: error.message, errorCode: 'batch_start_failed',
      });
      return { status: 'error', code: 'batch_start_failed' };
    }
    if (controller.signal.aborted) {
      await cancelSearch(searchId).catch(console.warn);
      return { status: 'cancelled', code: null };
    }
    const isCurrent = () => searchRegistryRef.current.get(searchKey)?.searchId === searchId;

    await streamSearch({
      payload,
      controller,
      onMatch: (evt) => {
        if (isCurrent()) addSellerResult(pageId, sellerUsername, evt.item);
      },
      onRemove: (evt) => {
        if (isCurrent()) updateSellerRow(pageId, sellerUsername, {
          results: (getSellerRow(pageId, sellerUsername)?.results || []).filter((item) => item.url !== evt.url),
        });
      },
      onProgress: (progress) => {
        if (isCurrent()) updateSellerRow(pageId, sellerUsername, { progress });
      },
      onMeta: (meta) => isCurrent() && updateSellerRow(pageId, sellerUsername, {
        progress: createProgressState({
          phase: 'collecting',
          total: meta.total,
          processed: meta.processed ?? getSellerRow(pageId, sellerUsername)?.progress?.processed ?? 0,
          matches: meta.matches ?? getSellerRow(pageId, sellerUsername)?.results?.length ?? 0,
        }),
      }),
      onError: (error) => {
        const { message, code } = getStreamErrorDetails(error);
        const existingProgress = getSellerRow(pageId, sellerUsername)?.progress;
        const didFinish = finishSellerSearch(pageId, sellerUsername, searchId, {
          loading: false,
          error: message,
          errorCode: code,
          processed: true,
          progress: existingProgress
            ? {
              ...existingProgress,
              phase: 'error',
              retryAttempt: null,
              retryTotalAttempts: null,
              retryDelaySeconds: null,
              retryAvailableAt: null,
            }
            : null,
        });
        if (didFinish) {
          finalized = true;
          outcome = { status: 'error', code, message };
        } else {
          outcome = { status: 'paused', code: null };
        }
      },
      onDone: (summary) => {
        if (summary?.stopReason === 'cancelled') {
          finalized = finishSellerSearch(pageId, sellerUsername, searchId, {
            loading: false, processed: false, progress: null,
          });
          outcome = { status: 'cancelled', code: null };
          return;
        }
        const existingProgress = getSellerRow(pageId, sellerUsername)?.progress;
        const doneProgress = existingProgress
          ? {
            ...existingProgress,
            processed: Number.isFinite(Number(summary?.processed))
              ? Number(summary.processed)
              : existingProgress.processed,
            total: Number.isFinite(Number(summary?.total))
              ? Number(summary.total)
              : existingProgress.total,
            matches: Number.isFinite(Number(summary?.matches))
              ? Number(summary.matches)
              : existingProgress.matches,
            phase: 'done',
            stopReason: summary?.stopReason || null,
            retryAttempt: null,
            retryTotalAttempts: null,
            retryDelaySeconds: null,
            retryAvailableAt: null,
          }
          : null;
        const shouldRetry = shouldRetryLowParse(task, summary);
        const didFinish = finishSellerSearch(pageId, sellerUsername, searchId, {
          loading: false,
          processed: !shouldRetry,
          progress: shouldRetry
            ? createProgressState({
              phase: 'queued',
              message: `Retrying after an incomplete scan (${summary?.processed ?? 0}/${summary?.total ?? 0} parsed).`,
            })
            : doneProgress,
        });
        if (didFinish) {
          finalized = true;
          if (shouldRetry) {
            queueLowParseRetry(task, summary);
            outcome = { status: 'requeued', code: null };
          } else {
            outcome = { status: 'done', code: null };
          }
        } else {
          outcome = { status: 'paused', code: null };
        }
      },
    });

    if (!finalized && !searchRegistryRef.current.has(searchKey)) {
      return { status: 'paused', code: null };
    }

    return outcome;
  };

  const queueSellerSearch = (pageId, sellerUsername, options = {}) => {
    const key = searchKeyFor(pageId, sellerUsername);
    if (isSellerScheduled(pageId, sellerUsername)) {
      return false;
    }

    const task = {
      key,
      pageId,
      sellerUsername,
      searchId: options.searchId || makeSearchId(),
      filters: normalizePageFilters(pageId, options.filters ?? pageFilters[pageId]),
      batchId: options.batchId || nextBatchId(options.source === 'batch' ? 'batch' : 'manual'),
      source: options.source || 'manual',
      resetResults: options.resetResults !== false,
      lowParseRetryCount: options.lowParseRetryCount || 0,
    };

    void executeSellerSearch(task, options.registration).then((outcome) => handleSearchCompletion(task, outcome));
    return true;
  };

  const reconnectPersistedSearches = () => {
    Object.entries(pageWorkspacesRef.current).forEach(([pageId, workspace]) => {
      (workspace?.sellerRows || []).forEach((row) => {
        if (!row.searchId) {
          return;
        }

        const savedTask = row.searchTask || {};
        const persistedFilters = savedTask.filters ?? row.resultsFilters;
        const savedFilters = persistedFilters
          ? { ...persistedFilters, maxPriceCad: persistedFilters.maxPriceCad ?? '' }
          : pageFilters[pageId];
        const task = {
          ...savedTask,
          key: searchKeyFor(pageId, row.seller),
          pageId,
          sellerUsername: row.seller,
          // A registered job keeps its original filters when reconnected.
          filters: isLegacyBottomsSearch(pageId, savedFilters) ? savedFilters : normalizePageFilters(pageId, savedFilters),
          batchId: savedTask.batchId || `resume-${row.searchId}`,
          source: savedTask.source || 'resume',
          resetResults: false,
          lowParseRetryCount: savedTask.lowParseRetryCount || 0,
          searchId: row.searchId,
        };

        void executeSellerSearch(task).then(handleSearchCompletion);
      });
    });
  };

  const startSellerSearch = async (pageId, sellerUsername) => {
    if (invalidSearchFilters || !canSearchRow(sellerUsername)) return;
    queueSellerSearch(pageId, sellerUsername, { source: 'manual' });
  };

  const startSearchAllForPage = async (pageId) => {
    if (invalidSearchFilters || checkingRetailers) return;
    setSellerView('all');
    setSellerQuery('');
    const batchId = nextBatchId('batch');
    const page = CATEGORY_PAGE_MAP[pageId];
    const filters = normalizePageFilters(pageId, pageFilters[pageId]);
    const searchesToStart = sellerRows
      .map((row) => row.seller)
      .filter((sellerUsername) => canSearchRow(sellerUsername) && !isSellerScheduled(pageId, sellerUsername))
      .map((sellerUsername) => {
        const searchId = makeSearchId();
        return {
          sellerUsername,
          searchId,
          payload: buildSearchPayload(
            page,
            filters,
            sellerUsername,
            searchId
          ),
        };
      });

    if (searchesToStart.length === 0) {
      return;
    }

    let registered;
    let registrationFailed;
    const registration = new Promise((resolve, reject) => {
      registered = resolve;
      registrationFailed = reject;
    });
    searchesToStart.forEach(({ sellerUsername, searchId }) => {
      queueSellerSearch(pageId, sellerUsername, {
        source: 'batch',
        batchId,
        filters,
        searchId,
        registration,
      });
    });
    // Persist every job ID before the batch request can outlive this tab.
    try {
      window.localStorage.setItem(PAGE_WORKSPACES_STORAGE_KEY, serializePageWorkspaces(pageWorkspacesRef.current));
    } catch (error) {
      console.warn('[Search] Failed to save batch:', error);
    }
    try {
      await startSearchBatch(searchesToStart.map((entry) => entry.payload));
      registered();
    } catch (error) {
      registrationFailed(error);
    }
  };

  const cancelPageSearches = async (pageId) => {
    removeQueuedTasks((task) => task.pageId === pageId);

    const activeSellers = sellerRows
      .filter((row) => row.loading)
      .map((row) => row.seller);

    await Promise.all(activeSellers.map((sellerUsername) => cancelSellerSearch(pageId, sellerUsername)));
  };

  const resetPageFilters = (pageId) => {
    setPageFilters((prev) => ({
      ...prev,
      [pageId]: getDefaultPageFilters(pageId),
    }));
  };

  const renderFilterControls = () => {
    if (['measurements', 'bottomMeasurements'].includes(activePage.mode)) {
      const isTops = activePage.mode === 'measurements';
      return (isTops ? TOPS_RANGES : BOTTOMS_RANGES).map((range) => (
        <RangeField key={range.min} {...range} filters={currentFilters}
          invalid={isTops && !getTopsRange(currentFilters, range)}
          onChange={(field, value) => handlePageFilterChange(activePage.id, field, value)} />
      ));
    }

    if (activePage.mode === 'sizeRange') {
      return (
        <>
          <label className="filter-label">
            <span>{activePage.sizeUnitLabel} min</span>
            <select
              className="input-dark input-select"
              value={currentFilters.min}
              onChange={(event) => handlePageFilterChange(activePage.id, 'min', event.target.value)}
            >
              {activePage.sizeOptions.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
          <label className="filter-label">
            <span>{activePage.sizeUnitLabel} max</span>
            <select
              className="input-dark input-select"
              value={currentFilters.max}
              onChange={(event) => handlePageFilterChange(activePage.id, 'max', event.target.value)}
            >
              {activePage.sizeOptions.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        </>
      );
    }

    return null;
  };

  return (
    <div className="app-home-shell app-frame">
      <a className="skip-link" href="#workspace" onClick={(event) => {
        event.preventDefault();
        document.getElementById('workspace')?.focus();
      }}>Skip to search</a>
      <ReconnectSearches onReconnect={reconnectPersistedSearches} />

      <dialog
        ref={sellerManagerRef}
        className="seller-manager-drawer"
        aria-labelledby="seller-manager-title"
        onClose={() => setSellerManagerOpen(false)}
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            const { left, right, top, bottom } = event.currentTarget.getBoundingClientRect();
            if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) {
              setSellerManagerOpen(false);
            }
          }
        }}
      >
        <div className="seller-manager-header">
          <div>
            <h2 id="seller-manager-title">Manage sellers</h2>
            <span className="mono muted">{sellerAccounts.length} saved sellers</span>
          </div>
          <button
            type="button"
            className="btn search-btn secondary icon-button"
            onClick={() => setSellerManagerOpen(false)}
            aria-label="Close seller manager"
          >
            <X size={18} />
          </button>
        </div>

        <form className="seller-manager-form" onSubmit={(event) => { event.preventDefault(); addSellerAccount(); }}>
          <label className="filter-label">Username or shop link
            <input className="input-dark" value={sellerForm.username}
              onChange={(event) => handleSellerFormInput('username', event.target.value)} />
          </label>
          <label className="filter-label">Display name <span className="muted">(optional)</span>
            <input className="input-dark" value={sellerForm.displayName}
              onChange={(event) => handleSellerFormInput('displayName', event.target.value)} />
          </label>
          <button type="submit" className="btn btn--primary search-btn">
            <Plus size={16} /> Add seller
          </button>
        </form>

        {sellerForm.error && (
          <div className="row-error seller-manager-error">
            {sellerForm.error}
          </div>
        )}

        <div className="seller-manager-list">
          {sellerAccounts.length === 0 ? (
            <div className="seller-manager-empty">
              No saved sellers.
            </div>
          ) : (
            sellerAccounts.map((account) => (
              <div key={account.username} className="seller-manager-item">
                <div className="seller-manager-item-copy">
                  <span className="seller-manager-item-name">
                    {account.name || account.username}
                  </span>
                  <span className="seller-manager-item-username">
                    @{account.username}
                  </span>
                </div>
                <button
                  type="button"
                  className="btn search-btn secondary small"
                  onClick={() => removeSellerAccount(account.username)}
                >
                  Remove
                </button>
              </div>
            ))
          )}
        </div>

        <div className="seller-manager-footer">
          <button
            type="button"
            className="btn search-btn secondary"
            onClick={resetSellerAccounts}
          >
            <RotateCcw size={16} /> Reset sellers
          </button>
          <button
            type="button"
            className="btn search-btn secondary"
            onClick={() => setSellerManagerOpen(false)}
          >
            Close
          </button>
        </div>
      </dialog>

      <aside className="sidebar category-sidebar">
        <div className="sidebar-title">
          <span className="brand-mark"><ScanLine size={20} strokeWidth={1.9} /></span>
          <span>Debot</span>
        </div>
        <nav className="sidebar-nav" aria-label="Categories">
          <div className="nav-label mono">Categories</div>
          <ul>
            {CATEGORY_PAGES.map((page) => {
              const Icon = CATEGORY_ICONS[page.id];
              const isActive = page.id === activePage.id;
              return (
              <li key={page.id}>
                <button
                  type="button"
                  className="sidebar-nav-button"
                  aria-label={page.label}
                  aria-current={isActive ? 'page' : undefined}
                  onClick={() => switchPage(page.id)}
                >
                  <Icon size={17} strokeWidth={1.75} />
                  <span>{page.label}</span>
                </button>
              </li>
              );
            })}
          </ul>
        </nav>
        <button type="button" className="btn search-btn secondary sidebar-manage"
          aria-haspopup="dialog" onClick={() => setSellerManagerOpen(true)}>
          <UsersRound size={17} /> <span>Manage sellers</span>
        </button>
      </aside>

      <main id="workspace" tabIndex={-1} className="main-content">
        <div className="home-workspace">
          <header className="home-header">
            <div className="home-header-copy">
              <div className="home-header-kicker mono"><span aria-hidden="true">/</span> {hasRetailers ? 'Depop + retailers' : 'Depop'}</div>
              <h1>{activePage.headline}</h1>
            </div>
            <dl className="workspace-stats" aria-label="Workspace summary">
              <div><dt>Sellers</dt><dd>{sellerCount}</dd></div>
              <div><dt>Matches</dt><dd>{totalHits}</dd></div>
              {queueState.active.length + queueState.pending.length > 0 && (
                <div><dt>In queue</dt><dd>{queueState.active.length + queueState.pending.length}</dd></div>
              )}
            </dl>
          </header>

          <section className={`home-controls${hasFitGuide ? ' has-fit-guide' : ''}`} aria-labelledby="fit-filters-title">
            <div className="home-controls-content">
              <div className="home-controls-heading">
                <h2 id="fit-filters-title"><ListFilter size={17} /> Filters</h2>
                <button type="button" className="btn search-btn secondary reset-filters"
                  onClick={() => resetPageFilters(activePage.id)}>
                  <RotateCcw size={15} /> Reset filters
                </button>
              </div>
              <div className="home-controls-fields">
                {renderFilterControls()}
              </div>

              <div className="home-controls-actions">
                <label className="filter-label price-filter">
                  <span>Max price (CAD)</span>
                  <input className="input-dark" inputMode="decimal" placeholder="No limit"
                    value={currentFilters.maxPriceCad} aria-invalid={invalidPriceCap || undefined}
                    aria-describedby={invalidPriceCap ? 'price-filter-error' : undefined}
                    onChange={(event) => handlePageFilterChange(activePage.id, 'maxPriceCad', event.target.value)} />
                </label>
                {pageIsLoading ? (
                  <button
                    type="button"
                    className="btn search-btn stop"
                    onClick={() => cancelPageSearches(activePage.id)}
                  >
                    <Square size={15} /> Stop page
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn--primary search-btn"
                    onClick={() => startSearchAllForPage(activePage.id)}
                    disabled={checkingRetailers || invalidSearchFilters || !sellerRows.some((row) => canSearchRow(row.seller))}
                  >
                    <Search size={16} /> Search All Sellers
                  </button>
                )}
              </div>
              {invalidPriceCap && (
                <p className="range-error" id="price-filter-error" role="alert">Enter a price from $0 to $10,000.</p>
              )}
            </div>
            {hasFitGuide && <FitGuide category={activePage.id} />}
          </section>

          <section className="seller-section" aria-labelledby="seller-section-title">
            <div className="seller-section-heading">
              <h2 id="seller-section-title">Seller queue</h2>
              <div className="seller-list-tools">
                <div className="segmented" role="group" aria-label="Show sellers">
                  <button type="button" aria-pressed={!matchView}
                    onClick={() => { setSellerView('all'); setSellerQuery(''); }}>All sellers</button>
                  <button type="button" aria-pressed={matchView}
                    onClick={() => setSellerView('matched')}>With matches <span>{matchedSellerCount}</span></button>
                </div>
                <label className="seller-query">
                  <Search size={16} aria-hidden="true" />
                  <input aria-label="Filter sellers"
                    placeholder="Filter sellers" value={sellerQuery}
                    onChange={(event) => setSellerQuery(event.target.value)} />
                </label>
              </div>
            </div>
          {visibleSellerRows.length === 0 ? (
            <div className="empty-state-card">
              <UsersRound size={22} />
              <h3>{sellerCount === 0 ? 'No saved sellers' : sellerSearchQuery ? 'No sellers found' : 'No matching listings'}</h3>
            </div>
          ) : (
            <div className="following-sellers">
              <div className="seller-table-head" aria-hidden="true">
                <span>Seller</span><span>Status</span><span>Action</span>
              </div>
              {visibleSellerRows.map((sellerRow) => {
                  const retailer = RETAIL_BY_USERNAME.get(sellerRow.seller);
                  const unavailable = retailer && !canSearchRow(sellerRow.seller);
                  const availabilityLabel = retailSources?.[retailer?.id] === false ? 'Permission required'
                    : retailSources ? 'Unavailable' : 'Checking availability';
                  const sellerKey = searchKeyFor(activePage.id, sellerRow.seller);
                  const queuePosition = queuePositionByKey.get(sellerKey) || null;
                  const progressDetails = getSellerProgressDetails(sellerRow, nowMs, queuePosition);
                  const stateTone = getSellerStateTone(sellerRow);
                  const isQueued = sellerRow.progress?.phase === 'queued';
                  const rowExpanded = Boolean(expandedResults[sellerKey]);

                  return (
                    <div
                      key={`${activePage.id}-${sellerRow.seller}`}
                      className={`seller-row-card ${sellerRow.loading ? 'loading' : ''}`}
                    >
                      <div className="seller-header">
                    <div className="seller-info">
                      <a
                        href={(retailer?.urls?.[activePage.id] || retailer?.url) || buildSellerPageHref(sellerRow.seller, activePage)}
                        target="_blank"
                        rel="noreferrer"
                        className="seller-name"
                      >
                        {sellerRow.displayName || sellerRow.seller}
                        <ArrowUpRight size={13} aria-hidden="true" />
                      </a>
                      <span className="seller-username">{retailer?.domain || `@${sellerRow.seller}`}</span>
                    </div>

                    <div className="seller-controls">
                      {sellerRow.loading || isQueued ? (
                        <button
                          type="button"
                          className="btn search-btn stop small"
                          onClick={() => cancelSellerSearch(activePage.id, sellerRow.seller)}
                        >
                          <Square size={13} /> {sellerRow.loading ? 'Stop' : 'Remove'}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn search-btn secondary small"
                          onClick={() => startSellerSearch(activePage.id, sellerRow.seller)}
                          disabled={invalidSearchFilters || Boolean(unavailable)}
                          title={unavailable ? availabilityLabel : undefined}
                        >
                          <Search size={14} /> Search
                        </button>
                      )}

                      <div className="seller-status-stack">
                        <span className={`seller-status-badge ${stateTone}`}>
                          {unavailable && !sellerRow.loading && !isQueued ? availabilityLabel : getSellerStateLabel(sellerRow)}
                        </span>
                        {stateTone !== 'idle' && (
                          <span className="seller-status">
                            {formatSellerStatusLabel(sellerRow, queuePosition)}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  {progressDetails && (
                    <div className="seller-progress-message">
                      <div className="seller-progress-primary">
                        {progressDetails.primary}
                      </div>
                      {progressDetails.secondary && (
                        <div className="seller-progress-secondary">
                          {progressDetails.secondary}
                        </div>
                      )}
                    </div>
                  )}

                  {sellerRow.error && (
                    <div className="row-error seller-row-error">
                      {sellerRow.error}
                    </div>
                  )}

                  {sellerRow.results.length > 0 && (
                    <div className="seller-results">
                      <div className="results-scroll" id={`results-${sellerRow.seller}`}>
                        {(rowExpanded ? sellerRow.results : sellerRow.results.slice(0, 6)).map((result, index) => (
                          <ResultCard key={`${result.url}-${index}`} result={result} category={activePage.id} />
                        ))}
                      </div>
                      {sellerRow.results.length > 6 && (
                        <button type="button" className="btn search-btn secondary all-results"
                          aria-expanded={rowExpanded} aria-controls={`results-${sellerRow.seller}`}
                          onClick={() => setExpandedResults((previous) => ({ ...previous, [sellerKey]: !rowExpanded }))}>
                          <ChevronDown size={16} /> {rowExpanded ? 'Show fewer' : `Show all ${sellerRow.results.length} matches`}
                        </button>
                      )}
                    </div>
                  )}

                  {sellerRow.results.length === 0 && sellerRow.processed && !sellerRow.loading && !sellerRow.error && (
                    <div className="seller-empty-results">
                      No matching listings.
                    </div>
                  )}
                    </div>
                  );
              })}
            </div>
          )}
          </section>
        </div>
      </main>
    </div>
  );
}

export default App;
