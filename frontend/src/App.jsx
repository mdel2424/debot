import { useEffect, useRef, useState } from 'react';
import {
  cancelSearch,
  makeSearchId,
  startSearchBatch,
  streamSearch,
} from './hooks/useStream';
import { DEFAULT_FOLLOWING_ACCOUNTS } from './data/defaultSellers';
import {
  CATEGORY_FILTER_DEFAULTS_SIGNATURE,
  CATEGORY_PAGES,
  CATEGORY_PAGE_MAP,
  DEFAULT_CATEGORY_PAGE_ID,
  getDefaultPageFilters,
  isCategoryPageId,
} from './data/categoryPages';
import './App.css';
import './index.css';

const FOLLOWING_STORAGE_KEY = 'debot.followingAccounts.v1';
const ACTIVE_PAGE_STORAGE_KEY = 'debot.categoryPage.v1';
const PAGE_FILTERS_STORAGE_KEY = 'debot.categoryFilters.v1';
const PAGE_FILTERS_VERSION_STORAGE_KEY = 'debot.categoryFilters.defaults.v1';
const PAGE_WORKSPACES_STORAGE_KEY = 'debot.pageWorkspaces.v1';
const LOW_PARSE_RETRY_RATIO = 0.9;
const MAX_LOW_PARSE_RETRIES = 1;
const LISTING_AGE_CUTOFF_DAYS = 80;

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
  if (sellerRow?.progress?.phase === 'queued') {
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

  if (progress?.phase === 'queued') {
    return queuePosition ? `Queue position ${queuePosition}` : 'Waiting for the global queue';
  }

  if (!sellerRow?.loading && !sellerRow?.error && !sellerRow?.processed && safeHits === 0 && processed === 0 && total === 0) {
    return 'Awaiting search';
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

  if (progress.phase === 'rate_limited' && progress.retryAvailableAt) {
    const secondsRemaining = getRetryCountdownSeconds(progress, nowMs);
    const attempt = progress.retryAttempt || 1;
    const totalAttempts = progress.retryTotalAttempts || 1;
    return {
      primary: `Cooldown ${secondsRemaining}s • retry ${attempt}/${totalAttempts}`,
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
      primary: `Stopped at ${LISTING_AGE_CUTOFF_DAYS}-day cutoff`,
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

const createSellerRow = (account, existingRow = null) => ({
  seller: account.username,
  displayName: account.name || account.username,
  results: existingRow?.results || [],
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
        sellerRows: buildSellerRows(accounts, existingWorkspaces[page.id]?.sellerRows || []),
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
        return [
          page.id,
          {
            sellerRows: rows.map((row) => ({
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
    CATEGORY_PAGES.map((page) => [
      page.id,
      {
        sellerRows: (workspaces[page.id]?.sellerRows || []).map((row) => ({
          seller: row.seller,
          displayName: row.displayName,
          results: row.results,
          loading: row.loading,
          processed: row.processed,
          error: row.error,
          errorCode: row.errorCode,
          searchId: row.searchId,
          progress: row.progress,
          searchTask: row.searchTask,
        })),
      },
    ])
  )
);

const normalizeMeasurementValue = (value, fallback) => {
  const clean = String(value ?? '').trim();
  return clean || fallback;
};

const normalizeRangeValue = (value, options, fallback) => {
  const clean = String(value ?? '').trim();
  return options.includes(clean) ? clean : fallback;
};

const normalizePageFilters = (pageId, rawFilters = null) => {
  const page = CATEGORY_PAGE_MAP[pageId];
  const defaults = getDefaultPageFilters(pageId);
  const source = rawFilters && typeof rawFilters === 'object' ? rawFilters : {};

  if (!page) {
    return defaults;
  }

  if (page.mode === 'measurements') {
    return {
      first: normalizeMeasurementValue(source.first, defaults.first),
      second: normalizeMeasurementValue(source.second, defaults.second),
      firstTolerance: normalizeMeasurementValue(source.firstTolerance, defaults.firstTolerance),
      secondTolerance: normalizeMeasurementValue(source.secondTolerance, defaults.secondTolerance),
    };
  }

  if (page.mode === 'sizeRange') {
    return {
      min: normalizeRangeValue(source.min, page.sizeOptions, defaults.min),
      max: normalizeRangeValue(source.max, page.sizeOptions, defaults.max),
    };
  }

  if (page.mode === 'bottomMeasurements') {
    return {
      waistMin: normalizeMeasurementValue(source.waistMin, defaults.waistMin),
      waistMax: normalizeMeasurementValue(source.waistMax, defaults.waistMax),
      inseamRiseMin: normalizeMeasurementValue(source.inseamRiseMin, defaults.inseamRiseMin),
      inseamRiseMax: normalizeMeasurementValue(source.inseamRiseMax, defaults.inseamRiseMax),
      legOpeningMin: normalizeMeasurementValue(source.legOpeningMin, defaults.legOpeningMin),
      legOpeningMax: normalizeMeasurementValue(source.legOpeningMax, defaults.legOpeningMax),
    };
  }

  return defaults;
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
    return normalized.length > 0 ? normalized : getDefaultSellerAccounts();
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
    if (storedDefaultsSignature !== CATEGORY_FILTER_DEFAULTS_SIGNATURE) {
      window.localStorage.removeItem(PAGE_FILTERS_STORAGE_KEY);
      window.localStorage.setItem(
        PAGE_FILTERS_VERSION_STORAGE_KEY,
        CATEGORY_FILTER_DEFAULTS_SIGNATURE
      );
      return getDefaultFiltersByPage();
    }

    const raw = window.localStorage.getItem(PAGE_FILTERS_STORAGE_KEY);
    if (!raw) {
      return getDefaultFiltersByPage();
    }

    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      return getDefaultFiltersByPage();
    }

    return Object.fromEntries(
      CATEGORY_PAGES.map((page) => [page.id, normalizePageFilters(page.id, parsed[page.id])])
    );
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

  const storedValue = window.localStorage.getItem(ACTIVE_PAGE_STORAGE_KEY);
  return resolvePageId(storedValue);
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
  return Number.isNaN(parsed) ? fallback : parsed;
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

const formatResultMeasurement = (value, label) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return null;
  }

  const display = Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1);
  return `${display}" ${label}`;
};

const buildSearchPayload = (page, filters, sellerUsername, searchId) => {
  const payload = {
    category: page.id,
    seller: sellerUsername,
    groups: page.group,
    gender: 'male',
    maxItems: 40,
    maxLinks: 128,
    maxScrolls: 16,
    parseWorkers: 4,
    searchId,
  };

  if (page.mode === 'measurements') {
    payload.measurements = {
      first: parseMeasurementNumber(filters.first, null),
      second: parseMeasurementNumber(filters.second, null),
    };
    payload.p2pTolerance = parseMeasurementNumber(filters.firstTolerance, 0.5);
    payload.lengthTolerance = parseMeasurementNumber(filters.secondTolerance, 1.25);
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

    payload.bottomsMeasurements = {
      waist: normalizeMinMax(filters.waistMin, filters.waistMax, page.defaults.waistMin, page.defaults.waistMax),
      inseamRise: normalizeMinMax(
        filters.inseamRiseMin,
        filters.inseamRiseMax,
        page.defaults.inseamRiseMin,
        page.defaults.inseamRiseMax,
      ),
      legOpening: normalizeMinMax(
        filters.legOpeningMin,
        filters.legOpeningMax,
        page.defaults.legOpeningMin,
        page.defaults.legOpeningMax,
      ),
    };
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

function App() {
  const initialSellerAccounts = readStoredSellerAccounts();
  const searchRegistryRef = useRef(new Map());
  const searchQueueRef = useRef([]);
  const batchCounterRef = useRef(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [sellerManagerOpen, setSellerManagerOpen] = useState(false);
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
  const currentWorkspace = pageWorkspaces[activePage.id] || { sellerRows: [] };
  const currentFilters = pageFilters[activePage.id] || getDefaultPageFilters(activePage.id);
  const sellerCount = sellerAccounts.length;
  const activeSearchCount = currentWorkspace.sellerRows.filter((row) => row.loading).length;
  const queuedCountForPage = currentWorkspace.sellerRows.filter((row) => row.progress?.phase === 'queued').length;
  const totalHits = currentWorkspace.sellerRows.reduce((sum, row) => sum + row.results.length, 0);
  const pageIsLoading = activeSearchCount > 0 || queuedCountForPage > 0;
  const queuePositionByKey = new Map(
    queueState.pending.map((task, index) => [task.key, index + 1])
  );
  let globalQueueStatus = null;
  if (queueState.active.length > 0 && queueState.pending.length > 0) {
    globalQueueStatus = `${queueState.active.length} active • ${queueState.pending.length} paused`;
  } else if (queueState.active.length > 0) {
    globalQueueStatus = `${queueState.active.length} active`;
  } else if (queueState.pending.length > 0) {
    globalQueueStatus = `${queueState.pending.length} queued`;
  }

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

    window.localStorage.setItem(ACTIVE_PAGE_STORAGE_KEY, activePageId);
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
    if (!sellerManagerOpen || typeof window === 'undefined') {
      return undefined;
    }

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        setSellerManagerOpen(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
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
    if (!task || task.source !== 'batch') {
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
    setPageWorkspaces((prev) => ({
      ...prev,
      [pageId]: {
        sellerRows: prev[pageId].sellerRows.map((row) =>
          row.seller === sellerUsername ? { ...row, ...updates } : row
        ),
      },
    }));
  };

  const addSellerResult = (pageId, sellerUsername, item) => {
    setPageWorkspaces((prev) => ({
      ...prev,
      [pageId]: {
        sellerRows: prev[pageId].sellerRows.map((row) => {
          if (row.seller !== sellerUsername) {
            return row;
          }

          if (row.results.some((result) => result.url === item.url)) {
            return row;
          }

          return { ...row, results: [...row.results, item] };
        }),
      },
    }));
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
      results: [],
      progress: message ? createProgressState({ phase: 'idle', message }) : null,
    });
  };

  const queueLowParseRetry = (task, summary) => {
    const processed = Number(summary?.processed) || 0;
    const total = Number(summary?.total) || 0;
    const retryTask = {
      ...task,
      resetResults: false,
      lowParseRetryCount: (task.lowParseRetryCount || 0) + 1,
    };

    if (!searchQueueRef.current.some((queuedTask) => queuedTask.key === retryTask.key)) {
      searchQueueRef.current = [...searchQueueRef.current, retryTask];
    }

    updateSellerRow(task.pageId, task.sellerUsername, {
      loading: false,
      controller: null,
      searchId: '',
      searchTask: null,
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

    if (!username) {
      setSellerForm((prev) => ({
        ...prev,
        error: 'Enter a seller username to add.',
      }));
      return;
    }

    let didChange = false;

    setSellerAccounts((prev) => {
      const existingAccount = prev.find((account) => account.username === username);
      if (existingAccount) {
        if (!displayName || displayName === existingAccount.name) {
          return prev;
        }

        didChange = true;
        return prev.map((account) =>
          account.username === username ? { ...account, name: displayName } : account
        );
      }

      didChange = true;
      return [...prev, { username, name: displayName || username }];
    });

    if (!didChange) {
      setSellerForm((prev) => ({
        ...prev,
        error: 'That seller is already in your list.',
      }));
      return;
    }

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

    await cancelSearch(searchId);

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
    if (
      searchRegistryRef.current.size > 0 ||
      searchQueueRef.current.length === 0
    ) {
      syncQueueState();
      return;
    }

    const [nextTask, ...remainingTasks] = searchQueueRef.current;
    searchQueueRef.current = remainingTasks;
    syncQueueState();
    void executeSellerSearch(nextTask).then((outcome) => handleSearchCompletion(nextTask, outcome));
  };

  const handleSearchCompletion = () => {
    syncQueueState();
    resumePendingTasks();
  };

  const executeSellerSearch = async (incomingTask) => {
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
    if (task.resetResults !== false) {
      sellerRowUpdate.results = [];
    }
    updateSellerRow(pageId, sellerUsername, sellerRowUpdate);
    syncQueueState();

    await streamSearch({
      payload,
      controller,
      onMatch: (evt) => addSellerResult(pageId, sellerUsername, evt.item),
      onProgress: (progress) => {
        updateSellerRow(pageId, sellerUsername, { progress });
      },
      onMeta: (meta) => updateSellerRow(pageId, sellerUsername, {
        progress: createProgressState({
          phase: 'collecting',
          total: meta.total,
          matches: getSellerRow(pageId, sellerUsername)?.results?.length || 0,
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

    void executeSellerSearch(task).then((outcome) => handleSearchCompletion(task, outcome));
    return true;
  };

  const reconnectPersistedSearches = () => {
    Object.entries(pageWorkspacesRef.current).forEach(([pageId, workspace]) => {
      (workspace?.sellerRows || []).forEach((row) => {
        if (!row.searchId) {
          return;
        }

        const savedTask = row.searchTask || {};
        const task = {
          ...savedTask,
          key: searchKeyFor(pageId, row.seller),
          pageId,
          sellerUsername: row.seller,
          filters: normalizePageFilters(
            pageId,
            savedTask.filters ?? pageFilters[pageId]
          ),
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
    queueSellerSearch(pageId, sellerUsername, { source: 'manual' });
  };

  const startSearchAllForPage = async (pageId) => {
    const batchId = nextBatchId('batch');
    const page = CATEGORY_PAGE_MAP[pageId];
    const filters = normalizePageFilters(pageId, pageFilters[pageId]);
    const searchesToStart = pageWorkspaces[pageId].sellerRows
      .map((row) => row.seller)
      .filter((sellerUsername) => !isSellerScheduled(pageId, sellerUsername))
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

    try {
      await startSearchBatch(searchesToStart.map((entry) => entry.payload));
    } catch (error) {
      console.warn('[Search] Batch registration failed; streams will retry:', error);
    }

    searchesToStart.forEach(({ sellerUsername, searchId }) => {
      queueSellerSearch(pageId, sellerUsername, {
        source: 'batch',
        batchId,
        filters,
        searchId,
      });
    });
  };

  const cancelPageSearches = async (pageId) => {
    removeQueuedTasks((task) => task.pageId === pageId);

    const activeSellers = pageWorkspaces[pageId].sellerRows
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
    if (activePage.mode === 'measurements') {
      return (
        <>
          <input
            className="input-dark"
            placeholder="P2P (in)"
            value={currentFilters.first}
            onChange={(event) => handlePageFilterChange(activePage.id, 'first', event.target.value)}
          />
          <input
            className="input-dark input-small"
            placeholder="± P2P"
            value={currentFilters.firstTolerance}
            onChange={(event) => handlePageFilterChange(activePage.id, 'firstTolerance', event.target.value)}
          />
          <input
            className="input-dark"
            placeholder="Length (in)"
            value={currentFilters.second}
            onChange={(event) => handlePageFilterChange(activePage.id, 'second', event.target.value)}
          />
          <input
            className="input-dark input-small"
            placeholder="± Len"
            value={currentFilters.secondTolerance}
            onChange={(event) => handlePageFilterChange(activePage.id, 'secondTolerance', event.target.value)}
          />
        </>
      );
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

    if (activePage.mode === 'bottomMeasurements') {
      return (
        <>
          <label className="filter-label">
            <span>Waist</span>
            <div className="filter-range-pair">
              <input
                className="input-dark input-small"
                placeholder="Min"
                value={currentFilters.waistMin}
                onChange={(event) => handlePageFilterChange(activePage.id, 'waistMin', event.target.value)}
              />
              <input
                className="input-dark input-small"
                placeholder="Max"
                value={currentFilters.waistMax}
                onChange={(event) => handlePageFilterChange(activePage.id, 'waistMax', event.target.value)}
              />
            </div>
          </label>
          <label className="filter-label">
            <span>Inseam + Rise</span>
            <div className="filter-range-pair">
              <input
                className="input-dark input-small"
                placeholder="Min"
                value={currentFilters.inseamRiseMin}
                onChange={(event) => handlePageFilterChange(activePage.id, 'inseamRiseMin', event.target.value)}
              />
              <input
                className="input-dark input-small"
                placeholder="Max"
                value={currentFilters.inseamRiseMax}
                onChange={(event) => handlePageFilterChange(activePage.id, 'inseamRiseMax', event.target.value)}
              />
            </div>
          </label>
          <label className="filter-label">
            <span>Leg Opening</span>
            <div className="filter-range-pair">
              <input
                className="input-dark input-small"
                placeholder="Min"
                value={currentFilters.legOpeningMin}
                onChange={(event) => handlePageFilterChange(activePage.id, 'legOpeningMin', event.target.value)}
              />
              <input
                className="input-dark input-small"
                placeholder="Max"
                value={currentFilters.legOpeningMax}
                onChange={(event) => handlePageFilterChange(activePage.id, 'legOpeningMax', event.target.value)}
              />
            </div>
          </label>
        </>
      );
    }

    return (
      <span className="workspace-pill muted">
        No size filter
      </span>
    );
  };

  return (
    <div className="app-dark-ui app-home-shell">
      <ReconnectSearches onReconnect={reconnectPersistedSearches} />
      <div
        className={`seller-manager-backdrop ${sellerManagerOpen ? 'open' : ''}`}
        onClick={() => setSellerManagerOpen(false)}
      />

      <aside
        className={`seller-manager-drawer ${sellerManagerOpen ? 'open' : ''}`}
        aria-hidden={!sellerManagerOpen}
      >
        <div className="seller-manager-header">
          <div>
            <div className="seller-manager-kicker">Shared sellers</div>
            <h2>Manage sellers</h2>
            <p className="following-description">
              Add or remove the accounts searched across every category page. Your
              seller list stays local to this browser.
            </p>
          </div>
          <button
            type="button"
            className="seller-manager-close"
            onClick={() => setSellerManagerOpen(false)}
            aria-label="Close seller manager"
          >
            ×
          </button>
        </div>

        <div className="seller-manager-form">
          <input
            className="input-dark"
            placeholder="@username or shop link"
            value={sellerForm.username}
            onChange={(event) => handleSellerFormInput('username', event.target.value)}
          />
          <input
            className="input-dark"
            placeholder="Display name (optional)"
            value={sellerForm.displayName}
            onChange={(event) => handleSellerFormInput('displayName', event.target.value)}
          />
          <button type="button" className="search-btn" onClick={addSellerAccount}>
            Add seller
          </button>
        </div>

        {sellerForm.error && (
          <div className="row-error seller-manager-error">
            {sellerForm.error}
          </div>
        )}

        <div className="seller-manager-list">
          {sellerAccounts.length === 0 ? (
            <div className="seller-manager-empty">
              No sellers saved yet. Add one to start building your category pages.
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
                  className="search-btn secondary small"
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
            className="search-btn secondary"
            onClick={resetSellerAccounts}
          >
            Reset sellers
          </button>
          <button
            type="button"
            className="search-btn secondary"
            onClick={() => setSellerManagerOpen(false)}
          >
            Close
          </button>
        </div>
      </aside>

      <aside className="sidebar category-sidebar">
        <div className="sidebar-title">Debot</div>
        <nav className="sidebar-nav">
          <ul>
            {CATEGORY_PAGES.map((page) => (
              <li key={page.id} className={page.id === activePage.id ? 'active' : ''}>
                <button
                  type="button"
                  className="sidebar-nav-button"
                  onClick={() => switchPage(page.id)}
                >
                  <span className="sidebar-icon">{page.shortLabel}</span>
                  <span>{page.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      <main className="main-content home-main-content">
        <div className="following-container home-workspace">
          <div className="following-header home-header">
            <div className="home-header-copy">
              <div className="home-header-kicker">Depop category workspace</div>
              <h1>{activePage.headline}</h1>
              <p className="following-description">
                {activePage.description}
              </p>
            </div>

            <div className="home-header-actions">
              <button
                type="button"
                className="search-btn secondary"
                onClick={() => setSellerManagerOpen(true)}
              >
                Manage sellers
              </button>
              <span className="workspace-pill">
                {sellerCount} sellers • {totalHits} hits
              </span>
              {globalQueueStatus && (
                <span className="workspace-pill muted">
                  {globalQueueStatus}
                </span>
              )}
            </div>
          </div>

          <div className="following-inputs home-controls">
            <div className="home-controls-fields">
              {renderFilterControls()}
            </div>

            <div className="home-controls-actions">
              <button
                type="button"
                className="search-btn secondary"
                onClick={() => resetPageFilters(activePage.id)}
              >
                Reset filters
              </button>

              {pageIsLoading ? (
                <button
                  type="button"
                  className="search-btn stop"
                  onClick={() => cancelPageSearches(activePage.id)}
                >
                  Stop Page
                </button>
              ) : (
                <button
                  type="button"
                  className="search-btn"
                  onClick={() => startSearchAllForPage(activePage.id)}
                  disabled={currentWorkspace.sellerRows.length === 0}
                >
                  Search All Sellers
                </button>
              )}
            </div>
          </div>

          {currentWorkspace.sellerRows.length === 0 ? (
            <div className="empty-state-card">
              <h3>No sellers saved</h3>
              <p>
                Open Manage sellers to add your first account, or reset back to the
                curated default list.
              </p>
            </div>
          ) : (
            <div className="following-sellers">
              {currentWorkspace.sellerRows.map((sellerRow) => (
                (() => {
                  const sellerKey = searchKeyFor(activePage.id, sellerRow.seller);
                  const queuePosition = queuePositionByKey.get(sellerKey) || null;
                  const progressDetails = getSellerProgressDetails(sellerRow, nowMs, queuePosition);
                  const isQueued = sellerRow.progress?.phase === 'queued';

                  return (
                    <div
                      key={`${activePage.id}-${sellerRow.seller}`}
                      className={`seller-row-card ${sellerRow.loading ? 'loading' : ''}`}
                    >
                      <div className="seller-header">
                    <div className="seller-info">
                      <a
                        href={buildSellerPageHref(sellerRow.seller, activePage)}
                        target="_blank"
                        rel="noreferrer"
                        className="seller-name"
                      >
                        {sellerRow.displayName || sellerRow.seller}
                      </a>
                      <span className="seller-username">@{sellerRow.seller}</span>
                    </div>

                    <div className="seller-controls">
                      {sellerRow.loading || isQueued ? (
                        <button
                          type="button"
                          className="search-btn stop small"
                          onClick={() => cancelSellerSearch(activePage.id, sellerRow.seller)}
                        >
                          {sellerRow.loading ? 'Stop' : 'Remove'}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="search-btn small"
                          onClick={() => startSellerSearch(activePage.id, sellerRow.seller)}
                        >
                          Search
                        </button>
                      )}

                      <div className="seller-status-stack">
                        <span className={`seller-status-badge ${getSellerStateTone(sellerRow)}`}>
                          {getSellerStateLabel(sellerRow)}
                        </span>
                        <span className="seller-status">
                          {formatSellerStatusLabel(sellerRow, queuePosition)}
                        </span>
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
                    <div className="results-scroll">
                      {sellerRow.results.map((result, index) => {
                        const metaParts = [];

                        if (result.sizeLabel) {
                          metaParts.push(`Size ${result.sizeLabel}`);
                        }
                        if (activePage.id === 'bottoms') {
                          const waistLabel = formatResultMeasurement(result.waist, 'waist');
                          if (waistLabel) {
                            metaParts.push(waistLabel);
                          }
                          const inseamLabel = formatResultMeasurement(result.inseam, 'inseam');
                          if (inseamLabel) {
                            metaParts.push(inseamLabel);
                          }
                          const inseamRiseLabel = formatResultMeasurement(result.inseamRise, 'inseam + rise');
                          if (inseamRiseLabel) {
                            metaParts.push(inseamRiseLabel);
                          }
                          const riseLabel = formatResultMeasurement(result.rise, 'rise');
                          if (riseLabel) {
                            metaParts.push(riseLabel);
                          }
                          const legOpeningLabel = formatResultMeasurement(result.legOpening, 'leg opening');
                          if (legOpeningLabel) {
                            metaParts.push(legOpeningLabel);
                          }
                        }
                        if (
                          result.p2p !== undefined &&
                          result.p2p !== null &&
                          result.p2p !== ''
                        ) {
                          metaParts.push(
                            `${result.p2p.toFixed ? result.p2p.toFixed(2) : result.p2p}" P2P`
                          );
                        }
                        if (
                          result.length !== undefined &&
                          result.length !== null &&
                          result.length !== ''
                        ) {
                          metaParts.push(
                            `${result.length.toFixed ? result.length.toFixed(2) : result.length}" Len`
                          );
                        }
                        const ageDays = getResultAgeDays(result);
                        if (ageDays !== null) {
                          metaParts.push(`${Math.round(ageDays)}d old`);
                        }

                        return (
                          <a
                            className="result-card"
                            key={`${result.url}-${index}`}
                            href={result.url || '#'}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {result.image ? (
                              <img className="result-img" src={result.image} alt="item" />
                            ) : (
                              <div className="result-img placeholder" />
                            )}
                            <div className="result-price">{result.price || ''}</div>
                            {metaParts.length > 0 && (
                              <div className="result-meta">
                                {metaParts.join(' · ')}
                              </div>
                            )}
                          </a>
                        );
                      })}
                    </div>
                  )}

                  {sellerRow.results.length === 0 && sellerRow.processed && !sellerRow.loading && !sellerRow.error && (
                    <div className="seller-empty-results">
                      No matching items found in the latest listings.
                    </div>
                  )}
                    </div>
                  );
                })()
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default App;
