import { useRef, useState } from 'react';
import Sidebar from './components/Sidebar';
import SearchRow from './components/SearchRow';
import { streamSearch, cancelSearch, makeSearchId } from './hooks/useStream';
import './App.css';
import './index.css';

const pages = [
  { name: 'Home', icon: '🏠' },
  { name: 'Following', icon: '👥' },
];

const DEFAULT_P2P = '21.5';
const DEFAULT_LENGTH = '27.25';
const DEFAULT_P2P_TOLERANCE = '0.5';
const DEFAULT_LENGTH_TOLERANCE = '1.25';

// Predefined list of followed sellers
const FOLLOWING_ACCOUNTS = [
  { name: 'OnTheMarkCo', username: 'onthemarkco' },
  { name: 'Reduce & Re - Use ♻️', username: 'reducereuseclothes' },
  { name: 'FORMER 💫', username: 'former_vintage' },
  { name: 'C Drizzly', username: 'crown_clothing' },
  { name: 'topleft vintage', username: 'topleftvintage' },
  { name: '50% OFF SALE', username: 'refinedvtg' },
  { name: 'NEW TMRW', username: 'newtmrw' },
  { name: 'Dante Amato', username: 'swag4lifee' },
  { name: 'Askar', username: 'asknr' },
  { name: '⚒Heavy Vintage ⚒', username: 'heavyvintage' },
  { name: 'garcia', username: 'iheartvintageco' },
  { name: 'Rainy Finds', username: 'rainyfinds_' },
  { name: 'Vtgthriftshit', username: 'vtgthriftshit' },
  { name: 'shitty plug', username: 'shittyvintageplug' },
  { name: 'Krown Vintage', username: 'krownvintage' },
  { name: '⊂(´･◡･⊂ )∘˚˳°', username: 'gwoodsgear' },
  { name: 'Thrift Jesus', username: 'thethriftingjesus' },
  { name: '5-Out Vintage 🇨🇦', username: '5outvintage' },
  { name: 'Way Back Vintage', username: 'waybackco' },
  { name: 'Rutland Retros', username: 'rutlandretros' },
  { name: 'Alex Vinson', username: 'flashyfashion' },
];

const defaultSearch = { 
  seller: 'flashyfashion', 
  length: DEFAULT_LENGTH, 
  p2p: DEFAULT_P2P, 
  p2pTolerance: DEFAULT_P2P_TOLERANCE, 
  lengthTolerance: DEFAULT_LENGTH_TOLERANCE, 
  results: [], 
  loading: false, 
  error: '', 
  errorCode: null,
  searchId: '', 
  controller: null, 
  progress: null 
};

const createProgressState = (overrides = {}) => ({
  processed: 0,
  total: 0,
  matches: 0,
  ...overrides,
});

const formatProgressLabel = (progress) => {
  if (!progress) {
    return null;
  }

  const processed = Number(progress.processed) || 0;
  const matches = Number(progress.matches) || 0;
  const total = Number(progress.total);

  if (Number.isFinite(total) && total > 0) {
    return `${matches} hits • ${processed}/${total} searched`;
  }

  return `${matches} hits • ${processed} searched`;
};

const formatSellerStatusLabel = (sellerRow) => {
  const progress = sellerRow?.progress;
  const hits = Number(progress?.matches);
  const processed = Number(progress?.processed) || 0;
  const total = Number(progress?.total) || 0;
  const safeHits = Number.isFinite(hits) ? hits : (sellerRow?.results?.length || 0);

  return `${safeHits} hits • ${processed} parsed / ${total} collected`;
};

function App() {
  const [activePage, setActivePage] = useState('Home');
  const [searchRows, setSearchRows] = useState([{ ...defaultSearch }]);
  const followingSearchesRef = useRef(new Map());
  
  // Following tab state - pre-populated with accounts
  const [followingState, setFollowingState] = useState({
    p2p: DEFAULT_P2P,
    length: DEFAULT_LENGTH,
    p2pTolerance: DEFAULT_P2P_TOLERANCE,
    lengthTolerance: DEFAULT_LENGTH_TOLERANCE,
    loading: false,
    sellerRows: FOLLOWING_ACCOUNTS.map(acc => ({
      seller: acc.username,
      displayName: acc.name,
      results: [],
      loading: false,
      processed: false,
      error: null,
      errorCode: null,
      searchId: '',
      controller: null,
      progress: null,
    })),
    progress: null,
  });

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
    setFollowingState(prev => ({
      ...prev,
      loading: followingSearchesRef.current.size > 0,
    }));
  };

  const registerFollowingSearch = (sellerUsername, searchId, controller) => {
    followingSearchesRef.current.set(sellerUsername, { searchId, controller });
    syncFollowingLoading();
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

  // Row state helpers
  const handleInput = (idx, field, value) => {
    setSearchRows((rows) => {
      const newRows = [...rows];
      newRows[idx] = { ...newRows[idx], [field]: value };
      return newRows;
    });
  };

  const addResult = (idx, item) => {
    setSearchRows((rows) => {
      const newRows = [...rows];
      const currentResults = newRows[idx].results || [];
      newRows[idx] = { ...newRows[idx], results: [...currentResults, item] };
      return newRows;
    });
  };

  const updateProgress = (idx, progress) => {
    setSearchRows((rows) => {
      const newRows = [...rows];
      newRows[idx] = { ...newRows[idx], progress };
      return newRows;
    });
  };

  const setRowState = (idx, updates) => {
    setSearchRows((rows) => {
      const newRows = [...rows];
      newRows[idx] = { ...newRows[idx], ...updates };
      return newRows;
    });
  };

  const addRow = () => {
    setSearchRows((rows) => [...rows, { ...defaultSearch, results: [], progress: null }]);
  };

  const addResultToSeller = (sellerName, item, measurements) => {
    setSearchRows((rows) => {
      const existingIdx = rows.findIndex(r => r.seller === sellerName);
      
      if (existingIdx !== -1) {
        const newRows = [...rows];
        const currentResults = newRows[existingIdx].results || [];
        if (currentResults.some(r => r.url === item.url)) return rows;
        
        newRows[existingIdx] = {
          ...newRows[existingIdx],
          results: [...currentResults, item]
        };
        return newRows;
      }
      
      return [...rows, {
        ...defaultSearch,
        seller: sellerName,
        p2p: measurements.p2p,
        length: measurements.length,
        results: [item],
        loading: false 
      }];
    });
  };

  // Build payload from row data
  const buildPayload = (row, searchId, isBrowse = false) => {
    const p2p = parseFloat(row.p2p);
    const length = parseFloat(row.length);
    const p2pTolerance = row.p2pTolerance === '' ? parseFloat(DEFAULT_P2P_TOLERANCE) : parseFloat(row.p2pTolerance);
    const lengthTolerance = row.lengthTolerance === '' ? parseFloat(DEFAULT_LENGTH_TOLERANCE) : parseFloat(row.lengthTolerance);
    
    return {
      category: 'tops',
      measurements: {
        first: isNaN(p2p) ? null : p2p,
        second: isNaN(length) ? null : length,
      },
      p2pTolerance: isNaN(p2pTolerance) ? parseFloat(DEFAULT_P2P_TOLERANCE) : p2pTolerance,
      lengthTolerance: isNaN(lengthTolerance) ? parseFloat(DEFAULT_LENGTH_TOLERANCE) : lengthTolerance,
      seller: isBrowse ? "" : (row.seller || null),
      maxItems: isBrowse ? 100 : 40,
      maxLinks: isBrowse ? 10000 : 1000,
      searchId,
    };
  };

  // Start seller-specific search
  const startStream = async (idx) => {
    const controller = new AbortController();
    const searchId = makeSearchId();
    const row = searchRows[idx];
    
    setRowState(idx, { 
      loading: true, 
      error: '', 
      errorCode: null,
      results: [], 
      controller, 
      searchId,
      progress: createProgressState({ phase: 'starting' }),
    });
    
    await streamSearch({
      payload: buildPayload(row, searchId, false),
      controller,
      onMatch: (evt) => addResult(idx, evt.item),
      onProgress: (progress) => updateProgress(idx, progress),
      onMeta: (meta) => updateProgress(idx, { 
        total: meta.total, 
        processed: 0, 
        matches: 0, 
        phase: 'scanning' 
      }),
      onError: (error) => {
        const { message, code } = getStreamErrorDetails(error);
        setRowState(idx, { 
          loading: false, 
          error: message,
          errorCode: code,
          controller: null,
          searchId: '',
        });
      },
      onDone: () => setRowState(idx, { loading: false, controller: null, searchId: '' }),
    });
  };

  // Browse all listings
  const startBrowse = async (idx) => {
    const controller = new AbortController();
    const searchId = makeSearchId();
    const row = searchRows[idx];
    
    setRowState(idx, { 
      loading: true, 
      error: '', 
      errorCode: null,
      results: [], 
      controller, 
      searchId,
      progress: createProgressState({ phase: 'starting' }),
    });
    
    await streamSearch({
      payload: buildPayload(row, searchId, true),
      controller,
      onMatch: (evt) => {
        if (evt.seller) {
          addResultToSeller(evt.seller, evt.item, { 
            p2p: row.p2p, 
            length: row.length 
          });
        }
      },
      onProgress: (progress) => updateProgress(idx, { ...progress, phase: 'browsing' }),
      onError: (error) => {
        const { message, code } = getStreamErrorDetails(error);
        setRowState(idx, { 
          loading: false, 
          error: message,
          errorCode: code,
          controller: null,
          searchId: '',
        });
      },
      onDone: () => setRowState(idx, { loading: false, controller: null, searchId: '' }),
    });
  };

  // Cancel active stream
  const cancelStream = async (idx) => {
    const row = searchRows[idx];
    
    await cancelSearch(row?.searchId);
    
    if (row?.controller) {
      try {
        row.controller.abort();
      } catch (e) {
        console.warn('[Cancel] Failed to abort controller:', e);
      }
    }
    
    setRowState(idx, { loading: false, controller: null, searchId: '', error: '', errorCode: null });
  };

  // Following tab handlers
  const handleFollowingInput = (field, value) => {
    setFollowingState(prev => ({ ...prev, [field]: value }));
  };

  // Update a specific seller row in the following state
  const updateFollowingSellerRow = (sellerUsername, updates) => {
    setFollowingState(prev => ({
      ...prev,
      sellerRows: prev.sellerRows.map(row =>
        row.seller === sellerUsername ? { ...row, ...updates } : row
      ),
    }));
  };

  // Add result to a specific seller in following
  const addFollowingResult = (sellerUsername, item) => {
    setFollowingState(prev => ({
      ...prev,
      sellerRows: prev.sellerRows.map(row => {
        if (row.seller === sellerUsername) {
          if (row.results.some(r => r.url === item.url)) return row;
          return { ...row, results: [...row.results, item] };
        }
        return row;
      }),
    }));
  };

  // Start searching a single seller in the following list
  const startFollowingSellerSearch = async (sellerUsername) => {
    if (followingSearchesRef.current.has(sellerUsername)) {
      return;
    }

    const controller = new AbortController();
    const searchId = makeSearchId();
    
    const p2p = parseFloat(followingState.p2p);
    const length = parseFloat(followingState.length);
    const p2pTolerance = followingState.p2pTolerance === '' ? parseFloat(DEFAULT_P2P_TOLERANCE) : parseFloat(followingState.p2pTolerance);
    const lengthTolerance = followingState.lengthTolerance === '' ? parseFloat(DEFAULT_LENGTH_TOLERANCE) : parseFloat(followingState.lengthTolerance);
    
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
        first: isNaN(p2p) ? null : p2p,
        second: isNaN(length) ? null : length,
      },
      p2pTolerance: isNaN(p2pTolerance) ? parseFloat(DEFAULT_P2P_TOLERANCE) : p2pTolerance,
      lengthTolerance: isNaN(lengthTolerance) ? parseFloat(DEFAULT_LENGTH_TOLERANCE) : lengthTolerance,
      seller: sellerUsername,
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
        progress: createProgressState({ total: meta.total })
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

  // Cancel a specific seller's search
  const cancelFollowingSellerSearch = async (sellerUsername) => {
    const activeSearch = followingSearchesRef.current.get(sellerUsername);
    const sellerRow = followingState.sellerRows.find(r => r.seller === sellerUsername);
    const searchId = activeSearch?.searchId || sellerRow?.searchId;
    if (!searchId && !activeSearch?.controller) return;
    
    await cancelSearch(searchId);
    
    if (activeSearch?.controller) {
      try {
        activeSearch.controller.abort();
      } catch (e) {
        console.warn('[Cancel] Failed to abort controller:', e);
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

  // Start searching ALL sellers in the following list
  const startBrowseAllFollowing = async () => {
    const sellersToStart = followingState.sellerRows
      .map(row => row.seller)
      .filter(seller => !followingSearchesRef.current.has(seller));

    await Promise.all(sellersToStart.map(seller => startFollowingSellerSearch(seller)));
  };

  // Cancel ALL following searches
  const cancelAllFollowingSearches = async () => {
    const activeSellers = Array.from(followingSearchesRef.current.keys());
    await Promise.all(activeSellers.map(seller => cancelFollowingSellerSearch(seller)));
  };

  return (
    <div className="app-dark-ui">
      <Sidebar pages={pages} activePage={activePage} setActivePage={setActivePage} />
      <main className="main-content">
        {activePage === 'Home' && (
          <div className="rows-container">
            {searchRows.map((row, idx) => (
              <SearchRow
                key={idx}
                row={row}
                idx={idx}
                progressLabel={formatProgressLabel(row.progress)}
                handleInput={handleInput}
                startStream={startStream}
                cancelStream={cancelStream}
                startBrowse={startBrowse}
              />
            ))}
            <div className="add-row-container">
              <button className="add-row-btn" onClick={addRow} title="Add search row" aria-label="Add search row">
                +
              </button>
            </div>
          </div>
        )}
        
        {activePage === 'Following' && (
          <div className="following-container">
            <div className="following-header">
              <h2>Browse Following</h2>
              <p className="following-description">
                Browse all followed accounts for matching items.
              </p>
            </div>
            
            <div className="following-inputs">
              <input
                className="input-dark"
                placeholder="P2P (in)"
                value={followingState.p2p}
                onChange={e => handleFollowingInput('p2p', e.target.value)}
              />
              <input
                className="input-dark input-small"
                placeholder="± P2P"
                value={followingState.p2pTolerance}
                onChange={e => handleFollowingInput('p2pTolerance', e.target.value)}
              />
              <input
                className="input-dark"
                placeholder="Length (in)"
                value={followingState.length}
                onChange={e => handleFollowingInput('length', e.target.value)}
              />
              <input
                className="input-dark input-small"
                placeholder="± Len"
                value={followingState.lengthTolerance}
                onChange={e => handleFollowingInput('lengthTolerance', e.target.value)}
              />
              
              {followingState.loading ? (
                <button className="search-btn stop" onClick={cancelAllFollowingSearches}>Stop All</button>
              ) : (
                <button className="search-btn" onClick={startBrowseAllFollowing}>Browse All Following</button>
              )}
            </div>
            
            <div className="following-sellers">
              {followingState.sellerRows.map((sellerRow) => (
                <div key={sellerRow.seller} className={`seller-row-card ${sellerRow.loading ? 'loading' : ''}`}>
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
                          className="search-btn stop small" 
                          onClick={() => cancelFollowingSellerSearch(sellerRow.seller)}
                        >
                          Stop
                        </button>
                      ) : (
                        <button 
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
                      {sellerRow.results.map((res, i) => {
                        const metaParts = [];
                        if (res.p2p !== undefined && res.p2p !== null && res.p2p !== '') {
                          metaParts.push(`${res.p2p.toFixed ? res.p2p.toFixed(2) : res.p2p}" P2P`);
                        }
                        if (res.length !== undefined && res.length !== null && res.length !== '') {
                          metaParts.push(`${res.length.toFixed ? res.length.toFixed(2) : res.length}" Len`);
                        }
                        if (typeof res.ageDays === 'number' && !Number.isNaN(res.ageDays)) {
                          metaParts.push(`${Math.round(res.ageDays)}d old`);
                        }
                        
                        return (
                          <a className="result-card" key={`${res.url}-${i}`} href={res.url || '#'} target="_blank" rel="noreferrer">
                            {res.image ? (
                              <img className="result-img" src={res.image} alt="item" />
                            ) : (
                              <div className="result-img placeholder" />
                            )}
                            <div className="result-price">{res.price || ''}</div>
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
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
