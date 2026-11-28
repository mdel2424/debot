import assert from 'node:assert/strict';
import test from 'node:test';
import { CATEGORY_PAGE_MAP } from '../data/categoryPages.js';
import {
  filterRowResults,
  matchesItemFilters,
  parsePriceCad,
  sortResultsList,
  sortSellerEntriesByHits,
} from './itemFilters.js';

test('parsePriceCad handles CAD formats and rejects foreign currencies', () => {
  assert.equal(parsePriceCad({ price: '$25.00 CAD' }), 25);
  assert.equal(parsePriceCad({ price: '$30' }), 30);
  assert.equal(parsePriceCad({ priceCad: 42.5, price: '$99' }), 42.5);
  assert.equal(parsePriceCad({ price: 'US$20.00' }), null);
  assert.equal(parsePriceCad({ price: '£15.00' }), null);
});

test('realtime tops filter masks cached catalog without re-searching', () => {
  const page = CATEGORY_PAGE_MAP.tops;
  const row = {
    seller: 'thegoodsale',
    hasCatalog: true,
    catalog: [
      { url: 'u1', title: 'Vintage Nike Tee', price: '$25.00 CAD', priceCad: 25, p2p: 21, length: 27 },
      { url: 'u2', title: 'Carhartt Jacket', price: '$55.00 CAD', priceCad: 55, p2p: 21, length: 27 },
      { url: 'u3', title: 'Boxy Crewneck', price: '$28.00 CAD', priceCad: 28, p2p: 23, length: 26 },
    ],
    results: [],
  };

  const strict = filterRowResults(row, page, {
    p2pMin: '20.5', p2pMax: '21.25', lengthMin: '26.5', lengthMax: '27.25', maxPriceCad: '35',
  });
  assert.deepEqual(strict.map((i) => i.url), ['u1']);

  const higherPrice = filterRowResults(row, page, {
    p2pMin: '20.5', p2pMax: '21.25', lengthMin: '26.5', lengthMax: '27.25', maxPriceCad: '60',
  });
  assert.deepEqual(higherPrice.map((i) => i.url), ['u1', 'u2']);

  const widerFit = filterRowResults(row, page, {
    p2pMin: '20', p2pMax: '24', lengthMin: '25.5', lengthMax: '28', maxPriceCad: '35',
  });
  assert.deepEqual(widerFit.map((i) => i.url), ['u1', 'u3']);

  const keywordMasked = filterRowResults(row, page, {
    p2pMin: '20', p2pMax: '24', lengthMin: '25.5', lengthMax: '28', maxPriceCad: '60',
  }, { keyword: 'carhartt' });
  assert.deepEqual(keywordMasked.map((i) => i.url), ['u2']);
});

test('retailer tops strictly require both pit-to-pit and length', () => {
  const page = CATEGORY_PAGE_MAP.tops;
  const filters = { p2pMin: '20', p2pMax: '22', lengthMin: '26', lengthMax: '28', maxPriceCad: '40' };
  const partial = { url: 'p1', priceCad: 20, p2p: 21, length: null };
  assert.equal(matchesItemFilters(partial, page, filters, { isRetailer: false }), true);
  assert.equal(matchesItemFilters(partial, page, filters, { isRetailer: true }), false);
});

test('bottoms realtime filter checks waist, inseam, rise, legOpening, and price', () => {
  const page = CATEGORY_PAGE_MAP.bottoms;
  const filters = {
    waistMin: '32', waistMax: '36',
    inseamMin: '31', inseamMax: '33',
    riseMin: '11', riseMax: '13',
    legOpeningMin: '9.5', legOpeningMax: '10.5',
    maxPriceCad: '45',
  };
  assert.equal(matchesItemFilters({ waist: 34, inseam: 32, rise: 12, legOpening: 10, priceCad: 35 }, page, filters), true);
  assert.equal(matchesItemFilters({ waist: 34, inseam: 32, rise: 10, legOpening: 10, priceCad: 35 }, page, filters), false);
  assert.equal(matchesItemFilters({ waist: 34, inseam: 32, priceCad: 50 }, page, filters), false);
});

test('sortSellerEntriesByHits ranks most hits first with stable tie-breaking', () => {
  const sorted = sortSellerEntriesByHits([
    { seller: 'a', originalIndex: 0, displayedResults: [] },
    { seller: 'b', originalIndex: 1, displayedResults: [1, 2] },
    { seller: 'c', originalIndex: 2, displayedResults: [] },
    { seller: 'd', originalIndex: 3, displayedResults: [1, 2, 3, 4] },
    { seller: 'e', originalIndex: 4, displayedResults: [1, 2] },
  ]);
  assert.deepEqual(sorted.map((s) => s.seller), ['d', 'b', 'e', 'a', 'c']);
});

test('sortResultsList sorts by lowest price or newest age cleanly', () => {
  const items = [
    { url: 'a', priceCad: 35, ageDays: 5 },
    { url: 'b', priceCad: 18, ageDays: 12 },
    { url: 'c', priceCad: 25, ageDays: 1 },
  ];
  assert.deepEqual(sortResultsList(items, 'price_asc').map((i) => i.url), ['b', 'c', 'a']);
  assert.deepEqual(sortResultsList(items, 'newest').map((i) => i.url), ['c', 'a', 'b']);
});
