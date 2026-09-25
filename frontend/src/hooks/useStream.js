/**
 * SSE stream hook for handling Server-Sent Events
 */

const API_BASE = import.meta.env && import.meta.env.DEV ? 'http://127.0.0.1:8000' : '';

/**
 * Generate a unique search ID
 */
export const makeSearchId = () => 
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export async function startSearchBatch(searches) {
  if (!Array.isArray(searches) || searches.length === 0) {
    return { ok: true, count: 0, jobs: [] };
  }

  const response = await fetch(`${API_BASE}/api/search/batch/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ searches }),
    keepalive: true,
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Create an SSE stream connection and process events
 * 
 * @param {Object} options - Stream options
 * @param {Object} options.payload - Request payload to send
 * @param {AbortController} options.controller - Abort controller for cancellation
 * @param {Function} options.onMatch - Called when a match event is received
 * @param {Function} options.onProgress - Called when a progress event is received
 * @param {Function} options.onMeta - Called when a meta event is received
 * @param {Function} options.onError - Called when an error occurs
 * @param {Function} options.onDone - Called when stream completes
 */
const STREAM_RECONNECT_DELAYS_MS = [500, 1000, 2000, 5000];

const waitForReconnect = (delayMs, signal) => new Promise((resolve) => {
  if (signal.aborted) {
    resolve();
    return;
  }

  const timeoutId = window.setTimeout(() => {
    signal.removeEventListener('abort', handleAbort);
    resolve();
  }, delayMs);

  const handleAbort = () => {
    window.clearTimeout(timeoutId);
    resolve();
  };

  signal.addEventListener('abort', handleAbort, { once: true });
});

const dispatchSearchEvent = (evt, callbacks) => {
  switch (evt.type) {
    case 'match':
      if (evt.item) {
        callbacks.onMatch?.(evt);
      }
      return false;
    case 'progress':
      callbacks.onProgress?.({
        processed: evt.processed,
        total: evt.total,
        matches: evt.matches,
        phase: evt.phase || 'parsing',
        message: evt.message || '',
        stopReason: evt.stopReason || null,
        retryAttempt: evt.retryAttempt || null,
        retryTotalAttempts: evt.retryTotalAttempts || null,
        retryDelaySeconds: evt.retryDelaySeconds || null,
        retryAvailableAt: evt.retryAvailableAt || null,
      });
      return false;
    case 'meta':
      callbacks.onMeta?.({
        total: evt.links,
        seller: evt.seller,
      });
      return false;
    case 'cancelled':
    case 'done':
      callbacks.onDone?.({
        processed: evt.processed,
        total: evt.total,
        matches: evt.matches,
        stopReason: evt.stopReason || null,
      });
      return true;
    case 'error':
      callbacks.onError?.({
        message: evt.message || 'Stream error',
        code: evt.code || null,
      });
      return true;
    case 'hello':
      console.log('[SSE] Attached to backend job:', evt.searchId);
      return false;
    default:
      return false;
  }
};

async function consumeSearchStream({
  payload,
  controller,
  onMatch,
  onProgress,
  onMeta,
  onError,
  onDone,
}) {
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

  try {
    while (!controller.signal.aborted) {
      const { value, done } = await reader.read();
      if (done) {
        return false;
      }

      buffer += decoder.decode(value, { stream: true });
      let separatorIndex;
      while ((separatorIndex = buffer.indexOf('\n\n')) !== -1) {
        const rawEvent = buffer.slice(0, separatorIndex).trim();
        buffer = buffer.slice(separatorIndex + 2);
        if (!rawEvent || rawEvent.startsWith(':')) {
          continue;
        }

        const eventLine = rawEvent.startsWith('data:')
          ? rawEvent.slice(5).trim()
          : rawEvent;

        try {
          const event = JSON.parse(eventLine);
          const terminal = dispatchSearchEvent(event, {
            onMatch,
            onProgress,
            onMeta,
            onError,
            onDone,
          });
          if (terminal) {
            return true;
          }
        } catch (error) {
          console.warn('[SSE] Failed to parse event:', error);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }

  return false;
}

export async function streamSearch(options) {
  const { controller } = options;
  let reconnectAttempt = 0;

  while (!controller.signal.aborted) {
    try {
      const reachedTerminalEvent = await consumeSearchStream(options);
      if (reachedTerminalEvent || controller.signal.aborted) {
        return;
      }
    } catch (error) {
      if (controller.signal.aborted || error.name === 'AbortError') {
        return;
      }
      console.warn('[SSE] Connection lost; reattaching to saved job:', error);
    }

    const delayIndex = Math.min(
      reconnectAttempt,
      STREAM_RECONNECT_DELAYS_MS.length - 1
    );
    await waitForReconnect(
      STREAM_RECONNECT_DELAYS_MS[delayIndex],
      controller.signal
    );
    reconnectAttempt += 1;
  }
}

/**
 * Cancel a stream via the backend API
 * @param {string} searchId - The search ID to cancel
 */
export async function cancelSearch(searchId) {
  if (!searchId) return;
  
  try {
    await fetch(`${API_BASE}/api/search/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ searchId }),
    });
    console.log('[Cancel] Backend notified of cancellation');
  } catch (e) {
    console.warn('[Cancel] Failed to notify backend:', e);
  }
}

/**
 * Stream search through all accounts a user is following
 * 
 * @param {Object} options - Stream options
 * @param {Object} options.payload - Request payload to send
 * @param {AbortController} options.controller - Abort controller for cancellation
 * @param {Function} options.onFollowingList - Called when following list is received
 * @param {Function} options.onMatch - Called when a match event is received
 * @param {Function} options.onSellerDone - Called when a seller is done being processed
 * @param {Function} options.onProgress - Called when a progress event is received
 * @param {Function} options.onError - Called when an error occurs
 * @param {Function} options.onDone - Called when stream completes
 */
export async function streamFollowing({
  payload,
  controller,
  onFollowingList,
  onMatch,
  onSellerDone,
  onProgress,
  onError,
  onDone,
}) {
  try {
    console.log('[SSE-Following] Starting stream with payload:', payload);
    
    const res = await fetch(`${API_BASE}/api/search/following/stream`, {
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
    
    console.log('[SSE-Following] Connected successfully');
    
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let eventCount = 0;
    let isAborted = false;
    
    controller.signal.addEventListener('abort', () => {
      isAborted = true;
      console.log('[SSE-Following] Stream aborted by user');
    });
    
    try {
      while (true) {
        const { value, done } = await reader.read();
        
        if (done || isAborted) {
          console.log('[SSE-Following] Stream ended, total events:', eventCount);
          break;
        }
        
        buffer += decoder.decode(value, { stream: true });
        
        // Process complete SSE events
        let sepIndex;
        while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, sepIndex).trim();
          buffer = buffer.slice(sepIndex + 2);
          
          if (!rawEvent || rawEvent.startsWith(':')) continue;
          
          const eventLine = rawEvent.startsWith('data:') ? rawEvent.slice(5).trim() : rawEvent;
          
          try {
            const evt = JSON.parse(eventLine);
            eventCount++;
            console.log(`[SSE-Following] Event #${eventCount}:`, evt.type);
            
            switch (evt.type) {
              case 'following_list':
                onFollowingList?.({
                  usernames: evt.usernames,
                  count: evt.count
                });
                break;
              case 'match':
                if (evt.item) {
                  onMatch?.(evt);
                }
                break;
              case 'seller_done':
                onSellerDone?.({
                  seller: evt.seller,
                  matches: evt.matches,
                  processed: evt.processed,
                  total: evt.total
                });
                break;
              case 'seller_error':
                console.warn(`[SSE-Following] Seller error for ${evt.seller}:`, evt.error);
                onSellerDone?.({
                  seller: evt.seller,
                  matches: 0,
                  processed: evt.processed,
                  total: evt.total,
                  error: evt.error
                });
                break;
              case 'progress':
                onProgress?.({
                  phase: evt.phase,
                  message: evt.message,
                  processed: evt.processed,
                  total: evt.total,
                  matches: evt.matches
                });
                break;
              case 'cancelled':
              case 'done':
                onDone?.();
                return;
              case 'error':
                onError?.({
                  message: evt.message || 'Stream error',
                  code: evt.code || null,
                });
                return;
              case 'hello':
                console.log('[SSE-Following] Hello from server:', evt.ts);
                break;
            }
          } catch (e) {
            console.warn('[SSE-Following] Failed to parse event:', e);
          }
        }
      }
    } catch (readError) {
      if (!isAborted) throw readError;
    }
    
    onDone?.();
    
  } catch (err) {
    if (err.name === 'AbortError' || err.message?.includes('aborted')) {
      console.log('[SSE-Following] Stream was cancelled by user');
      onDone?.();
    } else {
      console.error('[SSE-Following] Stream failed:', err);
      onError?.({
        message: String(err),
        code: null,
      });
    }
  }
}
