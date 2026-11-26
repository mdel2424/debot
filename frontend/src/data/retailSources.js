export const RETAIL_ACCOUNTS = [
  { username: 'retail:wildrose', name: 'Wildrose Vintage', url: 'https://www.wildrosevintage.ca/collections/all' },
  { username: 'retail:la-penderie', name: 'La Penderie du Paradis', url: 'https://lapenderieduparadis.com/en/collections/secondhand-men-tees-sweaters-pullovers' },
  { username: 'retail:peoples-thrift', name: "People's Thrift", url: 'https://www.peoplesthrift.ca/category/men%E2%80%99s-tops' },
];

export const RETAIL_BY_USERNAME = new Map(RETAIL_ACCOUNTS.map((account) => [account.username, {
  ...account,
  id: account.username.slice('retail:'.length),
  domain: new URL(account.url).hostname.replace(/^www\./, ''),
}]));
