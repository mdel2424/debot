import React from 'react';

const SearchRow = ({ row, idx, handleInput, startStream, cancelStream, startBrowse }) => {
  return (
    <div className="search-row-card">
      <div className="search-row-inputs">
        <input
          className="input-dark"
          placeholder="Seller"
          value={row.seller}
          onChange={e => handleInput(idx, 'seller', e.target.value)}
        />
        <input
          className="input-dark"
          placeholder="Length (in)"
          value={row.length}
          onChange={e => handleInput(idx, 'length', e.target.value)}
        />
        <input
          className="input-dark"
          placeholder="P2P (in)"
          value={row.p2p}
          onChange={e => handleInput(idx, 'p2p', e.target.value)}
        />
        <input
          className="input-dark"
          placeholder="Tolerance (in)"
          value={row.tolerance}
          onChange={e => handleInput(idx, 'tolerance', e.target.value)}
        />
        <div className="search-controls">
          {row.loading ? (
            <button className="search-btn stop" onClick={() => cancelStream(idx)}>Stop</button>
          ) : (
            <div className="button-group">
              <button className="search-btn" onClick={() => startStream(idx)}>Search</button>
              <button className="search-btn secondary" onClick={() => startBrowse(idx)}>Browse All</button>
            </div>
          )}
          {row.progress && (
            <div className="progress-counter">
              {row.progress.processed || 0}/{row.progress.total || 0} • {row.progress.matches || 0} matches
            </div>
          )}
        </div>
      </div>

      <div className="results-scroll">
        {/* Render results */}
        {row.results && row.results.length > 0 && row.results.map((res, i) => {
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
          const hasMeta = metaParts.length > 0;

          return (
            <a className="result-card" key={`${res.url}-${i}` || i} href={res.url || '#'} target="_blank" rel="noreferrer">
              {res.image ? (
                <img className="result-img" src={res.image} alt="item" />
              ) : (
                <div className="result-img placeholder" />
              )}
              <div className="result-price">{res.price || ''}</div>
              {hasMeta && (
                <div className="result-meta">
                  {metaParts.join(' | ')}
                </div>
              )}
            </a>
          );
        })}
      </div>
    </div>
  );
};

export default SearchRow;
