import { useEffect, useRef, useState } from 'react';
import { cancelSearch, makeSearchId, streamSearch } from './hooks/useStream';
import { DEFAULT_FOLLOWING_ACCOUNTS } from './data/defaultSellers';
import './App.css';
import './index.css';

const DEFAULT_P2P = '21.5';
const DEFAULT_LENGTH = '27.25';
const DEFAULT_P2P_TOLERANCE = '0.5';
const DEFAULT_LENGTH_TOLERANCE = '1.25';
const FOLLOWING_STORAGE_KEY = 'debot.followingAccounts.v1';

const createProgressState = (overrides = {}) => ({
  processed: 0,
  total: 0,
  matches: 0,
  ...overrides,
});

const formatSellerStatusLabel = (sellerRow) => {
  const progress = sellerRow?.progress;
  const hits = Number(progress?.matches);
  const processed = Number(progress?.processed) || 0;
  const total = Number(progress?.total) || 0;
  const safeHits = Number.isFinite(hits) ? hits : (sellerRow?.results?.length || 0);

  if (!sellerRow?.loading && !sellerRow?.error && !sellerRow?.processed && safeHits === 0 && processed === 0 && total === 0) {
    return 'Ready';
  }

  return `${safeHits} hits • ${processed} parsed / ${total} collected`;
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

const normalizeSellerGroups = (groups) => {
  const source = Array.isArray(groups) ? groups : [groups];
  const normalized = [];
  const seen = new Set();

  for (const group of source) {
    const clean = String(group || '').trim();
    if (!clean || seen.has(clean)) {
      continue;
    }
    seen.add(clean);
    normalized.push(clean);
  }

  return normalized.length > 0 ? normalized : ['tops'];
};

const normalizeSellerAccount = (account) => {
  const username = normalizeSellerUsername(
    account?.username ?? account?.seller ?? ''
  );
  if (!username) {
    return null;
  }

  const name = normalizeSellerDisplayName(
    account?.name ?? account?.displayName ?? ''
  );

  return {
    username,
    name: name || username,
    groups: normalizeSellerGroups(account?.groups),
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
  groups: normalizeSellerGroups(account.groups),
  results: existingRow?.results || [],
  loading: existingRow?.loading || false,
  processed: existingRow?.processed || false,
  error: existingRow?.error || null,
  errorCode: existingRow?.errorCode || null,
  searchId: existingRow?.searchId || '',
  controller: existingRow?.controller || null,
  progress: existingRow?.progress || null,
});

const buildSellerRows = (accounts, existingRows = []) => {
  const existingByUsername = new Map(existingRows.map((row) => [row.seller, row]));

  return dedupeSellerAccounts(accounts).map((account) =>
    createSellerRow(account, existingByUsername.get(account.username))
  );
};

const extractSellerAccounts = (sellerRows) =>
  sellerRows.map((row) => ({
    username: row.seller,
    name: row.displayName || row.seller,
    groups: normalizeSellerGroups(row.groups),
  }));

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
    console.warn('[Home] Failed to read saved seller accounts:', error);
    return getDefaultSellerAccounts();
  }
};

function App() {
  const followingSearchesRef = useRef(new Map());
  const [sellerManagerOpen, setSellerManagerOpen] = useState(false);
  const [sellerForm, setSellerForm] = useState({
    username: '',
    displayName: '',
    error: '',
  });
  const [followingState, setFollowingState] = useState(() => ({
    p2p: DEFAULT_P2P,
    length: DEFAULT_LENGTH,
    p2pTolerance: DEFAULT_P2P_TOLERANCE,
    lengthTolerance: DEFAULT_LENGTH_TOLERANCE,
    loading: false,
    sellerRows: buildSellerRows(readStoredSellerAccounts()),
  }));

  const sellerAccounts = extractSellerAccounts(followingState.sellerRows);
  const sellerAccountsSnapshot = JSON.stringify(sellerAccounts);
  const sellerCount = followingState.sellerRows.length;
  const activeSearchCount = followingState.sellerRows.filter((row) => row.loading).length;
  const totalHits = followingState.sellerRows.reduce(
    (sum, row) => sum + row.results.length,
    0
  );

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    try {
      window.localStorage.setItem(FOLLOWING_STORAGE_KEY, sellerAccountsSnapshot);
    } catch (error) {
      console.warn('[Home] Failed to persist seller accounts:', error);
    }
  }, [sellerAccountsSnapshot]);

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

  const getStreamErrorDetails = (error) => {
    if (typeof error === 'string') {
      return { message: error, code: null };
    }

    return {
      message: error?.message || 'Stream error',
      code: error?.code || null,
    };
  };

  const syncFollowingLoading = () => {
    setFollowingState((prev) => ({
      ...prev,
      loading: followingSearchesRef.current.size > 0,
    }));
  };

  const registerFollowingSearch = (sellerUsername, searchId, controller) => {
    followingSearchesRef.current.set(sellerUsername, { searchId, controller });
    syncFollowingLoading();
  };

  const updateFollowingSellerRow = (sellerUsername, updates) => {
    setFollowingState((prev) => ({
      ...prev,
      sellerRows: prev.sellerRows.map((row) =>
        row.seller === sellerUsername ? { ...row, ...updates } : row
      ),
    }));
  };

  const finishFollowingSearch = (sellerUsername, searchId, updates) => {
    const activeSearch = followingSearchesRef.current.get(sellerUsername);
    if (!activeSearch || activeSearch.searchId !== searchId) {
      return false;
    }

    followingSearchesRef.current.delete(sellerUsername);
    syncFollowingLoading();
    updateFollowingSellerRow(sellerUsername, {
      controller: null,
      searchId: '',
      ...updates,
    });
    return true;
  };

  const handleFollowingInput = (field, value) => {
    setFollowingState((prev) => ({ ...prev, [field]: value }));
  };

  const handleSellerFormInput = (field, value) => {
    setSellerForm((prev) => ({
      ...prev,
      [field]: value,
      error: '',
    }));
  };

  const addFollowingResult = (sellerUsername, item) => {
    setFollowingState((prev) => ({
      ...prev,
      sellerRows: prev.sellerRows.map((row) => {
        if (row.seller !== sellerUsername) {
          return row;
        }

        if (row.results.some((result) => result.url === item.url)) {
          return row;
        }

        return { ...row, results: [...row.results, item] };
      }),
    }));
  };

  const replaceSellerAccounts = (accounts) => {
    setFollowingState((prev) => ({
      ...prev,
      sellerRows: buildSellerRows(accounts, prev.sellerRows),
    }));
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

    setFollowingState((prev) => {
      const existingRow = prev.sellerRows.find((row) => row.seller === username);

      if (existingRow) {
        if (!displayName || displayName === existingRow.displayName) {
          return prev;
        }

        didChange = true;
        return {
          ...prev,
          sellerRows: prev.sellerRows.map((row) =>
            row.seller === username ? { ...row, displayName } : row
          ),
        };
      }

      didChange = true;
      const nextAccounts = [
        ...extractSellerAccounts(prev.sellerRows),
        { username, name: displayName || username, groups: ['tops'] },
      ];

      return {
        ...prev,
        sellerRows: buildSellerRows(nextAccounts, prev.sellerRows),
      };
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

  const removeSellerAccount = async (sellerUsername) => {
    const normalizedUsername = normalizeSellerUsername(sellerUsername);
    if (!normalizedUsername) {
      return;
    }

    if (followingSearchesRef.current.has(normalizedUsername)) {
      await cancelFollowingSellerSearch(normalizedUsername);
    }

    setFollowingState((prev) => ({
      ...prev,
      sellerRows: prev.sellerRows.filter((row) => row.seller !== normalizedUsername),
    }));
  };

  const resetSellerAccounts = async () => {
    const defaultAccounts = getDefaultSellerAccounts();
    const defaultUsernames = new Set(defaultAccounts.map((account) => account.username));

    const removedActiveSellers = followingState.sellerRows
      .filter(
        (row) =>
          !defaultUsernames.has(row.seller) &&
          followingSearchesRef.current.has(row.seller)
      )
      .map((row) => row.seller);

    if (removedActiveSellers.length > 0) {
      await Promise.all(
        removedActiveSellers.map((sellerUsername) =>
          cancelFollowingSellerSearch(sellerUsername)
        )
      );
    }

    replaceSellerAccounts(defaultAccounts);
    setSellerForm({
      username: '',
      displayName: '',
      error: '',
    });
  };

  const startFollowingSellerSearch = async (sellerUsername) => {
    if (followingSearchesRef.current.has(sellerUsername)) {
      return;
    }

    const controller = new AbortController();
    const searchId = makeSearchId();

    const p2p = parseFloat(followingState.p2p);
    const length = parseFloat(followingState.length);
    const p2pTolerance = followingState.p2pTolerance === ''
      ? parseFloat(DEFAULT_P2P_TOLERANCE)
      : parseFloat(followingState.p2pTolerance);
    const lengthTolerance = followingState.lengthTolerance === ''
      ? parseFloat(DEFAULT_LENGTH_TOLERANCE)
      : parseFloat(followingState.lengthTolerance);

    registerFollowingSearch(sellerUsername, searchId, controller);
    updateFollowingSellerRow(sellerUsername, {
      loading: true,
      error: null,
      errorCode: null,
      results: [],
      searchId,
      controller,
      processed: false,
      progress: createProgressState({ phase: 'starting' }),
    });

    const payload = {
      category: 'tops',
      measurements: {
        first: Number.isNaN(p2p) ? null : p2p,
        second: Number.isNaN(length) ? null : length,
      },
      p2pTolerance: Number.isNaN(p2pTolerance)
        ? parseFloat(DEFAULT_P2P_TOLERANCE)
        : p2pTolerance,
      lengthTolerance: Number.isNaN(lengthTolerance)
        ? parseFloat(DEFAULT_LENGTH_TOLERANCE)
        : lengthTolerance,
      seller: sellerUsername,
      groups: normalizeSellerGroups(
        followingState.sellerRows.find((row) => row.seller === sellerUsername)?.groups
      ),
      maxItems: 40,
      maxLinks: 1000,
      searchId,
    };

    await streamSearch({
      payload,
      controller,
      onMatch: (evt) => addFollowingResult(sellerUsername, evt.item),
      onProgress: (progress) => updateFollowingSellerRow(sellerUsername, { progress }),
      onMeta: (meta) => updateFollowingSellerRow(sellerUsername, {
        progress: createProgressState({ total: meta.total }),
      }),
      onError: (error) => {
        const { message, code } = getStreamErrorDetails(error);
        finishFollowingSearch(sellerUsername, searchId, {
          loading: false,
          error: message,
          errorCode: code,
          processed: true,
        });
      },
      onDone: () => {
        finishFollowingSearch(sellerUsername, searchId, {
          loading: false,
          processed: true,
        });
      },
    });
  };

  const cancelFollowingSellerSearch = async (sellerUsername) => {
    const activeSearch = followingSearchesRef.current.get(sellerUsername);
    const sellerRow = followingState.sellerRows.find((row) => row.seller === sellerUsername);
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

    followingSearchesRef.current.delete(sellerUsername);
    syncFollowingLoading();
    updateFollowingSellerRow(sellerUsername, {
      loading: false,
      controller: null,
      searchId: '',
      error: null,
      errorCode: null,
    });
  };

  const startBrowseAllFollowing = async () => {
    const sellersToStart = followingState.sellerRows
      .map((row) => row.seller)
      .filter((sellerUsername) => !followingSearchesRef.current.has(sellerUsername));

    if (sellersToStart.length === 0) {
      return;
    }

    await Promise.all(sellersToStart.map((sellerUsername) => startFollowingSellerSearch(sellerUsername)));
  };

  const cancelAllFollowingSearches = async () => {
    const activeSellers = Array.from(followingSearchesRef.current.keys());
    await Promise.all(
      activeSellers.map((sellerUsername) => cancelFollowingSellerSearch(sellerUsername))
    );
  };

  return (
    <div className="app-dark-ui app-home-shell">
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
            <div className="seller-manager-kicker">Saved sellers</div>
            <h2>Manage sellers</h2>
            <p className="following-description">
              Add or remove the accounts included in Home search. Your changes stay
              local to this browser.
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
          {followingState.sellerRows.length === 0 ? (
            <div className="seller-manager-empty">
              No sellers saved yet. Add one to start building your Home search list.
            </div>
          ) : (
            followingState.sellerRows.map((sellerRow) => (
              <div key={sellerRow.seller} className="seller-manager-item">
                <div className="seller-manager-item-copy">
                  <span className="seller-manager-item-name">
                    {sellerRow.displayName || sellerRow.seller}
                  </span>
                  <span className="seller-manager-item-username">
                    @{sellerRow.seller}
                  </span>
                </div>
                <button
                  type="button"
                  className="search-btn secondary small"
                  onClick={() => removeSellerAccount(sellerRow.seller)}
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
            Reset to defaults
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

      <main className="main-content home-main-content">
        <div className="following-container home-workspace">
          <div className="following-header home-header">
            <div>
              <h1>Home Search</h1>
              <p className="following-description">
                Search your curated seller list for measurement matches. The default
                Canada and east-coast leaning set loads automatically, and you can
                edit it anytime from the left drawer.
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
              {activeSearchCount > 0 && (
                <span className="workspace-pill muted">
                  {activeSearchCount} active
                </span>
              )}
            </div>
          </div>

          <div className="following-inputs home-controls">
            <input
              className="input-dark"
              placeholder="P2P (in)"
              value={followingState.p2p}
              onChange={(event) => handleFollowingInput('p2p', event.target.value)}
            />
            <input
              className="input-dark input-small"
              placeholder="± P2P"
              value={followingState.p2pTolerance}
              onChange={(event) => handleFollowingInput('p2pTolerance', event.target.value)}
            />
            <input
              className="input-dark"
              placeholder="Length (in)"
              value={followingState.length}
              onChange={(event) => handleFollowingInput('length', event.target.value)}
            />
            <input
              className="input-dark input-small"
              placeholder="± Len"
              value={followingState.lengthTolerance}
              onChange={(event) => handleFollowingInput('lengthTolerance', event.target.value)}
            />

            {followingState.loading ? (
              <button
                type="button"
                className="search-btn stop"
                onClick={cancelAllFollowingSearches}
              >
                Stop All
              </button>
            ) : (
              <button
                type="button"
                className="search-btn"
                onClick={startBrowseAllFollowing}
                disabled={followingState.sellerRows.length === 0}
              >
                Search All Sellers
              </button>
            )}
          </div>

          {followingState.sellerRows.length === 0 ? (
            <div className="empty-state-card">
              <h3>No sellers saved</h3>
              <p>
                Open Manage sellers to add your first account, or reset back to the
                curated default list.
              </p>
            </div>
          ) : (
            <div className="following-sellers">
              {followingState.sellerRows.map((sellerRow) => (
                <div
                  key={sellerRow.seller}
                  className={`seller-row-card ${sellerRow.loading ? 'loading' : ''}`}
                >
                  <div className="seller-header">
                    <div className="seller-info">
                      <a
                        href={`https://www.depop.com/${sellerRow.seller}/`}
                        target="_blank"
                        rel="noreferrer"
                        className="seller-name"
                      >
                        {sellerRow.displayName || sellerRow.seller}
                      </a>
                      <span className="seller-username">@{sellerRow.seller}</span>
                    </div>

                    <div className="seller-controls">
                      {sellerRow.loading ? (
                        <button
                          type="button"
                          className="search-btn stop small"
                          onClick={() => cancelFollowingSellerSearch(sellerRow.seller)}
                        >
                          Stop
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="search-btn small"
                          onClick={() => startFollowingSellerSearch(sellerRow.seller)}
                        >
                          Search
                        </button>
                      )}

                      <span className="seller-status">
                        {sellerRow.loading ? '⏳' : sellerRow.error ? '❌' : sellerRow.processed ? '✓' : ''}
                        {' '}
                        {formatSellerStatusLabel(sellerRow)}
                      </span>
                    </div>
                  </div>

                  {sellerRow.error && (
                    <div className="row-error seller-row-error">
                      {sellerRow.error}
                    </div>
                  )}

                  {sellerRow.results.length > 0 && (
                    <div className="results-scroll">
                      {sellerRow.results.map((result, index) => {
                        const metaParts = [];
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
                        if (
                          typeof result.ageDays === 'number' &&
                          !Number.isNaN(result.ageDays)
                        ) {
                          metaParts.push(`${Math.round(result.ageDays)}d old`);
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
                                {metaParts.join(' | ')}
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
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default App;
