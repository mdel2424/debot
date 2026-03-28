const MEASUREMENT_DEFAULTS = Object.freeze({
  first: '21.5',
  second: '27.25',
  firstTolerance: '0.5',
  secondTolerance: '1.25',
});

const BOTTOMS_SIZE_OPTIONS = [
  '28',
  '29',
  '30',
  '31',
  '32',
  '33',
  '34',
  '35',
  '36',
  '37',
  '38',
  '39',
  '40',
];

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
    group: 'tops',
    mode: 'measurements',
    headline: 'Tops Search',
    description: 'Search your shared seller list for tops that match your saved P2P and length range.',
    defaults: MEASUREMENT_DEFAULTS,
  },
  {
    id: 'bottoms',
    label: 'Bottoms',
    shortLabel: 'B',
    group: 'bottoms',
    mode: 'sizeRange',
    headline: 'Bottoms Search',
    description: 'Filter seller listings by Depop bottoms sizing, using your saved waist range.',
    defaults: {
      min: '30',
      max: '34',
    },
    sizeOptions: BOTTOMS_SIZE_OPTIONS,
    sizeUnitLabel: 'Waist',
    sizeSystem: 'WAIST',
  },
  {
    id: 'coats-jackets',
    label: 'Coats/Jackets',
    shortLabel: 'CJ',
    group: 'coats-jackets',
    mode: 'measurements',
    headline: 'Coats/Jackets Search',
    description: 'Search jackets and outerwear using the same measurement defaults as your tops page.',
    defaults: MEASUREMENT_DEFAULTS,
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

export function getDefaultPageFilters(pageId) {
  const page = CATEGORY_PAGE_MAP[pageId] || CATEGORY_PAGE_MAP[DEFAULT_CATEGORY_PAGE_ID];
  return { ...page.defaults };
}

export function isCategoryPageId(value) {
  return Boolean(CATEGORY_PAGE_MAP[value]);
}
