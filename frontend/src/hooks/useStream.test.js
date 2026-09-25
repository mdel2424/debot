import assert from 'node:assert/strict';
import test from 'node:test';
import { cancelSearch, startSearchBatch, streamSearch, streamFollowing } from './useStream.js';

const encoded = (value) => new TextEncoder().encode(value);
const response = (...chunks) => new Response(new ReadableStream({
  start(controller) {
    for (const chunk of chunks) controller.enqueue(encoded(chunk));
    controller.close();
  },
}));

test('SSE comments, CRLF, and split chunks preserve the first event and terminal state', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => response(
    ': preamble\r\n',
    'data: {"type":"match",\r\n',
    'data: "item":{"url":"one"}}\r',
    '\n\r\ndata: {"type":"done","processed":1}\r\n\r\n',
  ));
  const matches = [];
  const completed = [];
  await streamSearch({
    payload: { searchId: 'saved' }, controller: new AbortController(),
    onMatch: (event) => matches.push(event.item.url),
    onDone: (summary) => completed.push(summary),
  });
  assert.deepEqual(matches, ['one']);
  assert.equal(completed.length, 1);
  assert.equal(completed[0].processed, 1);
});

test('unexpected EOF reconnects using the same job ID and never reports completion', async (t) => {
  const ids = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    ids.push(JSON.parse(options.body).searchId);
    return ids.length === 1 ? response(': lost connection\n\n') : response('data: {"type":"done"}\n\n');
  });
  let done = 0;
  await streamSearch({ payload: {}, controller: new AbortController(), onDone: () => { done += 1; } });
  assert.equal(ids.length, 2);
  assert.ok(ids[0]);
  assert.equal(ids[0], ids[1]);
  assert.equal(done, 1);
});

test('HTTP validation failures terminate instead of retrying forever', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => new Response('{}', { status: 422 }));
  const errors = [];
  await streamSearch({ payload: {}, controller: new AbortController(), onError: (e) => errors.push(e) });
  assert.equal(fetch.mock.callCount(), 1);
  assert.equal(errors[0].code, 'invalid_request');
});

test('server rate-limit errors and cancellation are terminal, distinct outcomes', async (t) => {
  const replies = [
    'data: {"type":"error","code":"rate_limited","message":"cooldown exhausted"}\n\n',
    'data: {"type":"cancelled"}\n\n',
  ];
  t.mock.method(globalThis, 'fetch', async () => response(replies.shift()));
  const errors = [];
  const done = [];
  const options = { payload: {}, onError: (e) => errors.push(e), onDone: (e) => done.push(e) };
  await streamSearch({ ...options, controller: new AbortController() });
  await streamSearch({ ...options, controller: new AbortController() });
  assert.equal(errors.length, 1);
  assert.equal(errors[0].code, 'rate_limited');
  assert.deepEqual(done, [{ stopReason: 'cancelled' }]);
});

test('result streams leave connections available for Stop and aborted queued streams never start', async (t) => {
  let opened = 0;
  let stopped = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url.endsWith('/cancel')) {
      stopped += 1;
      return Response.json({ ok: true });
    }
    opened += 1;
    return new Response(new ReadableStream({
      start(controller) {
        options.signal.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')), { once: true });
      },
    }));
  });
  const controllers = Array.from({ length: 8 }, () => new AbortController());
  const promises = controllers.map((controller, index) => streamSearch({
    payload: { searchId: `job-${index}` }, controller,
  }));
  try {
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(opened, 3);
    await cancelSearch('job-0');
    assert.equal(stopped, 1);
  } finally {
    controllers.slice(3).forEach((controller) => controller.abort());
    controllers.slice(0, 3).forEach((controller) => controller.abort());
    await Promise.all(promises);
  }
  assert.equal(opened, 3);
});

test('batch and Stop failures are visible to the caller', async (t) => {
  t.mock.method(console, 'warn', () => {});
  t.mock.method(globalThis, 'fetch', async (url) => url.endsWith('/cancel')
    ? new Response('{}', { status: 503 })
    : Response.json({ ok: false, error: 'batch rejected' }));
  await assert.rejects(startSearchBatch([{ searchId: 'one' }]), /batch rejected/);
  await assert.rejects(cancelSearch('one'), /503/);
});

test('following search shares SSE framing and does not report success on a dropped connection', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => response(
    ': ready\r\n\r\ndata: {"type":"following_list","usernames":["one"],"count":1}\r\n\r\n',
  ));
  const lists = [];
  const errors = [];
  let done = 0;
  await streamFollowing({
    payload: { username: 'example' }, controller: new AbortController(),
    onFollowingList: (event) => lists.push(event),
    onError: (error) => errors.push(error), onDone: () => { done += 1; },
  });
  assert.equal(lists[0].count, 1);
  assert.match(errors[0].message, /disconnected/);
  assert.equal(done, 0);
});
