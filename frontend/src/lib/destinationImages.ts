export interface DestinationImageMeta {
  name: string;
  country: string;
  region: string;
  imageUrl: string;
  tagline: string;
  description: string;
  mood: string;
  category: string[];
  landscape: 'City' | 'Mountain' | 'Beach' | 'Cultural' | 'Nature' | 'Historic' | 'Island';
  fallbackGradient: string;
}

export const DESTINATION_CATALOG: Record<string, DestinationImageMeta> = {
  // ==================== WEST & CENTRAL INDIA ====================
  goa: {
    name: 'Goa',
    country: 'India',
    region: 'Goa',
    imageUrl: 'https://images.unsplash.com/photo-1512343879784-a960bf40e7f2?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Sunlit coastlines, Portuguese quarters and slow seaside evenings.',
    description: 'Golden hour palm shores, heritage Latin quarters of Fontainhas, and relaxed coastal dining.',
    mood: 'Relaxed & Coastal',
    category: ['beach', 'coastal', 'culture', 'relaxation'],
    landscape: 'Beach',
    fallbackGradient: 'from-[#192A2E] via-[#2C7A78] to-[#E7A07F]',
  },
  mumbai: {
    name: 'Mumbai',
    country: 'India',
    region: 'Maharashtra',
    imageUrl: 'https://images.unsplash.com/photo-1570168007204-dfb528c6958f?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Marine Drive sunsets, Art Deco promenades and electric city energy.',
    description: 'The City of Dreams with the Arabian Sea coastline, Victorian Gothic architecture, and world-class street food.',
    mood: 'Dynamic & Coastal',
    category: ['city', 'food', 'heritage', 'coastal'],
    landscape: 'City',
    fallbackGradient: 'from-[#1C2530] via-[#204953] to-[#C96F52]',
  },
  udaipur: {
    name: 'Udaipur',
    country: 'India',
    region: 'Rajasthan',
    imageUrl: 'https://images.unsplash.com/photo-1599661046289-e31897846e41?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Lake Pichola reflections, marble palaces and quiet Mewari courtyards.',
    description: 'The City of Lakes with shimmering water palaces, heritage havelis, and sunset boat journeys.',
    mood: 'Romantic & Royal',
    category: ['heritage', 'culture', 'lakes', 'slow-travel'],
    landscape: 'Historic',
    fallbackGradient: 'from-[#28211E] via-[#5B3E31] to-[#DCCDB5]',
  },
  jodhpur: {
    name: 'Jodhpur',
    country: 'India',
    region: 'Rajasthan',
    imageUrl: 'https://images.unsplash.com/photo-1568283096533-078a24930eb8?auto=format&fit=crop&w=1400&q=80',
    tagline: 'The Blue City beneath the towering ramparts of Mehrangarh.',
    description: 'Cobalt-painted alleys, majestic cliff-top forts, and vibrant spice markets of Marwar.',
    mood: 'Historic & Majestic',
    category: ['heritage', 'culture', 'desert', 'architecture'],
    landscape: 'Historic',
    fallbackGradient: 'from-[#142338] via-[#214F75] to-[#DCCDB5]',
  },
  jaipur: {
    name: 'Jaipur',
    country: 'India',
    region: 'Rajasthan',
    imageUrl: 'https://images.unsplash.com/photo-1477587458883-47145ed94245?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Pink sandstone gates, Hawa Mahal facades and royal bazaars.',
    description: 'Rajasthan’s vibrant capital of astronomical observatories, hill-perched forts, and textile craft.',
    mood: 'Grand & Heritage',
    category: ['heritage', 'crafts', 'food', 'architecture'],
    landscape: 'Historic',
    fallbackGradient: 'from-[#261E1C] via-[#854535] to-[#E7A07F]',
  },
  ahmedabad: {
    name: 'Ahmedabad',
    country: 'India',
    region: 'Gujarat',
    imageUrl: 'https://images.unsplash.com/photo-1589308078059-be1415eab4c3?auto=format&fit=crop&w=1400&q=80',
    tagline: 'UNESCO pols, stepwell geometry and Sabarmati evening tranquility.',
    description: 'Centuries-old wooden pol neighborhoods, serene ashrams, and renowned Gujarati culinary traditions.',
    mood: 'Historic & Craft',
    category: ['heritage', 'culture', 'food', 'architecture'],
    landscape: 'Historic',
    fallbackGradient: 'from-[#201D1A] via-[#5B4633] to-[#DCCDB5]',
  },

  // ==================== NORTH INDIA ====================
  delhi: {
    name: 'Delhi',
    country: 'India',
    region: 'Delhi NCR',
    imageUrl: 'https://images.unsplash.com/photo-1587474260584-136574528ed5?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Mughal monuments, tree-lined avenues and centuries of street culinary soul.',
    description: 'From Old Delhi spice alleys to Lutyens green boulevards and medieval stepwells.',
    mood: 'Epic & Historic',
    category: ['heritage', 'food', 'city', 'culture'],
    landscape: 'City',
    fallbackGradient: 'from-[#201918] via-[#653E31] to-[#E7A07F]',
  },
  agra: {
    name: 'Agra',
    country: 'India',
    region: 'Uttar Pradesh',
    imageUrl: 'https://images.unsplash.com/photo-1564507592333-c60657eea523?auto=format&fit=crop&w=1400&q=80',
    tagline: 'The timeless marble wonder of the Taj Mahal beside the Yamuna.',
    description: 'Iconic Mughal imperial architecture, grand red sandstone forts, and river gardens.',
    mood: 'Iconic & Romantic',
    category: ['heritage', 'architecture', 'monuments'],
    landscape: 'Historic',
    fallbackGradient: 'from-[#202022] via-[#5A5B64] to-[#F0E8D9]',
  },
  amritsar: {
    name: 'Amritsar',
    country: 'India',
    region: 'Punjab',
    imageUrl: 'https://images.unsplash.com/photo-1609946859591-1fa1be357e62?auto=format&fit=crop&w=1400&q=80',
    tagline: 'The glowing Golden Temple, spiritual calm and unmatched Punjabi warmth.',
    description: 'Sacred waters of Harmandir Sahib, legendary community kitchens, and lively heritage lanes.',
    mood: 'Spiritual & Soulful',
    category: ['spiritual', 'food', 'culture', 'heritage'],
    landscape: 'Cultural',
    fallbackGradient: 'from-[#231B10] via-[#7B591C] to-[#E7A07F]',
  },
  srinagar: {
    name: 'Srinagar',
    country: 'India',
    region: 'Jammu & Kashmir',
    imageUrl: 'https://images.unsplash.com/photo-1598091383021-15ddea10925d?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Lakes, mountains and quiet valleys.',
    description: 'Shikara glides across Dal Lake at sunrise, Mughal terraced gardens, and cedar wood houseboats.',
    mood: 'Serene & Poetic',
    category: ['mountains', 'lakes', 'nature', 'slow-travel'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#142322] via-[#205852] to-[#DCCDB5]',
  },
  leh: {
    name: 'Leh Ladakh',
    country: 'India',
    region: 'Ladakh',
    imageUrl: 'https://images.unsplash.com/photo-1506197603052-3cc9c3a201bd?auto=format&fit=crop&w=1400&q=80',
    tagline: 'High-altitude deserts, clifftop gompas and crystal Himalayan skies.',
    description: 'Dramatic moonscape valleys, whitewashed stupas, high mountain passes and prayer flags in the wind.',
    mood: 'Rugged & Contemplative',
    category: ['mountains', 'adventure', 'spiritual', 'nature'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#16202A] via-[#2A465B] to-[#DCCDB5]',
  },
  manali: {
    name: 'Manali',
    country: 'India',
    region: 'Himachal Pradesh',
    imageUrl: 'https://images.unsplash.com/photo-1626621341517-bbf3d9990a23?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Pine forests, roaring river valleys and snow-crowned Himalayan passes.',
    description: 'Old wooden village chalets, apple orchards, mountain trails and Solang alpine air.',
    mood: 'Alpine & Refreshing',
    category: ['mountains', 'nature', 'adventure'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#16221E] via-[#224A3E] to-[#E9EEF0]',
  },
  rishikesh: {
    name: 'Rishikesh',
    country: 'India',
    region: 'Uttarakhand',
    imageUrl: 'https://images.unsplash.com/photo-1596401057633-54a8fe8ef647?auto=format&fit=crop&w=1400&q=80',
    tagline: 'The emerald Ganga, Himalayan foothills and evening prayer bells.',
    description: 'The yoga capital where the Ganges leaves the mountains, suspension bridges and riverside ashrams.',
    mood: 'Spiritual & Rejuvenating',
    category: ['spiritual', 'nature', 'yoga', 'wellness'],
    landscape: 'Nature',
    fallbackGradient: 'from-[#172523] via-[#25574F] to-[#DCCDB5]',
  },
  dehradun: {
    name: 'Dehradun',
    country: 'India',
    region: 'Uttarakhand',
    imageUrl: 'https://images.unsplash.com/photo-1605649487212-47bdab064df7?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Doon valley mornings, forest institutes and mountain foothills.',
    description: 'Quiet valley gateways with colonial institutional architecture and tea-bush slopes.',
    mood: 'Peaceful & Green',
    category: ['nature', 'mountains', 'relaxation'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#18231E] via-[#274B3E] to-[#DCCDB5]',
  },
  varanasi: {
    name: 'Varanasi',
    country: 'India',
    region: 'Uttar Pradesh',
    imageUrl: 'https://images.unsplash.com/photo-1561361513-2d000a50f0dc?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Ancient river ghats, dawn boat glides and devotional Ganga Aarti.',
    description: 'One of the world’s oldest living cities, where stone ghats meet the holy Ganges under morning mist.',
    mood: 'Spiritual & Eternal',
    category: ['spiritual', 'heritage', 'culture'],
    landscape: 'Historic',
    fallbackGradient: 'from-[#231A14] via-[#663A1D] to-[#E7A07F]',
  },

  // ==================== EAST INDIA ====================
  kolkata: {
    name: 'Kolkata',
    country: 'India',
    region: 'West Bengal',
    imageUrl: 'https://images.unsplash.com/photo-1558431382-27e303142255?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Heritage streets, riverfronts and food culture.',
    description: 'The City of Joy: Victoria Memorial gardens, yellow taxis, Hooghly ferries and literary coffee houses.',
    mood: 'Cultural & Literary',
    category: ['culture', 'food', 'heritage', 'city'],
    landscape: 'City',
    fallbackGradient: 'from-[#1C2024] via-[#1D4A52] to-[#E7A07F]',
  },
  darjeeling: {
    name: 'Darjeeling',
    country: 'India',
    region: 'West Bengal',
    imageUrl: 'https://images.unsplash.com/photo-1544735716-392fe2489ffa?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Tea gardens beneath the Himalayas.',
    description: 'Misty tea estates, views of Mt. Kanchenjunga at dawn, and slow heritage mountain railways.',
    mood: 'Misty & Mountain',
    category: ['mountains', 'tea', 'nature', 'slow-travel'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#162322] via-[#205149] to-[#DCCDB5]',
  },
  kalimpong: {
    name: 'Kalimpong',
    country: 'India',
    region: 'West Bengal',
    imageUrl: 'https://images.unsplash.com/photo-1622308644420-a7732a3962b3?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Quiet ridge-top monasteries, orchid nurseries and valley views.',
    description: 'A serene Himalayan hill town overlooking the Teesta River with British-era stone cottages.',
    mood: 'Quiet & Idyllic',
    category: ['mountains', 'nature', 'monasteries'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#182622] via-[#28574B] to-[#DCCDB5]',
  },
  sundarbans: {
    name: 'Sundarbans',
    country: 'India',
    region: 'West Bengal',
    imageUrl: 'https://images.unsplash.com/photo-1618172193763-c511deb635ca?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Tidal mangrove forests, silent waterways and royal Bengal wildlife.',
    description: 'The world’s largest mangrove delta, navigable only by wooden river boats under wide tidal skies.',
    mood: 'Wild & Untamed',
    category: ['wildlife', 'nature', 'waterways'],
    landscape: 'Nature',
    fallbackGradient: 'from-[#14201A] via-[#244E3B] to-[#DCCDB5]',
  },
  bhubaneswar: {
    name: 'Bhubaneswar',
    country: 'India',
    region: 'Odisha',
    imageUrl: 'https://images.unsplash.com/photo-1609137144813-7d9921338f24?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Ancient sandstone temple spires and Odissi cultural craftsmanship.',
    description: 'The Temple City of India featuring 10th-century Kalinga architecture and craft villages.',
    mood: 'Historic & Sculptural',
    category: ['heritage', 'architecture', 'culture'],
    landscape: 'Historic',
    fallbackGradient: 'from-[#221B16] via-[#634530] to-[#DCCDB5]',
  },
  puri: {
    name: 'Puri',
    country: 'India',
    region: 'Odisha',
    imageUrl: 'https://images.unsplash.com/photo-1590490360182-c33d57733427?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Golden Bay of Bengal breakers and sacred coastal temple traditions.',
    description: 'Pilgrimage shoreline with sea breezes, fishermen catamarans and Mahaprasad culinary traditions.',
    mood: 'Spiritual & Coastal',
    category: ['spiritual', 'beach', 'culture'],
    landscape: 'Beach',
    fallbackGradient: 'from-[#192429] via-[#275F65] to-[#E7A07F]',
  },

  // ==================== NORTHEAST INDIA ====================
  gangtok: {
    name: 'Gangtok',
    country: 'India',
    region: 'Sikkim',
    imageUrl: 'https://images.unsplash.com/photo-1589182373726-e4f658ab50f0?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Mountain roads, monasteries and clear air.',
    description: 'Clean mountain capital framed by prayer wheels, Rumtek Monastery, and panoramic views of Kanchenjunga.',
    mood: 'Crisp & Mountain',
    category: ['mountains', 'culture', 'nature'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#152327] via-[#245359] to-[#E9EEF0]',
  },
  sikkim: {
    name: 'Sikkim',
    country: 'India',
    region: 'Sikkim',
    imageUrl: 'https://images.unsplash.com/photo-1626014303757-656c0e05f77e?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Glacial valleys, high passes and peaceful Buddhist culture.',
    description: 'From Yumthang Valley of flowers to Gurudongmar Lake and sacred rhododendron forests.',
    mood: 'Sublime & Pristine',
    category: ['mountains', 'nature', 'monasteries', 'adventure'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#112023] via-[#1E4D4E] to-[#DCCDB5]',
  },
  pelling: {
    name: 'Pelling',
    country: 'India',
    region: 'Sikkim',
    imageUrl: 'https://images.unsplash.com/photo-1589182373726-e4f658ab50f0?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Up-close views of Kanchenjunga and ancient monastery walks.',
    description: 'Perched on high ridges with skywalks, Pemayangtse Monastery, and tranquil mountain villages.',
    mood: 'Serene & Lofty',
    category: ['mountains', 'nature', 'monasteries'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#152326] via-[#215152] to-[#E9EEF0]',
  },
  shillong: {
    name: 'Shillong',
    country: 'India',
    region: 'Meghalaya',
    imageUrl: 'https://images.unsplash.com/photo-1508672019048-805489564502?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Pine hills, live acoustic music and cool highland air.',
    description: 'Scotland of the East with crystal clear lakes, Khasi village hospitality, and café culture.',
    mood: 'Vibrant & Highland',
    category: ['nature', 'hills', 'music', 'culture'],
    landscape: 'Nature',
    fallbackGradient: 'from-[#15231D] via-[#245341] to-[#DCCDB5]',
  },
  cherrapunji: {
    name: 'Cherrapunji & Meghalaya',
    country: 'India',
    region: 'Meghalaya',
    imageUrl: 'https://images.unsplash.com/photo-1600585154340-be6161a56a0c?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Cloud country, waterfalls and living-root bridges.',
    description: 'Moss-covered living root bridges bio-engineered over centuries, plunge waterfalls and karst caves.',
    mood: 'Mystical & Emerald',
    category: ['nature', 'waterfalls', 'rainforest', 'adventure'],
    landscape: 'Nature',
    fallbackGradient: 'from-[#12231A] via-[#1E523A] to-[#DCCDB5]',
  },
  kaziranga: {
    name: 'Kaziranga',
    country: 'India',
    region: 'Assam',
    imageUrl: 'https://images.unsplash.com/photo-1534177616072-ef7dc120449d?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Tall elephant grass, Brahmaputra floodplains and the Indian one-horned rhino.',
    description: 'UNESCO World Heritage wildlife sanctuary with untamed wetlands and rich birdlife.',
    mood: 'Wild & Untamed',
    category: ['wildlife', 'nature', 'safari'],
    landscape: 'Nature',
    fallbackGradient: 'from-[#192215] via-[#3E512A] to-[#DCCDB5]',
  },

  // ==================== SOUTH INDIA ====================
  kerala: {
    name: 'Kerala',
    country: 'India',
    region: 'Kerala',
    imageUrl: 'https://images.unsplash.com/photo-1602216056096-3b40cc0c9944?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Backwaters, green hills and coastal life.',
    description: 'Palm-fringed lagoons, traditional wooden kettuvallam houseboats, and Ayurvedic spice plantations.',
    mood: 'Tranquil & Lush',
    category: ['backwaters', 'nature', 'relaxation', 'coastal'],
    landscape: 'Nature',
    fallbackGradient: 'from-[#13221B] via-[#20573F] to-[#E7A07F]',
  },
  kochi: {
    name: 'Kochi',
    country: 'India',
    region: 'Kerala',
    imageUrl: 'https://images.unsplash.com/photo-1585135759902-b0d5c0e0b3fa?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Chinese fishing nets, spice warehouses and colonial Fort Kochi art.',
    description: 'Historic maritime spice port where Dutch, Portuguese, and British legacies blend with contemporary art.',
    mood: 'Artistic & Maritime',
    category: ['heritage', 'art', 'food', 'coastal'],
    landscape: 'Cultural',
    fallbackGradient: 'from-[#152329] via-[#205562] to-[#E7A07F]',
  },
  munnar: {
    name: 'Munnar',
    country: 'India',
    region: 'Kerala',
    imageUrl: 'https://images.unsplash.com/photo-1596176530529-78163a4f7af2?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Endless rolling tea carpet hills wrapped in mountain fog.',
    description: 'High-altitude Western Ghats sanctuary famous for Nilgiri tahr, cool mountain breeze, and spice trails.',
    mood: 'Lush & Mountain',
    category: ['mountains', 'tea', 'nature'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#13221B] via-[#265F42] to-[#DCCDB5]',
  },
  alleppey: {
    name: 'Alleppey (Alappuzha)',
    country: 'India',
    region: 'Kerala',
    imageUrl: 'https://images.unsplash.com/photo-1593693397690-362cb9666fc2?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Slow journeys through mirror-like canals and water-bound villages.',
    description: 'The Venice of the East: thatch-roof houseboats gliding through tranquil palm canals at dusk.',
    mood: 'Slow & Serene',
    category: ['backwaters', 'nature', 'slow-travel'],
    landscape: 'Nature',
    fallbackGradient: 'from-[#142322] via-[#225752] to-[#E7A07F]',
  },
  bengaluru: {
    name: 'Bengaluru',
    country: 'India',
    region: 'Karnataka',
    imageUrl: 'https://images.unsplash.com/photo-1596176530529-78163a4f7af2?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Garden City parks, craft breweries and innovative modern pulse.',
    description: 'Cubbon Park greenery, filter coffee cafes, tech vibrancy, and temperate year-round weather.',
    mood: 'Contemporary & Green',
    category: ['city', 'food', 'culture', 'parks'],
    landscape: 'City',
    fallbackGradient: 'from-[#152220] via-[#1F4A44] to-[#DCCDB5]',
  },
  mysuru: {
    name: 'Mysuru',
    country: 'India',
    region: 'Karnataka',
    imageUrl: 'https://images.unsplash.com/photo-1590050752117-238cb0fb12b1?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Illuminated royal palaces, sandalwood scents and silk bazaars.',
    description: 'Heritage city of the Wodeyars famous for the grand illuminated Mysore Palace and yoga traditions.',
    mood: 'Regal & Cultural',
    category: ['heritage', 'architecture', 'culture'],
    landscape: 'Historic',
    fallbackGradient: 'from-[#221B14] via-[#65431E] to-[#DCCDB5]',
  },
  chennai: {
    name: 'Chennai',
    country: 'India',
    region: 'Tamil Nadu',
    imageUrl: 'https://images.unsplash.com/photo-1582510003544-4d00b7f74220?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Marina Beach dawns, Carnatic classical music and Dravidian temple art.',
    description: 'Gateway to South India: ancient Kapaleeshwarar gopurams, coastal strolls and filter coffee culture.',
    mood: 'Classical & Coastal',
    category: ['culture', 'heritage', 'beach', 'music'],
    landscape: 'Cultural',
    fallbackGradient: 'from-[#19222B] via-[#244C65] to-[#E7A07F]',
  },
  ooty: {
    name: 'Ooty (Udhagamandalam)',
    country: 'India',
    region: 'Tamil Nadu',
    imageUrl: 'https://images.unsplash.com/photo-1589182373726-e4f658ab50f0?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Blue mountain eucalyptus hills and the Nilgiri Mountain Railway.',
    description: 'Queen of Hill Stations with colonial botanical gardens, lake boat houses, and cool pine ridges.',
    mood: 'Nostalgic & Alpine',
    category: ['mountains', 'nature', 'hills'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#162420] via-[#265345] to-[#DCCDB5]',
  },
  coorg: {
    name: 'Coorg (Kodagu)',
    country: 'India',
    region: 'Karnataka',
    imageUrl: 'https://images.unsplash.com/photo-1596176530529-78163a4f7af2?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Coffee estate trails, stream waterfalls and misty Western Ghats.',
    description: 'The Scotland of India: homestays tucked into cardamom and coffee plantations with mountain mist.',
    mood: 'Fragrant & Peaceful',
    category: ['nature', 'coffee', 'hills', 'relaxation'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#14231C] via-[#24543C] to-[#DCCDB5]',
  },

  // ==================== ISLANDS ====================
  andaman: {
    name: 'Andaman & Nicobar',
    country: 'India',
    region: 'Andaman & Nicobar Islands',
    imageUrl: 'https://images.unsplash.com/photo-1589308078059-be1415eab4c3?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Turquoise waters, pristine coral reefs and tropical rainforests.',
    description: 'Radhanagar Beach white sands, crystal-clear scuba diving bays, and untouched tropical islands.',
    mood: 'Tropical & Untouched',
    category: ['beach', 'islands', 'diving', 'nature'],
    landscape: 'Island',
    fallbackGradient: 'from-[#122A2E] via-[#1E6068] to-[#E7A07F]',
  },
  lakshadweep: {
    name: 'Lakshadweep',
    country: 'India',
    region: 'Lakshadweep',
    imageUrl: 'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Atolls of coral lagoons, emerald waters and sun-washed shores.',
    description: 'Secluded archipelago in the Arabian Sea with shallow lagoon swimming and coconut palm coves.',
    mood: 'Secluded & Paradise',
    category: ['beach', 'islands', 'lagoons', 'nature'],
    landscape: 'Island',
    fallbackGradient: 'from-[#11262B] via-[#1C6972] to-[#E7A07F]',
  },

  // ==================== INTERNATIONAL ====================
  paris: {
    name: 'Paris',
    country: 'France',
    region: 'Île-de-France',
    imageUrl: 'https://images.unsplash.com/photo-1502602898657-3e91760cbb34?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Café culture, grand boulevards & evening Seine reflections',
    description: 'Iconic art museums, architectural grandeur, and timeless Parisian sidewalk bistros.',
    mood: 'Romantic & Cultural',
    category: ['city', 'culture', 'art', 'food'],
    landscape: 'City',
    fallbackGradient: 'from-[#17262A] via-[#1E3B3F] to-[#C96F52]',
  },
  kyoto: {
    name: 'Kyoto',
    country: 'Japan',
    region: 'Kansai',
    imageUrl: 'https://images.unsplash.com/photo-1493976040374-85c8e12f0c0e?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Ancient cedar paths, morning tea rituals & autumn maple foliage',
    description: 'Zen stone gardens, historic bamboo groves, and preserved wooden machiya architecture.',
    mood: 'Tranquil & Historic',
    category: ['heritage', 'culture', 'nature', 'zen'],
    landscape: 'Cultural',
    fallbackGradient: 'from-[#1F2C2A] via-[#174D48] to-[#DCCDB5]',
  },
  tokyo: {
    name: 'Tokyo',
    country: 'Japan',
    region: 'Kanto',
    imageUrl: 'https://images.unsplash.com/photo-1503899036084-c55cdd92da26?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Neon nightscapes, quiet shrine alleys & world-class gastronomy',
    description: 'Futuristic metropolis seamlessly interwoven with neighborhood shrines and culinary excellence.',
    mood: 'Modern & Culinary',
    category: ['city', 'food', 'technology', 'culture'],
    landscape: 'City',
    fallbackGradient: 'from-[#171E26] via-[#176B63] to-[#E7A07F]',
  },
  bali: {
    name: 'Bali',
    country: 'Indonesia',
    region: 'Bali',
    imageUrl: 'https://images.unsplash.com/photo-1537996194471-e657df975ab4?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Cascading emerald rice terraces, cliffside sanctuaries & ocean breezes',
    description: 'Spiritual wellness retreats, volcanic ridge walks, and warm Indian Ocean beaches.',
    mood: 'Spiritual & Tropical',
    category: ['beach', 'nature', 'wellness', 'culture'],
    landscape: 'Nature',
    fallbackGradient: 'from-[#142322] via-[#205852] to-[#DCCDB5]',
  },
  istanbul: {
    name: 'Istanbul',
    country: 'Turkey',
    region: 'Marmara',
    imageUrl: 'https://images.unsplash.com/photo-1524231757912-21f4fe3a7200?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Minaret skylines, bustling spice bazaars & the glittering Bosphorus',
    description: 'The historic bridge between continents where Byzantine and Ottoman masterpieces meet.',
    mood: 'Historic & Vibrant',
    category: ['heritage', 'history', 'city', 'food'],
    landscape: 'City',
    fallbackGradient: 'from-[#1E2229] via-[#176B63] to-[#C96F52]',
  },
  swiss_alps: {
    name: 'Swiss Alps',
    country: 'Switzerland',
    region: 'Bernese Oberland',
    imageUrl: 'https://images.unsplash.com/photo-1530122037265-a5f1f91d3b99?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Glacier peaks, panoramic mountain rails & quiet alpine meadows',
    description: 'Crisp alpine air, classic cogwheel trains, and wooden chalets beneath the Eiger.',
    mood: 'Adventure & Serene',
    category: ['mountains', 'nature', 'adventure'],
    landscape: 'Mountain',
    fallbackGradient: 'from-[#112024] via-[#234A4D] to-[#E9EEF0]',
  },
  amalfi: {
    name: 'Amalfi Coast',
    country: 'Italy',
    region: 'Campania',
    imageUrl: 'https://images.unsplash.com/photo-1533105079780-92b9be482077?auto=format&fit=crop&w=1400&q=80',
    tagline: 'Pastel cliffside villages, fragrant lemon groves & cobalt waters',
    description: 'Dramatic coastal cliffs, seaside villas, and sun-soaked Mediterranean pacing.',
    mood: 'Scenic & Romantic',
    category: ['beach', 'coastal', 'food', 'scenic'],
    landscape: 'Beach',
    fallbackGradient: 'from-[#16272B] via-[#2C7A78] to-[#C96F52]',
  },
};

// ==================== FEATURED LISTS ====================

export const INDIAN_FEATURED_DESTINATIONS: DestinationImageMeta[] = [
  DESTINATION_CATALOG.goa,
  DESTINATION_CATALOG.srinagar,
  DESTINATION_CATALOG.kerala,
  DESTINATION_CATALOG.jaipur,
  DESTINATION_CATALOG.darjeeling,
  DESTINATION_CATALOG.gangtok,
  DESTINATION_CATALOG.cherrapunji,
  DESTINATION_CATALOG.andaman,
];

export const INTERNATIONAL_FEATURED_DESTINATIONS: DestinationImageMeta[] = [
  DESTINATION_CATALOG.paris,
  DESTINATION_CATALOG.kyoto,
  DESTINATION_CATALOG.tokyo,
  DESTINATION_CATALOG.bali,
  DESTINATION_CATALOG.istanbul,
  DESTINATION_CATALOG.amalfi,
  DESTINATION_CATALOG.swiss_alps,
];

// Quick Picks for Create Journey
export const QUICK_PICKS_INDIA = [
  'Goa, India',
  'Kerala, India',
  'Srinagar (Kashmir), India',
  'Jaipur, India',
  'Darjeeling, India',
  'Gangtok (Sikkim), India',
  'Udaipur, India',
  'Andaman & Nicobar, India',
];

export const QUICK_PICKS_INTERNATIONAL = [
  'Paris, France',
  'Kyoto, Japan',
  'Tokyo, Japan',
  'Bali, Indonesia',
  'Istanbul, Turkey',
  'Amalfi Coast, Italy',
];

// ==================== EDITORIAL TRAVEL STORIES ====================

export interface IndiaTravelStory {
  id: string;
  title: string;
  subtitle: string;
  destination: string;
  imageUrl: string;
  tag: string;
  description: string;
}

export const INDIA_TRAVEL_STORIES: IndiaTravelStory[] = [
  {
    id: 'kerala-slow',
    title: 'Slow journeys through Kerala',
    subtitle: 'Backwaters, green hills and coastal life',
    destination: 'Kerala, India',
    imageUrl: 'https://images.unsplash.com/photo-1602216056096-3b40cc0c9944?auto=format&fit=crop&w=1200&q=80',
    tag: 'Waterways & Ayurveda',
    description: 'Drifting on silent houseboats through Vembanad Lake, waking to cardamom-scented breezes in Munnar tea hills.',
  },
  {
    id: 'himalayan-mornings',
    title: 'Himalayan mornings',
    subtitle: 'Mist, prayer flags and mountain teas',
    destination: 'Darjeeling, India',
    imageUrl: 'https://images.unsplash.com/photo-1544735716-392fe2489ffa?auto=format&fit=crop&w=1200&q=80',
    tag: 'Mountain Quietude',
    description: 'Watching first sunlight strike the Kanchenjunga ridge while steam rises from fresh first-flush Darjeeling cups.',
  },
  {
    id: 'royal-rajasthan',
    title: 'Royal Rajasthan',
    subtitle: 'Forts, palaces and desert light',
    destination: 'Udaipur, India',
    imageUrl: 'https://images.unsplash.com/photo-1599661046289-e31897846e41?auto=format&fit=crop&w=1200&q=80',
    tag: 'Living Heritage',
    description: 'Courtyard lamps reflecting on Lake Pichola, intricate stone jali screens, and amber sunset over the Aravalli hills.',
  },
  {
    id: 'northeast-clouds',
    title: 'Northeast in the clouds',
    subtitle: 'Living-root bridges and emerald falls',
    destination: 'Cherrapunji & Meghalaya, India',
    imageUrl: 'https://images.unsplash.com/photo-1600585154340-be6161a56a0c?auto=format&fit=crop&w=1200&q=80',
    tag: 'Rainforest & Living Bridges',
    description: 'Walking beneath living ficus roots woven across centuries, through crystal rivers and mist-covered Khasi ridges.',
  },
  {
    id: 'coastal-india',
    title: 'Coastal India',
    subtitle: 'Sunlit palm shores and slow evenings',
    destination: 'Goa, India',
    imageUrl: 'https://images.unsplash.com/photo-1512343879784-a960bf40e7f2?auto=format&fit=crop&w=1200&q=80',
    tag: 'Coast & Sunshine',
    description: 'Cobblestone lanes in Portuguese quarters, fisherman boat landings at dusk, and coastal coconut curries.',
  },
  {
    id: 'weekend-escapes',
    title: 'Weekend escapes from the city',
    subtitle: 'Quick scenic breaks within easy reach',
    destination: 'Kolkata, India',
    imageUrl: 'https://images.unsplash.com/photo-1558431382-27e303142255?auto=format&fit=crop&w=1200&q=80',
    tag: 'Heritage & Food',
    description: 'Recharging across river ghats, heritage manor stays, and century-old culinary lanes.',
  },
];

// ==================== DISCOVERY CATEGORIES & MOODS ====================

export const TRAVEL_MOODS = [
  {
    id: 'beach',
    label: 'Coast & Islands',
    icon: 'Sun',
    count: 'Goa · Andaman · Lakshadweep · Varkala',
    places: ['Goa, India', 'Andaman & Nicobar, India', 'Lakshadweep, India', 'Kerala, India'],
    image: 'https://images.unsplash.com/photo-1512343879784-a960bf40e7f2?auto=format&fit=crop&w=800&q=80',
  },
  {
    id: 'mountains',
    label: 'Himalayas & Highlands',
    icon: 'Mountain',
    count: 'Kashmir · Ladakh · Darjeeling · Sikkim · Manali',
    places: ['Srinagar, India', 'Leh Ladakh, India', 'Darjeeling, India', 'Gangtok, India', 'Manali, India'],
    image: 'https://images.unsplash.com/photo-1598091383021-15ddea10925d?auto=format&fit=crop&w=800&q=80',
  },
  {
    id: 'heritage',
    label: 'Heritage & Palaces',
    icon: 'Compass',
    count: 'Jaipur · Udaipur · Jodhpur · Agra · Kolkata · Delhi',
    places: ['Jaipur, India', 'Udaipur, India', 'Agra, India', 'Kolkata, India', 'Delhi, India'],
    image: 'https://images.unsplash.com/photo-1599661046289-e31897846e41?auto=format&fit=crop&w=800&q=80',
  },
  {
    id: 'spiritual',
    label: 'Spiritual & Sacred',
    icon: 'Sparkles',
    count: 'Varanasi · Rishikesh · Amritsar · Puri',
    places: ['Varanasi, India', 'Rishikesh, India', 'Amritsar, India', 'Puri, India'],
    image: 'https://images.unsplash.com/photo-1561361513-2d000a50f0dc?auto=format&fit=crop&w=800&q=80',
  },
  {
    id: 'food',
    label: 'Culinary Traditions',
    icon: 'Utensils',
    count: 'Kolkata · Delhi · Kochi · Mumbai · Amritsar',
    places: ['Kolkata, India', 'Delhi, India', 'Kochi, India', 'Mumbai, India'],
    image: 'https://images.unsplash.com/photo-1558431382-27e303142255?auto=format&fit=crop&w=800&q=80',
  },
];

export const TRAVEL_LANDSCAPES = [
  { id: 'beach', label: 'Coast & Tropical', destination: 'Goa, India', image: 'https://images.unsplash.com/photo-1512343879784-a960bf40e7f2?auto=format&fit=crop&w=600&q=80' },
  { id: 'mountain', label: 'Himalayan Slopes', destination: 'Darjeeling, India', image: 'https://images.unsplash.com/photo-1544735716-392fe2489ffa?auto=format&fit=crop&w=600&q=80' },
  { id: 'historic', label: 'Forts & Havelis', destination: 'Udaipur, India', image: 'https://images.unsplash.com/photo-1599661046289-e31897846e41?auto=format&fit=crop&w=600&q=80' },
  { id: 'waterways', label: 'Lagoons & Canals', destination: 'Kerala, India', image: 'https://images.unsplash.com/photo-1602216056096-3b40cc0c9944?auto=format&fit=crop&w=600&q=80' },
];

export const getDestinationImage = (destinationName?: string): DestinationImageMeta => {
  if (!destinationName) return DESTINATION_CATALOG.goa;

  const normalized = destinationName.toLowerCase().trim();

  // Direct exact match
  if (DESTINATION_CATALOG[normalized]) {
    return DESTINATION_CATALOG[normalized];
  }

  // Common synonym / alias mappings
  if (normalized.includes('kashmir') || normalized.includes('dal lake') || normalized.includes('gulmarg') || normalized.includes('pahalgam')) {
    return DESTINATION_CATALOG.srinagar;
  }
  if (normalized.includes('ladakh') || normalized.includes('pangong') || normalized.includes('nubra')) {
    return DESTINATION_CATALOG.leh;
  }
  if (normalized.includes('rajasthan')) {
    return DESTINATION_CATALOG.jaipur;
  }
  if (normalized.includes('meghalaya') || normalized.includes('living root') || normalized.includes('sohra')) {
    return DESTINATION_CATALOG.cherrapunji;
  }
  if (normalized.includes('alappuzha')) {
    return DESTINATION_CATALOG.alleppey;
  }
  if (normalized.includes('nicobar') || normalized.includes('havelock') || normalized.includes('port blair')) {
    return DESTINATION_CATALOG.andaman;
  }
  if (normalized.includes('kodagu')) {
    return DESTINATION_CATALOG.coorg;
  }
  if (normalized.includes('udhagamandalam')) {
    return DESTINATION_CATALOG.ooty;
  }

  // Iterate over catalog entries
  for (const [key, meta] of Object.entries(DESTINATION_CATALOG)) {
    if (
      normalized.includes(key) ||
      normalized.includes(meta.name.toLowerCase()) ||
      normalized.includes(meta.region.toLowerCase()) ||
      (meta.country.toLowerCase() === normalized && key === 'goa')
    ) {
      return meta;
    }
  }

  // Generic fallback if unknown destination
  return {
    name: destinationName,
    country: 'Journey Discovery',
    region: 'World',
    imageUrl: 'https://images.unsplash.com/photo-1488646953014-85cb44e25828?auto=format&fit=crop&w=1400&q=80',
    tagline: 'A new chapter of discovery and authentic travel moments',
    description: 'Custom journey itinerary balancing authentic sights, regional dining, and comfortable pacing.',
    mood: 'Discovery',
    category: ['travel', 'discovery'],
    landscape: 'Cultural',
    fallbackGradient: 'from-[#18242B] via-[#176B63] to-[#DCCDB5]',
  };
};
