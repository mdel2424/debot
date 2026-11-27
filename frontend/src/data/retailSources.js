export const RETAIL_CATEGORIES = new Set(['tops', 'bottoms']);

export const RETAIL_ACCOUNTS = [
  {
    username: 'retail:wildrose',
    name: 'Wildrose Vintage',
    url: 'https://www.wildrosevintage.ca/collections/all',
    urls: {
      tops: 'https://www.wildrosevintage.ca/collections/all',
      bottoms: 'https://www.wildrosevintage.ca/collections/bottoms-1',
    },
  },
  {
    username: 'retail:la-penderie',
    name: 'La Penderie du Paradis',
    url: 'https://lapenderieduparadis.com/en/collections/secondhand-men-tees-sweaters-pullovers',
    urls: {
      tops: 'https://lapenderieduparadis.com/en/collections/secondhand-men-tees-sweaters-pullovers',
      bottoms: 'https://lapenderieduparadis.com/en/collections/secondhand-men-pants-shorts',
    },
  },
  {
    username: 'retail:peoples-thrift',
    name: "People's Thrift",
    url: 'https://www.peoplesthrift.ca/category/men%E2%80%99s-tops',
    urls: {
      tops: 'https://www.peoplesthrift.ca/category/men%E2%80%99s-tops',
      bottoms: 'https://www.peoplesthrift.ca/category/men%E2%80%99s-casual-pants',
    },
  },
];

export const RETAIL_BY_USERNAME = new Map(RETAIL_ACCOUNTS.map((account) => [account.username, {
  ...account,
  id: account.username.slice('retail:'.length),
  domain: new URL(account.url).hostname.replace(/^www\./, ''),
}]));
