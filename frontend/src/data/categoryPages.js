const MEASUREMENT_DEFAULTS = Object.freeze({
  first: '21.5',
  second: '27.25',
  firstTolerance: '0.5',
  secondTolerance: '1',
});

const FOOTWEAR_SIZE_OPTIONS = [
  '7',
  '7.5',
  '8',
  '8.5',
  '9',
  '9.5',
  '10',
  '10.5',
  '11',
  '11.5',
  '12',
  '12.5',
  '13',
];

export const CATEGORY_PAGES = [
  {
    id: 'tops',
    label: 'Tops',
    shortLabel: 'T',
    group: ['tops', 'coats-jackets'],
    mode: 'measurements',
    headline: 'Tops Search',
    description: 'Search your shared seller list for tops, tees, and jackets that match your saved P2P and length range.',
    defaults: MEASUREMENT_DEFAULTS,
  },
  {
    id: 'bottoms',
    label: 'Bottoms',
    shortLabel: 'B',
    group: 'bottoms',
    mode: 'bottomMeasurements',
    headline: 'Bottoms Search',
    description: 'Search seller listings by waist, inseam plus rise, and leg-opening ranges from your closet fit.',
    defaults: {
      waistMin: '32',
      waistMax: '36',
      inseamRiseMin: '42',
      inseamRiseMax: '44',
      legOpeningMin: '9.5',
      legOpeningMax: '10.5',
    },
  },
  {
    id: 'footwear',
    label: 'Footwear',
    shortLabel: 'F',
    group: 'footwear',
    mode: 'sizeRange',
    headline: 'Footwear Search',
    description: 'Filter seller listings by Depop shoe sizing with a saved US range.',
    defaults: {
      min: '10',
      max: '11',
    },
    sizeOptions: FOOTWEAR_SIZE_OPTIONS,
    sizeUnitLabel: 'US',
    sizeSystem: 'US',
  },
  {
    id: 'accessories',
    label: 'Accessories',
    shortLabel: 'A',
    group: 'accessories',
    mode: 'all',
    headline: 'Accessories Search',
    description: 'Browse every accessory listing from your saved sellers without any size filter.',
    defaults: {},
  },
];

export const CATEGORY_PAGE_MAP = Object.fromEntries(
  CATEGORY_PAGES.map((page) => [page.id, page])
);

export const DEFAULT_CATEGORY_PAGE_ID = CATEGORY_PAGES[0].id;
export const CATEGORY_FILTER_DEFAULTS_SIGNATURE = JSON.stringify(
  CATEGORY_PAGES.map((page) => ({
    id: page.id,
    defaults: page.defaults,
  }))
);

export function getDefaultPageFilters(pageId) {
  const page = CATEGORY_PAGE_MAP[pageId] || CATEGORY_PAGE_MAP[DEFAULT_CATEGORY_PAGE_ID];
  return { ...page.defaults };
}

export function isCategoryPageId(value) {
  return Boolean(CATEGORY_PAGE_MAP[value]);
}
