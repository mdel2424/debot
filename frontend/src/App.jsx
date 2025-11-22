import { useState } from 'react';
import Sidebar from './components/Sidebar';
import SearchRow from './components/SearchRow';
import './index.css';

const pages = [
  { name: 'Home', icon: '🏠' },
  // Add more pages here in the future
];

const defaultSearch = { seller: 'flashyfashion', length: '27', p2p: '21', tolerance: '1', results: [], loading: false, error: '', searchId: '', controller: null, progress: null };
const API_BASE = import.meta.env && import.meta.env.DEV ? 'http://127.0.0.1:8000' : '';

function App() {
  const [activePage, setActivePage] = useState('Home');
  const [searchRows, setSearchRows] = useState([{ ...defaultSearch }]);

  const handleInput = (idx, field, value) => {
    setSearchRows((rows) => {
      const newRows = [...rows];
      newRows[idx] = { ...newRows[idx], [field]: value };
      return newRows;
    });
  };

  const addResult = (idx, item) => {
    console.log('[UI] Adding result to row', idx, ':', item.url);
    setSearchRows((rows) => {
      const newRows = [...rows];
      const currentResults = newRows[idx].results || [];
      newRows[idx] = { 
        ...newRows[idx], 
        results: [...currentResults, item] 
      };
      console.log('[UI] New results count for row', idx, ':', newRows[idx].results.length);
      return newRows;
    });
  };

  const updateProgress = (idx, progress) => {
    setSearchRows((rows) => {
      const newRows = [...rows];
      newRows[idx] = { 
        ...newRows[idx], 
        progress: progress 
      };
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

  const startStream = async (idx) => {
    const makeId = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const controller = new AbortController();
    const searchId = makeId();
    
    // Initialize search state
    setRowState(idx, { 
      loading: true, 
      error: '', 
      results: [], 
      controller, 
      searchId,
      progress: null 
    });
    
    const row = searchRows[idx];
    
    try {
      const p2p = parseFloat(row.p2p);
      const length = parseFloat(row.length);
      const tolerance = row.tolerance === '' ? 0.5 : parseFloat(row.tolerance);
      const payload = {
        category: 'tops',
        measurements: {
          first: isNaN(p2p) ? null : p2p,
          second: isNaN(length) ? null : length,
        },
        tolerance: isNaN(tolerance) ? 0.5 : tolerance,
        seller: row.seller || null,
        maxItems: 40,
        maxLinks: 1000,
        searchId,
      };
      
      console.log('[SSE] Starting stream with payload:', payload);
      
      const res = await fetch(`${API_BASE}/api/search/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
        cache: 'no-store',
      });
      
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      
      if (!res.body) {
        throw new Error('No response body for streaming');
      }
      
      console.log('[SSE] Connected successfully', { 
        status: res.status, 
        url: res.url,
        headers: Object.fromEntries(res.headers.entries())
      });
      
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let eventCount = 0;
      let isAborted = false;
      
      // Handle abortion gracefully
      controller.signal.addEventListener('abort', () => {
        isAborted = true;
        console.log('[SSE] Stream aborted by user');
      });
      
      try {
        while (true) {
          const { value, done } = await reader.read();
          
          if (done) {
            console.log('[SSE] Stream ended naturally, total events:', eventCount);
            break;
          }
          
          if (isAborted) {
            console.log('[SSE] Breaking due to abort signal');
            break;
          }
          
          const chunk = decoder.decode(value, { stream: true });
          console.log('[SSE] Received chunk:', chunk.length, 'bytes');
          buffer += chunk;
          
          // Process complete SSE events
          let sepIndex;
          while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
            const rawEvent = buffer.slice(0, sepIndex).trim();
            buffer = buffer.slice(sepIndex + 2);
            
            if (!rawEvent) continue;
            
            // Skip SSE comments (lines starting with :)
            if (rawEvent.startsWith(':')) {
              console.log('[SSE] Comment/ping:', rawEvent.slice(0, 20));
              continue;
            }
            
            const eventLine = rawEvent.startsWith('data:') ? rawEvent.slice(5).trim() : rawEvent;
            
            try {
              const evt = JSON.parse(eventLine);
              eventCount++;
              console.log(`[SSE] Event #${eventCount}:`, evt.type, evt);
              
              if (evt.type === 'match' && evt.item) {
                console.log('[SSE] Processing match event:', evt.item);
                addResult(idx, evt.item);
                console.log('[SSE] Called addResult for row', idx);
              } else if (evt.type === 'progress') {
                updateProgress(idx, {
                  processed: evt.processed,
                  total: evt.total,
                  matches: evt.matches,
                  phase: evt.phase || 'parsing'
                });
                console.log(`[SSE] Updated progress: ${evt.processed}/${evt.total}, matches: ${evt.matches}`);
              } else if (evt.type === 'meta') {
                updateProgress(idx, {
                  total: evt.links,
                  processed: 0,
                  matches: 0,
                  phase: 'scanning'
                });
                console.log(`[SSE] Meta: ${evt.links} links found for seller: ${evt.seller}`);
              } else if (evt.type === 'cancelled') {
                console.log('[SSE] Stream cancelled by server');
                setRowState(idx, { loading: false, controller: null });
                break;
              } else if (evt.type === 'done') {
                console.log('[SSE] Stream completed');
                setRowState(idx, { loading: false, controller: null });
                break;
              } else if (evt.type === 'error') {
                console.error('[SSE] Server error:', evt);
                setRowState(idx, { 
                  loading: false, 
                  error: evt.message || 'Stream error', 
                  controller: null 
                });
                break;
              } else if (evt.type === 'hello') {
                console.log('[SSE] Hello from server:', evt.ts);
              }
            } catch (e) {
              console.warn('[SSE] Failed to parse event:', e, { raw: eventLine });
            }
          }
        }
      } catch (readError) {
        if (isAborted) {
          console.log('[SSE] Read terminated due to abort - this is expected');
        } else {
          throw readError;
        }
      }
      
      // Ensure we end the loading state
      setRowState(idx, { loading: false, controller: null });
      
    } catch (err) {
      if (err.name === 'AbortError' || err.message.includes('aborted')) {
        console.log('[SSE] Stream was cancelled by user');
        setRowState(idx, { loading: false, controller: null });
      } else {
        console.error('[SSE] Stream failed:', err);
        setRowState(idx, { 
          loading: false, 
          error: String(err), 
          controller: null 
        });
      }
    }
  };

  const cancelStream = async (idx) => {
    const row = searchRows[idx];
    
    // First, send cancel to backend if we have a searchId
    if (row?.searchId) {
      try {
        await fetch(`${API_BASE}/api/search/cancel`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ searchId: row.searchId }),
        });
        console.log('[Cancel] Backend notified of cancellation');
      } catch (e) {
        console.warn('[Cancel] Failed to notify backend:', e);
      }
    }
    
    // Then abort the fetch request
    if (row?.controller) {
      try {
        row.controller.abort();
        console.log('[Cancel] Fetch request aborted');
      } catch (e) {
        console.warn('[Cancel] Failed to abort controller:', e);
      }
    }
    
    // Update UI state immediately
    setRowState(idx, { 
      loading: false, 
      controller: null 
    });
    
    console.log('[Cancel] Stream cancellation complete');
  };

  const addRow = () => {
    setSearchRows((rows) => [...rows, { ...defaultSearch, results: [], progress: null }]);
  };

  const addResultToSeller = (sellerName, item, measurements) => {
    setSearchRows((rows) => {
      // Check if row exists
      const existingIdx = rows.findIndex(r => r.seller === sellerName);
      if (existingIdx !== -1) {
        // Add to existing
        const newRows = [...rows];
        const currentResults = newRows[existingIdx].results || [];
        // Avoid duplicates
        if (currentResults.some(r => r.url === item.url)) return rows;
        
        newRows[existingIdx] = {
            ...newRows[existingIdx],
            results: [...currentResults, item]
        };
        return newRows;
      } else {
        // Create new row
        const newRow = {
            ...defaultSearch,
            seller: sellerName,
            p2p: measurements.p2p,
            length: measurements.length,
            tolerance: measurements.tolerance,
            results: [item],
            loading: false 
        };
        return [...rows, newRow];
      }
    });
  };

  const startBrowse = async (idx) => {
    const makeId = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const controller = new AbortController();
    const searchId = makeId();
    
    // Initialize search state for the browsing row
    setRowState(idx, { 
      loading: true, 
      error: '', 
      results: [], 
      controller, 
      searchId,
      progress: null 
    });
    
    const row = searchRows[idx];
    
    try {
      const p2p = parseFloat(row.p2p);
      const length = parseFloat(row.length);
      const tolerance = row.tolerance === '' ? 0.5 : parseFloat(row.tolerance);
      const payload = {
        category: 'tops',
        measurements: {
          first: isNaN(p2p) ? null : p2p,
          second: isNaN(length) ? null : length,
        },
        tolerance: isNaN(tolerance) ? 0.5 : tolerance,
        seller: "", // Empty seller triggers browse mode
        maxItems: 100, // Browse limit
        maxLinks: 10000,
        searchId,
      };
      
      console.log('[SSE] Starting browse stream with payload:', payload);
      
      const res = await fetch(`${API_BASE}/api/search/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
        cache: 'no-store',
      });
      
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      
      if (!res.body) {
        throw new Error('No response body for streaming');
      }
      
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let isAborted = false;
      
      controller.signal.addEventListener('abort', () => {
        isAborted = true;
      });
      
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done || isAborted) break;
          
          buffer += decoder.decode(value, { stream: true });
          
          let sepIndex;
          while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
            const rawEvent = buffer.slice(0, sepIndex).trim();
            buffer = buffer.slice(sepIndex + 2);
            if (!rawEvent || rawEvent.startsWith(':')) continue;
            
            const eventLine = rawEvent.startsWith('data:') ? rawEvent.slice(5).trim() : rawEvent;
            try {
              const evt = JSON.parse(eventLine);
              
              if (evt.type === 'match' && evt.item && evt.seller) {
                addResultToSeller(evt.seller, evt.item, { p2p: row.p2p, length: row.length, tolerance: row.tolerance });
              } else if (evt.type === 'progress') {
                updateProgress(idx, {
                  processed: evt.processed,
                  matches: evt.matches,
                  phase: evt.phase || 'browsing'
                });
              } else if (evt.type === 'done' || evt.type === 'cancelled') {
                setRowState(idx, { loading: false, controller: null });
                break;
              } else if (evt.type === 'error') {
                setRowState(idx, { loading: false, error: evt.message, controller: null });
                break;
              }
            } catch (e) {
              console.warn('[SSE] Failed to parse event:', e);
            }
          }
        }
      } catch (readError) {
        if (!isAborted) throw readError;
      }
      setRowState(idx, { loading: false, controller: null });
    } catch (err) {
      if (err.name !== 'AbortError') {
        setRowState(idx, { loading: false, error: String(err), controller: null });
      }
    }
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
      <style>{`
        * {
          box-sizing: border-box;
        }
        body, .app-dark-ui {
          background: #000000;
          color: #ffffff;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
          margin: 0;
          height: 100vh;
          line-height: 1.5;
        }
        .app-dark-ui {
          display: flex;
          height: 100vh;
          overflow: hidden;
        }
        .sidebar {
          width: 240px;
          background: #111111;
          display: flex;
          flex-direction: column;
          padding: 0 0 2rem 0;
          border-right: 1px solid #333333;
          position: sticky;
          top: 0;
          height: 100vh;
          flex-shrink: 0;
        }
        .sidebar-title {
          font-size: 1.75rem;
          font-weight: 700;
          padding: 2rem 1.5rem 1rem 1.5rem;
          letter-spacing: -0.025em;
          color: #ffffff;
        }
        .sidebar-nav ul {
          list-style: none;
          padding: 0 1rem;
          margin: 0;
        }
        .sidebar-nav li {
          padding: 0.875rem 1rem;
          margin-bottom: 0.25rem;
          border-radius: 8px;
          cursor: pointer;
          display: flex;
          align-items: center;
          transition: all 0.2s ease;
          font-weight: 500;
        }
        .sidebar-nav li.active {
          background: #333333;
          color: #ffffff;
        }
        .sidebar-nav li:hover:not(.active) {
          background: #222222;
        }
        .sidebar-icon {
          margin-right: 0.75rem;
          font-size: 1.1rem;
        }
        .main-content {
          flex: 1;
          padding: 2rem;
          background: #000000;
          height: 100vh;
          overflow-y: auto;
          box-sizing: border-box;
        }
        .rows-container {
          display: flex;
          flex-direction: column;
          gap: 1.5rem;
          position: relative;
          max-width: 1400px;
          margin: 0 auto;
        }
        .search-row-card {
          background: #111111;
          border: 1px solid #333333;
          border-radius: 12px;
          padding: 1.5rem;
          display: flex;
          flex-direction: column;
          min-width: 0;
          transition: border-color 0.2s ease;
        }
        .search-row-card:hover {
          border-color: #555555;
        }
        .search-row-inputs {
          display: flex;
          gap: 1rem;
          margin-bottom: 1.25rem;
          flex-wrap: wrap;
          align-items: center;
        }
        .search-controls {
          display: flex;
          flex-direction: column;
          gap: 0.75rem;
          align-items: flex-start;
        }
        .button-group {
          display: flex;
          gap: 0.5rem;
        }
        .progress-counter {
          font-size: 0.875rem;
          color: #cccccc;
          font-family: ui-monospace, 'SF Mono', Consolas, monospace;
          white-space: nowrap;
          font-weight: 500;
        }
        .input-dark {
          background: #000000;
          border: 1px solid #555555;
          color: #ffffff;
          border-radius: 8px;
          padding: 0.75rem 1rem;
          font-size: 0.9rem;
          outline: none;
          transition: all 0.2s ease;
          font-family: inherit;
        }
        .input-dark:focus {
          border-color: #ffffff;
          box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.1);
        }
        .input-dark::placeholder {
          color: #888888;
        }
        .search-btn {
          background: #ffffff;
          color: #000000;
          border: none;
          border-radius: 8px;
          padding: 0.75rem 1.25rem;
          font-size: 0.9rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s ease;
        }
        .search-btn:hover:not(:disabled) {
          background: #e5e5e5;
          transform: translateY(-1px);
        }
        .search-btn.secondary {
          background: #333333;
          color: #ffffff;
          border: 1px solid #555555;
        }
        .search-btn.secondary:hover:not(:disabled) {
          background: #444444;
        }
        .search-btn.stop { 
          background: #ff4444;
          color: #ffffff;
        }
        .search-btn.stop:hover {
          background: #cc0000;
        }
        .search-btn:disabled { 
          opacity: 0.6; 
          cursor: not-allowed; 
          transform: none;
        }
        .results-scroll {
          display: flex;
          gap: 1rem;
          overflow-x: auto;
          padding-bottom: 0.75rem;
          scrollbar-width: thin;
          scrollbar-color: #555555 #111111;
        }
        .results-scroll::-webkit-scrollbar {
          height: 6px;
        }
        .results-scroll::-webkit-scrollbar-track {
          background: #111111;
          border-radius: 3px;
        }
        .results-scroll::-webkit-scrollbar-thumb {
          background: #555555;
          border-radius: 3px;
        }
        .results-scroll::-webkit-scrollbar-thumb:hover {
          background: #777777;
        }
        .result-card {
          min-width: 200px;
          max-width: 200px;
          background: #111111;
          border: 1px solid #333333;
          border-radius: 12px;
          padding: 1rem;
          display: flex;
          flex-direction: column;
          align-items: flex-start;
          color: inherit;
          text-decoration: none;
          transition: all 0.2s ease;
          flex-shrink: 0;
        }
        .result-card:hover {
          border-color: #ffffff;
          transform: translateY(-2px);
          box-shadow: 0 8px 25px rgba(255, 255, 255, 0.1);
        }
        .result-img {
          width: 100%;
          height: 200px;
          border-radius: 8px;
          object-fit: cover;
          background: #333333;
          margin-bottom: 0.75rem;
        }
        .result-img.placeholder { 
          display: block;
          background: linear-gradient(45deg, #333333, #555555);
        }
        .result-price {
          font-weight: 600;
          color: #ffffff;
          margin-bottom: 0.5rem;
          font-size: 0.95rem;
        }
        .result-meta {
          color: #cccccc;
          font-size: 0.85rem;
          margin-bottom: 0.25rem;
          font-family: ui-monospace, 'SF Mono', Consolas, monospace;
        }
        .results-placeholder {
          color: #888888;
          font-style: italic;
          padding: 2rem 0;
          text-align: center;
          width: 100%;
          font-size: 0.9rem;
        }
        .add-row-container {
          display: flex;
          justify-content: center;
          align-items: center;
          padding-top: 1rem;
        }
        .add-row-btn {
          background: #ffffff;
          color: #000000;
          border: none;
          border-radius: 50%;
          width: 48px;
          height: 48px;
          font-size: 1.5rem;
          line-height: 1;
          cursor: pointer;
          transition: all 0.2s ease;
          font-weight: bold;
        }
        .add-row-btn:hover {
          background: #e5e5e5;
          transform: translateY(-2px);
        }
        .add-row-btn:active {
          transform: translateY(0);
        }
        @media (max-width: 768px) {
          .sidebar { 
            width: 60px; 
          }
          .sidebar-title { 
            display: none; 
          }
          .sidebar-nav li {
            justify-content: center;
          }
          .sidebar-nav li span:not(.sidebar-icon) {
            display: none;
          }
          .main-content { 
            padding: 1rem; 
          }
          .search-row-inputs {
            flex-direction: column;
            align-items: stretch;
          }
          .result-card {
            min-width: 160px;
            max-width: 160px;
          }
        }
      `}</style>
    </div>
  );
}

export default App;
