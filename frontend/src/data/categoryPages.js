const MEASUREMENT_DEFAULTS = Object.freeze({
  p2pMin: '20.5',
  p2pMax: '21.25',
  lengthMin: '26.5',
  lengthMax: '27.25',
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
    group: ['tops', 'coats-jackets'],
    mode: 'measurements',
    headline: 'Tops Search',
    defaults: MEASUREMENT_DEFAULTS,
  },
  {
    id: 'bottoms',
    label: 'Bottoms',
    group: 'bottoms',
    mode: 'bottomMeasurements',
    headline: 'Bottoms Search',
    defaults: {
      waistMin: '32',
      waistMax: '36',
      inseamMin: '31',
      inseamMax: '33',
      riseMin: '11',
      riseMax: '13',
      legOpeningMin: '9.5',
      legOpeningMax: '10.5',
    },
  },
  {
    id: 'footwear',
    label: 'Footwear',
    group: 'footwear',
    mode: 'sizeRange',
    headline: 'Footwear Search',
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
    group: 'accessories',
    mode: 'all',
    headline: 'Accessories Search',
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
