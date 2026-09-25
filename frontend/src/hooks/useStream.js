/**
 * SSE stream hook for handling Server-Sent Events
 */

const API_BASE = import.meta.env?.VITE_API_BASE || '';

/**
 * Generate a unique search ID
 */
export const makeSearchId = () => 
  crypto.randomUUID();

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
  const result = await response.json();
  if (!result.ok) {
    throw new Error(result.error || 'Search batch was not accepted.');
  }
  return result;
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
const MAX_RESULT_STREAMS = 3;
let activeStreams = 0;
const streamWaiters = [];

// Leave HTTP connections free for batch registration and Stop requests.
const acquireStreamSlot = (signal) => new Promise((resolve) => {
  const grant = () => {
    signal.removeEventListener('abort', abort);
    activeStreams += 1;
    resolve(() => {
      activeStreams -= 1;
      streamWaiters.shift()?.();
    });
  };
  const abort = () => {
    const index = streamWaiters.indexOf(grant);
    if (index >= 0) streamWaiters.splice(index, 1);
    resolve(null);
  };
  if (signal.aborted) {
    resolve(null);
  } else if (activeStreams < MAX_RESULT_STREAMS) {
    grant();
  } else {
    streamWaiters.push(grant);
    signal.addEventListener('abort', abort, { once: true });
  }
});

const waitForReconnect = (delayMs, signal) => new Promise((resolve) => {
  if (signal.aborted) {
    resolve();
    return;
  }

  const timeoutId = globalThis.setTimeout(() => {
    signal.removeEventListener('abort', handleAbort);
    resolve();
  }, delayMs);

  const handleAbort = () => {
    globalThis.clearTimeout(timeoutId);
    resolve();
  };

  signal.addEventListener('abort', handleAbort, { once: true });
});

const dispatchSearchEvent = (evt, callbacks) => {
  switch (evt.type) {
    case 'following_list':
      callbacks.onFollowingList?.({ usernames: evt.usernames, count: evt.count });
      return false;
    case 'seller_done':
    case 'seller_error':
      callbacks.onSellerDone?.({
        seller: evt.seller, matches: evt.matches || 0,
        processed: evt.processed, total: evt.total, error: evt.error,
      });
      return false;
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
      callbacks.onDone?.({ stopReason: 'cancelled' });
      return true;
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
      return false;
    default:
      return false;
  }
};

async function consumeSearchStream({
  payload,
  controller,
  ...callbacks
}, endpoint = '/api/search/stream') {
  const res = await fetch(`${API_BASE}${endpoint}`, {
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
    if (res.status >= 400 && res.status < 500 && ![408, 429].includes(res.status)) {
      callbacks.onError?.({ message: `Search request rejected (HTTP ${res.status}).`, code: 'invalid_request' });
      return true;
    }
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
      let separator;
      while ((separator = /\r?\n\r?\n/.exec(buffer)) !== null) {
        const rawEvent = buffer.slice(0, separator.index);
        buffer = buffer.slice(separator.index + separator[0].length);
        const eventLine = rawEvent.split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).replace(/^ /, ''))
          .join('\n');
        if (!eventLine) {
          continue;
        }
        let event;
        try {
          event = JSON.parse(eventLine);
        } catch (error) {
          console.warn('[SSE] Failed to parse event:', error);
          continue;
        }
        const terminal = dispatchSearchEvent(event, callbacks);
        if (terminal) {
          return true;
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }

  return false;
}

export async function streamSearch(options) {
  const { controller } = options;
  const stableOptions = {
    ...options,
    payload: { ...options.payload, searchId: options.payload.searchId || makeSearchId() },
  };
  let reconnectAttempt = 0;

  while (!controller.signal.aborted) {
    const release = await acquireStreamSlot(controller.signal);
    if (!release) return;
    try {
      const reachedTerminalEvent = await consumeSearchStream(stableOptions);
      if (reachedTerminalEvent || controller.signal.aborted) {
        return;
      }
    } catch (error) {
      if (controller.signal.aborted || error.name === 'AbortError') {
        return;
      }
      console.warn('[SSE] Connection lost; reattaching to saved job:', error);
    } finally {
      release();
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
    const response = await fetch(`${API_BASE}/api/search/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ searchId }),
    });
    if (!response.ok) {
      throw new Error(`Cancellation failed (HTTP ${response.status}).`);
    }
  } catch (e) {
    console.warn('[Cancel] Failed to notify backend:', e);
    throw e;
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
export async function streamFollowing(options) {
  const { controller, onError } = options;
  const release = await acquireStreamSlot(controller.signal);
  if (!release) return;
  try {
    const finished = await consumeSearchStream(options, '/api/search/following/stream');
    if (!finished && !controller.signal.aborted) {
      throw new Error('Following search disconnected before completion.');
    }
  } catch (error) {
    if (!controller.signal.aborted) {
      onError?.({ message: error.message, code: null });
    }
  } finally {
    release();
  }
}
