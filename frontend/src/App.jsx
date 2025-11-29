import { useState } from 'react';
import Sidebar from './components/Sidebar';
import SearchRow from './components/SearchRow';
import { streamSearch, cancelSearch, makeSearchId } from './hooks/useStream';
import './App.css';
import './index.css';

const pages = [
  { name: 'Home', icon: '🏠' },
];

const defaultSearch = { 
  seller: 'flashyfashion', 
  length: '27', 
  p2p: '21.5', 
  p2pTolerance: '1', 
  lengthTolerance: '0.5', 
  results: [], 
  loading: false, 
  error: '', 
  searchId: '', 
  controller: null, 
  progress: null 
};

function App() {
  const [activePage, setActivePage] = useState('Home');
  const [searchRows, setSearchRows] = useState([{ ...defaultSearch }]);

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
    const p2pTolerance = row.p2pTolerance === '' ? 1 : parseFloat(row.p2pTolerance);
    const lengthTolerance = row.lengthTolerance === '' ? 0.5 : parseFloat(row.lengthTolerance);
    
    return {
      category: 'tops',
      measurements: {
        first: isNaN(p2p) ? null : p2p,
        second: isNaN(length) ? null : length,
      },
      p2pTolerance: isNaN(p2pTolerance) ? 1 : p2pTolerance,
      lengthTolerance: isNaN(lengthTolerance) ? 0.5 : lengthTolerance,
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
      results: [], 
      controller, 
      searchId,
      progress: null 
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
      onError: (message) => setRowState(idx, { 
        loading: false, 
        error: message, 
        controller: null 
      }),
      onDone: () => setRowState(idx, { loading: false, controller: null }),
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
      results: [], 
      controller, 
      searchId,
      progress: null 
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
      onError: (message) => setRowState(idx, { 
        loading: false, 
        error: message, 
        controller: null 
      }),
      onDone: () => setRowState(idx, { loading: false, controller: null }),
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
    
    setRowState(idx, { loading: false, controller: null });
  };

  return (
    <div className="app-dark-ui">
      <Sidebar pages={pages} activePage={activePage} setActivePage={setActivePage} />
      <main className="main-content">
        <div className="rows-container">
          {searchRows.map((row, idx) => (
            <SearchRow
              key={idx}
              row={row}
              idx={idx}
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
      </main>
    </div>
  );
}

export default App;
