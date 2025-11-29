/**
 * SSE stream hook for handling Server-Sent Events
 */

const API_BASE = import.meta.env && import.meta.env.DEV ? 'http://127.0.0.1:8000' : '';

/**
 * Generate a unique search ID
 */
export const makeSearchId = () => 
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

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
export async function streamSearch({
  payload,
  controller,
  onMatch,
  onProgress,
  onMeta,
  onError,
  onDone,
}) {
  try {
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
    
    console.log('[SSE] Connected successfully');
    
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let eventCount = 0;
    let isAborted = false;
    
    controller.signal.addEventListener('abort', () => {
      isAborted = true;
      console.log('[SSE] Stream aborted by user');
    });
    
    try {
      while (true) {
        const { value, done } = await reader.read();
        
        if (done || isAborted) {
          console.log('[SSE] Stream ended, total events:', eventCount);
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
            console.log(`[SSE] Event #${eventCount}:`, evt.type);
            
            switch (evt.type) {
              case 'match':
                if (evt.item) {
                  onMatch?.(evt);
                }
                break;
              case 'progress':
                onProgress?.({
                  processed: evt.processed,
                  total: evt.total,
                  matches: evt.matches,
                  phase: evt.phase || 'parsing'
                });
                break;
              case 'meta':
                onMeta?.({
                  total: evt.links,
                  seller: evt.seller
                });
                break;
              case 'cancelled':
              case 'done':
                onDone?.();
                return;
              case 'error':
                onError?.(evt.message || 'Stream error');
                return;
              case 'hello':
                console.log('[SSE] Hello from server:', evt.ts);
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
    
    onDone?.();
    
  } catch (err) {
    if (err.name === 'AbortError' || err.message?.includes('aborted')) {
      console.log('[SSE] Stream was cancelled by user');
      onDone?.();
    } else {
      console.error('[SSE] Stream failed:', err);
      onError?.(String(err));
    }
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
