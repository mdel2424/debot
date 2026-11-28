export const DEFAULT_SELLER_ADDITIONS_VERSION = 4;
const SELLER_UPDATE_BATCHES = [
  {
    version: 1,
    sellers: [
      { name: 'Thrifts N Spliffs', username: 'thriftsnspliffs' },
      { name: 'The Loop MTL', username: 'theloopmtl' },
      { name: 'Mountain Vintage Thrifts', username: 'mountainvintagethrifts' },
    ],
  },
  {
    version: 2,
    sellers: [
      { name: 'Second Life Vintage', username: 'secondlifeinc' },
      { name: 'Vintage by Sherman', username: 'vintagebysherman' },
    ],
  },
  {
    version: 3,
    sellers: [],
    removals: ['former_vintage', 'gwoodsgear', 'jizzypantthrifts'],
  },
  {
    version: 4,
    sellers: [
      { name: 'Great Lakes Grails', username: 'greatlakesgrails' },
      { name: 'The Good Sale', username: 'thegoodsale' },
      { name: 'Jimmy Vntg', username: 'jimmyvntg' },
      { name: 'AGF Thrifts', username: 'agfthrifts' },
      { name: 'Remainder', username: 'remainder' },
      { name: 'Davos Wrld', username: 'davos_wrld' },
      { name: 'Blue Angel Vintage', username: 'blueangelvtg' },
      { name: 'Mr Swag 4 Life', username: 'mr_swag4life' },
      { name: 'Lil Eggy Vintage', username: 'lileggyvintage' },
      { name: 'Easy Eddies Vintage', username: 'easyeddiesvintage' },
      { name: 'Rudra 19', username: 'rudra_19' },
      { name: 'Jpaz Closet', username: 'jpazcloset' },
      { name: 'MTL Fine Pieces', username: 'mtl_finepieces' },
      { name: 'Kitty Closet', username: 'kittycloset05' },
      { name: 'SJ Thrifts', username: 'sjthr1fts' },
      { name: 'Hidden Rags', username: 'hiddenrags' },
      { name: 'Spot Marked X', username: 'spotmarkedx' },
      { name: 'Inkloom', username: 'inkloom' },
      { name: 'Width', username: 'width' },
    ],
  },
];

export const getSellerAdditionsAfterVersion = (version) =>
  SELLER_UPDATE_BATCHES.filter((batch) => batch.version > version).flatMap((batch) => batch.sellers);

export const getSellerRemovalsAfterVersion = (version) =>
  SELLER_UPDATE_BATCHES.filter((batch) => batch.version > version).flatMap((batch) => batch.removals || []);

export const DEFAULT_SELLER_ADDITIONS = getSellerAdditionsAfterVersion(0);

export const DEFAULT_FOLLOWING_ACCOUNTS = [
  { name: 'OnTheMarkCo', username: 'onthemarkco' },
  { name: 'Reduce & Re - Use ♻️', username: 'reducereuseclothes' },
  { name: 'topleft vintage', username: 'topleftvintage' },
  { name: '50% OFF SALE', username: 'refinedvtg' },
  { name: 'NEW TMRW', username: 'newtmrw' },
  { name: 'Askar', username: 'asknr' },
  { name: '⚒Heavy Vintage ⚒', username: 'heavyvintage' },
  { name: 'garcia', username: 'iheartvintageco' },
  { name: 'Vtgthriftshit', username: 'vtgthriftshit' },
  { name: 'shitty plug', username: 'shittyvintageplug' },
  { name: 'Thrift Jesus', username: 'thethriftingjesus' },
  { name: '5-Out Vintage 🇨🇦', username: '5outvintage' },
  { name: 'Way Back Vintage', username: 'waybackco' },
  { name: 'Rutland Retros', username: 'rutlandretros' },
  { name: 'Alex Vinson', username: 'flashyfashion' },
  { name: 'Vintage Quebec', username: 'vintagequebec_' },
  { name: 'Best Choice Vintage', username: 'bestchoicevintage' },
  { name: 'y2ktodayy', username: 'y2ktodayy' },
  { name: 'Zed’s Vintage', username: 'zedsvintage' },
  { name: 'Dead Threads', username: 'dead_threads00' },
  { name: 'Ryan Is Garments', username: 'ryan_is_garments' },
  ...DEFAULT_SELLER_ADDITIONS,
];
