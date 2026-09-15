# -*- coding: utf-8 -*-
"""
Multilingual lexicon powering the offline classification engine.

Why a lexicon at all, when an LLM is available?
-----------------------------------------------
Three reasons, all of them operational rather than academic:

1. DETERMINISM. A public-investment recommendation has to be reproducible and
   auditable. The same request must classify the same way today and at appeal
   six months later.
2. AVAILABILITY. Rural intake must keep working when the uplink does not, and
   a live demo must not depend on someone else's API being up.
3. COST AT NATIONAL SCALE. Tens of millions of requests a year through a
   frontier model is not a defensible line in a national budget.

So the lexicon is the floor, not the ceiling: it handles the ~75% of requests
that are lexically obvious, and routes everything ambiguous to the LLM adapter
and then, if still uncertain, to a human reviewer. See docs/ARCHITECTURE.md.

Coverage here is demonstrative, not exhaustive. In production these tables are
maintained per-country by the national language team -- they are data, not
code, which is what makes the platform forkable by another BRICS nation.
"""

# Unicode script ranges -> candidate languages. Script is a near-perfect
# language signal for Indic and CJK text and needs no model.
SCRIPT_RANGES = [
    ((0x0900, 0x097F), ["hi", "mr"]),      # Devanagari
    ((0x0980, 0x09FF), ["bn", "as"]),      # Bengali-Assamese
    ((0x0A00, 0x0A7F), ["pa"]),            # Gurmukhi
    ((0x0A80, 0x0AFF), ["gu"]),            # Gujarati
    ((0x0B00, 0x0B7F), ["or"]),            # Odia
    ((0x0B80, 0x0BFF), ["ta"]),            # Tamil
    ((0x0C00, 0x0C7F), ["te"]),            # Telugu
    ((0x0C80, 0x0CFF), ["kn"]),            # Kannada
    ((0x0D00, 0x0D7F), ["ml"]),            # Malayalam
    ((0x0600, 0x06FF), ["ur"]),            # Arabic (Urdu)
    ((0x0400, 0x04FF), ["ru"]),            # Cyrillic
    ((0x4E00, 0x9FFF), ["zh"]),            # CJK Unified Ideographs
]

# Disambiguators for scripts and alphabets shared by several languages.
MARKERS = {
    "hi": ["है", "नहीं", "में", "और", "कृपया", "हमारे", "गांव", "यहाँ", "का", "की"],
    "mr": ["आहे", "नाही", "मध्ये", "आणि", "कृपया", "आमच्या", "गाव", "येथे", "चा"],
    "bn": ["আছে", "নেই", "এবং", "আমাদের", "গ্রাম", "এখানে", "করুন"],
    "as": ["আছে", "নাই", "আৰু", "আমাৰ", "গাঁও", "ইয়াত"],
    "en": ["the", "is", "and", "our", "village", "please", "there", "no", "we", "water"],
    "pt": ["não", "está", "nossa", "nosso", "por favor", "aqui", "água", "rua", "cidade", "há"],
    "es": ["no", "está", "nuestra", "nuestro", "por favor", "aquí", "agua", "calle", "hay"],
    "af": ["nie", "die", "ons", "asseblief", "water", "pad", "dorp", "het", "is"],
    "zu": ["akukho", "futhi", "sicela", "amanzi", "indawo", "lapha", "ngicela", "yethu"],
    "xh": ["akukho", "kwaye", "nceda", "amanzi", "apha", "yethu"],
    "st": ["hase", "le", "ka kopo", "metsi", "mona", "rona"],
}

# ---------------------------------------------------------------------------
# Sector lexicon. Terms are lowercased and matched as substrings, so inflected
# forms of the same stem are caught without a morphological analyser.
# ---------------------------------------------------------------------------
SECTOR_LEXICON = {
    "water": {
        "en": ["water", "tap", "borewell", "bore well", "drinking water", "sewage", "drainage",
               "toilet", "sanitation", "pipeline", "handpump", "hand pump", "well", "tanker",
               "sewer", "latrine", "contaminated water", "water supply"],
        "hi": ["पानी", "नल", "पेयजल", "बोरवेल", "नाली", "सीवर", "शौचालय", "हैंडपंप", "जल", "टंकी", "गंदा पानी"],
        "mr": ["पाणी", "नळ", "पिण्याचे पाणी", "गटार", "शौचालय", "विहीर", "टँकर"],
        "bn": ["জল", "পানি", "কল", "পানীয় জল", "নর্দমা", "শৌচাগার", "নলকূপ", "টিউবওয়েল"],
        "kn": ["ನೀರು", "ನಲ್ಲಿ", "ಕುಡಿಯುವ ನೀರು", "ಚರಂಡಿ", "ಶೌಚಾಲಯ", "ಬೋರ್‌ವೆಲ್", "ಕೊಳವೆಬಾವಿ"],
        "ta": ["தண்ணீர்", "குழாய்", "குடிநீர்", "கழிவுநீர்", "கழிப்பறை", "ஆழ்துளை கிணறு"],
        "te": ["నీరు", "నల్లా", "తాగునీరు", "మురుగు", "మరుగుదొడ్డి", "బోరుబావి"],
        "gu": ["પાણી", "નળ", "પીવાનું પાણી", "ગટર", "શૌચાલય", "બોરવેલ"],
        "ml": ["വെള്ളം", "പൈപ്പ്", "കുടിവെള്ളം", "ഓട", "കക്കൂസ്", "കുഴൽക്കിണർ"],
        "pa": ["ਪਾਣੀ", "ਨਲਕਾ", "ਪੀਣ ਵਾਲਾ ਪਾਣੀ", "ਨਾਲੀ", "ਪਖਾਨਾ"],
        "or": ["ପାଣି", "କଳ", "ପିଇବା ପାଣି", "ନର୍ଦ୍ଦମା", "ଶୌଚାଳୟ"],
        "ur": ["پانی", "نل", "پینے کا پانی", "نالی", "بیت الخلا"],
        "pt": ["água", "torneira", "esgoto", "saneamento", "poço", "encanamento", "caixa d'água"],
        "es": ["agua", "grifo", "alcantarillado", "saneamiento", "pozo", "tubería"],
        "zu": ["amanzi", "ithephu", "indle", "ukuhlanzeka", "umthombo"],
        "xh": ["amanzi", "ithephu", "indle", "ucoceko"],
        "af": ["water", "kraan", "riool", "sanitasie", "boorgat"],
        "ru": ["вода", "водопровод", "канализация", "скважина", "туалет"],
        "zh": ["水", "自来水", "饮用水", "下水道", "厕所", "供水"],
    },
    "roads": {
        "en": ["road", "pothole", "street", "bridge", "culvert", "highway", "footpath", "pavement",
               "kutcha road", "road repair", "broken road", "lane", "connectivity"],
        "hi": ["सड़क", "गड्ढा", "पुल", "रास्ता", "मार्ग", "फुटपाथ", "कच्ची सड़क", "राजमार्ग"],
        "mr": ["रस्ता", "खड्डा", "पूल", "मार्ग", "पदपथ"],
        "bn": ["রাস্তা", "গর্ত", "সেতু", "পথ", "ফুটপাত", "সড়ক"],
        "kn": ["ರಸ್ತೆ", "ಗುಂಡಿ", "ಸೇತುವೆ", "ದಾರಿ", "ಪಾದಚಾರಿ ಮಾರ್ಗ"],
        "ta": ["சாலை", "பள்ளம்", "பாலம்", "வழி", "நடைபாதை"],
        "te": ["రోడ్డు", "గుంత", "వంతెన", "దారి", "కాలిబాట"],
        "gu": ["રસ્તો", "ખાડો", "પુલ", "માર્ગ", "ફૂટપાથ"],
        "ml": ["റോഡ്", "കുഴി", "പാലം", "വഴി", "നടപ്പാത"],
        "pa": ["ਸੜਕ", "ਟੋਆ", "ਪੁਲ", "ਰਸਤਾ"],
        "or": ["ରାସ୍ତା", "ଗାତ", "ସେତୁ", "ପଥ"],
        "ur": ["سڑک", "گڑھا", "پل", "راستہ"],
        "pt": ["estrada", "buraco", "ponte", "rua", "calçada", "asfalto", "rodovia"],
        "es": ["carretera", "bache", "puente", "calle", "acera", "camino"],
        "zu": ["umgwaqo", "imbobo", "ibhuloho", "indlela"],
        "xh": ["indlela", "umngxuma", "ibhulorho"],
        "af": ["pad", "slaggat", "brug", "straat", "sypaadjie"],
        "ru": ["дорога", "яма", "мост", "улица", "тротуар"],
        "zh": ["道路", "坑洼", "桥", "街道", "人行道", "公路"],
    },
    "health": {
        "en": ["hospital", "clinic", "doctor", "medicine", "health centre", "health center", "phc",
               "ambulance", "nurse", "dispensary", "maternity", "vaccine", "medical"],
        "hi": ["अस्पताल", "डॉक्टर", "दवा", "स्वास्थ्य केंद्र", "एम्बुलेंस", "नर्स", "इलाज", "टीका"],
        "mr": ["रुग्णालय", "डॉक्टर", "औषध", "आरोग्य केंद्र", "रुग्णवाहिका"],
        "bn": ["হাসপাতাল", "ডাক্তার", "ওষুধ", "স্বাস্থ্য কেন্দ্র", "অ্যাম্বুলেন্স"],
        "kn": ["ಆಸ್ಪತ್ರೆ", "ವೈದ್ಯ", "ಔಷಧ", "ಆರೋಗ್ಯ ಕೇಂದ್ರ", "ಆಂಬ್ಯುಲೆನ್ಸ್"],
        "ta": ["மருத்துவமனை", "மருத்துவர்", "மருந்து", "சுகாதார நிலையம்", "ஆம்புலன்ஸ்"],
        "te": ["ఆసుపత్రి", "వైద్యుడు", "మందు", "ఆరోగ్య కేంద్రం", "అంబులెన్స్"],
        "gu": ["હોસ્પિટલ", "ડૉક્ટર", "દવા", "આરોગ્ય કેન્દ્ર", "એમ્બ્યુલન્સ"],
        "ml": ["ആശുപത്രി", "ഡോക്ടർ", "മരുന്ന്", "ആരോഗ്യ കേന്ദ്രം", "ആംബുലൻസ്"],
        "pa": ["ਹਸਪਤਾਲ", "ਡਾਕਟਰ", "ਦਵਾਈ", "ਸਿਹਤ ਕੇਂਦਰ"],
        "or": ["ଡାକ୍ତରଖାନା", "ଡାକ୍ତର", "ଔଷଧ", "ସ୍ୱାସ୍ଥ୍ୟ କେନ୍ଦ୍ର"],
        "ur": ["ہسپتال", "ڈاکٹر", "دوا", "صحت مرکز"],
        "pt": ["hospital", "posto de saúde", "médico", "remédio", "ambulância", "ubs", "enfermeira"],
        "es": ["hospital", "clínica", "médico", "medicina", "ambulancia", "centro de salud"],
        "zu": ["isibhedlela", "udokotela", "umuthi", "umtholampilo"],
        "xh": ["isibhedlele", "ugqirha", "amayeza", "ikliniki"],
        "af": ["hospitaal", "kliniek", "dokter", "medisyne", "ambulans"],
        "ru": ["больница", "врач", "лекарство", "поликлиника", "скорая"],
        "zh": ["医院", "医生", "药", "卫生院", "救护车"],
    },
    "education": {
        "en": ["school", "teacher", "college", "classroom", "student", "anganwadi", "education",
               "textbook", "midday meal", "university", "scholarship"],
        "hi": ["स्कूल", "शिक्षक", "विद्यालय", "कॉलेज", "कक्षा", "आंगनवाड़ी", "शिक्षा", "छात्र"],
        "mr": ["शाळा", "शिक्षक", "महाविद्यालय", "वर्ग", "अंगणवाडी", "शिक्षण"],
        "bn": ["স্কুল", "শিক্ষক", "বিদ্যালয়", "কলেজ", "শ্রেণিকক্ষ", "অঙ্গনওয়াড়ি"],
        "kn": ["ಶಾಲೆ", "ಶಿಕ್ಷಕ", "ಕಾಲೇಜು", "ತರಗತಿ", "ಅಂಗನವಾಡಿ", "ಶಿಕ್ಷಣ"],
        "ta": ["பள்ளி", "ஆசிரியர்", "கல்லூரி", "வகுப்பு", "அங்கன்வாடி", "கல்வி"],
        "te": ["పాఠశాల", "ఉపాధ్యాయుడు", "కళాశాల", "తరగతి", "అంగన్‌వాడీ", "విద్య"],
        "gu": ["શાળા", "શિક્ષક", "કૉલેજ", "વર્ગ", "આંગણવાડી", "શિક્ષણ"],
        "ml": ["സ്കൂൾ", "അധ്യാപകൻ", "കോളേജ്", "ക്ലാസ്", "അങ്കണവാടി", "വിദ്യാഭ്യാസം"],
        "pa": ["ਸਕੂਲ", "ਅਧਿਆਪਕ", "ਕਾਲਜ", "ਜਮਾਤ"],
        "or": ["ବିଦ୍ୟାଳୟ", "ଶିକ୍ଷକ", "କଲେଜ", "ଶ୍ରେଣୀ"],
        "ur": ["اسکول", "استاد", "کالج", "تعلیم"],
        "pt": ["escola", "professor", "faculdade", "sala de aula", "creche", "educação", "merenda"],
        "es": ["escuela", "maestro", "colegio", "aula", "educación", "beca"],
        "zu": ["isikole", "uthisha", "ikilasi", "imfundo"],
        "xh": ["isikolo", "utitshala", "iklasi", "imfundo"],
        "af": ["skool", "onderwyser", "klaskamer", "opvoeding"],
        "ru": ["школа", "учитель", "класс", "образование", "детский сад"],
        "zh": ["学校", "老师", "教室", "教育", "幼儿园"],
    },
    "power": {
        "en": ["electricity", "power cut", "transformer", "streetlight", "street light", "voltage",
               "power supply", "outage", "load shedding", "electric pole", "wiring", "blackout"],
        "hi": ["बिजली", "ट्रांसफार्मर", "स्ट्रीट लाइट", "वोल्टेज", "बिजली कटौती", "खंभा", "बत्ती"],
        "mr": ["वीज", "ट्रान्सफॉर्मर", "पथदिवा", "वीजपुरवठा"],
        "bn": ["বিদ্যুৎ", "ট্রান্সফরমার", "রাস্তার বাতি", "লোডশেডিং"],
        "kn": ["ವಿದ್ಯುತ್", "ಟ್ರಾನ್ಸ್‌ಫಾರ್ಮರ್", "ಬೀದಿ ದೀಪ", "ಕರೆಂಟ್"],
        "ta": ["மின்சாரம்", "மின்மாற்றி", "தெரு விளக்கு", "மின்வெட்டு"],
        "te": ["విద్యుత్", "ట్రాన్స్‌ఫార్మర్", "వీధి దీపం", "కరెంటు"],
        "gu": ["વીજળી", "ટ્રાન્સફોર્મર", "સ્ટ્રીટ લાઇટ"],
        "ml": ["വൈദ്യുതി", "ട്രാൻസ്ഫോർമർ", "തെരുവ് വിളക്ക്"],
        "pa": ["ਬਿਜਲੀ", "ਟਰਾਂਸਫਾਰਮਰ", "ਸਟਰੀਟ ਲਾਈਟ"],
        "or": ["ବିଦ୍ୟୁତ୍", "ଟ୍ରାନ୍ସଫର୍ମର", "ରାସ୍ତା ବତି"],
        "ur": ["بجلی", "ٹرانسفارمر", "اسٹریٹ لائٹ"],
        "pt": ["energia", "luz", "transformador", "poste", "apagão", "iluminação pública"],
        "es": ["electricidad", "luz", "transformador", "apagón", "alumbrado"],
        "zu": ["ugesi", "itransifoma", "isibani sasemgwaqweni"],
        "xh": ["umbane", "isibane sesitrato"],
        "af": ["elektrisiteit", "krag", "transformator", "straatlig", "beurtkrag"],
        "ru": ["электричество", "трансформатор", "фонарь", "отключение света"],
        "zh": ["电", "停电", "变压器", "路灯", "供电"],
    },
    "transport": {
        "en": ["bus", "bus stop", "train", "railway", "metro", "auto", "public transport",
               "bus service", "station", "ferry", "transport"],
        "hi": ["बस", "ट्रेन", "रेलवे", "मेट्रो", "बस स्टॉप", "परिवहन", "सवारी"],
        "mr": ["बस", "रेल्वे", "एसटी", "बस थांबा", "वाहतूक"],
        "bn": ["বাস", "ট্রেন", "রেল", "মেট্রো", "বাস স্টপ", "পরিবহন"],
        "kn": ["ಬಸ್", "ರೈಲು", "ಮೆಟ್ರೋ", "ಬಸ್ ನಿಲ್ದಾಣ", "ಸಾರಿಗೆ"],
        "ta": ["பேருந்து", "ரயில்", "மெட்ரோ", "பஸ் நிலையம்", "போக்குவரத்து"],
        "te": ["బస్సు", "రైలు", "మెట్రో", "బస్ స్టాప్", "రవాణా"],
        "gu": ["બસ", "ટ્રેન", "રેલવે", "બસ સ્ટોપ", "પરિવહન"],
        "ml": ["ബസ്", "ട്രെയിൻ", "മെട്രോ", "ബസ് സ്റ്റോപ്പ്", "ഗതാഗതം"],
        "pa": ["ਬੱਸ", "ਰੇਲ", "ਬੱਸ ਸਟਾਪ"],
        "or": ["ବସ୍", "ଟ୍ରେନ୍", "ରେଳ", "ପରିବହନ"],
        "ur": ["بس", "ٹرین", "ریلوے", "اسٹاپ"],
        "pt": ["ônibus", "trem", "metrô", "ponto de ônibus", "transporte público", "barca"],
        "es": ["autobús", "tren", "metro", "parada", "transporte público"],
        "zu": ["ibhasi", "isitimela", "isiteshi", "ezokuthutha"],
        "xh": ["ibhasi", "uloliwe", "isikhululo"],
        "af": ["bus", "trein", "bushalte", "openbare vervoer"],
        "ru": ["автобус", "поезд", "метро", "остановка", "транспорт"],
        "zh": ["公交", "火车", "地铁", "公交站", "交通"],
    },
    "housing": {
        "en": ["house", "housing", "slum", "shelter", "rent", "eviction", "hut", "roof",
               "settlement", "homeless", "colony", "quarters"],
        "hi": ["मकान", "आवास", "झुग्गी", "घर", "छत", "किराया", "बस्ती", "बेघर"],
        "mr": ["घर", "निवास", "झोपडपट्टी", "छत", "वस्ती"],
        "bn": ["বাড়ি", "আবাসন", "বস্তি", "ছাদ", "ঘর"],
        "kn": ["ಮನೆ", "ವಸತಿ", "ಕೊಳಚೆ ಪ್ರದೇಶ", "ಸೂರು"],
        "ta": ["வீடு", "குடியிருப்பு", "குடிசை", "கூரை"],
        "te": ["ఇల్లు", "గృహ", "మురికివాడ", "పైకప్పు"],
        "gu": ["ઘર", "આવાસ", "ઝૂંપડપટ્ટી", "છત"],
        "ml": ["വീട്", "പാർപ്പിടം", "ചേരി", "മേൽക്കൂര"],
        "pa": ["ਘਰ", "ਮਕਾਨ", "ਝੁੱਗੀ", "ਛੱਤ"],
        "or": ["ଘର", "ଆବାସ", "ବସ୍ତି", "ଛାତ"],
        "ur": ["مکان", "گھر", "کچی آبادی", "چھت"],
        "pt": ["casa", "moradia", "favela", "aluguel", "despejo", "telhado", "habitação"],
        "es": ["casa", "vivienda", "barrio", "alquiler", "desalojo", "techo"],
        "zu": ["indlu", "umjondolo", "uhlalo", "uphahla"],
        "xh": ["indlu", "ityotyombe", "uphahla"],
        "af": ["huis", "behuising", "plakkerskamp", "dak", "huur"],
        "ru": ["жильё", "дом", "квартира", "аренда", "крыша"],
        "zh": ["住房", "房屋", "棚户区", "屋顶", "租金"],
    },
    "digital": {
        "en": ["internet", "network", "mobile signal", "broadband", "wifi", "tower", "connectivity",
               "4g", "5g", "data", "no signal", "telecom"],
        "hi": ["इंटरनेट", "नेटवर्क", "मोबाइल सिग्नल", "ब्रॉडबैंड", "टावर", "सिग्नल नहीं"],
        "mr": ["इंटरनेट", "नेटवर्क", "मोबाइल रेंज", "टॉवर"],
        "bn": ["ইন্টারনেট", "নেটওয়ার্ক", "মোবাইল সিগন্যাল", "টাওয়ার"],
        "kn": ["ಇಂಟರ್ನೆಟ್", "ನೆಟ್‌ವರ್ಕ್", "ಮೊಬೈಲ್ ಸಿಗ್ನಲ್", "ಟವರ್"],
        "ta": ["இணையம்", "நெட்வொர்க்", "மொபைல் சிக்னல்", "கோபுரம்"],
        "te": ["ఇంటర్నెట్", "నెట్‌వర్క్", "మొబైల్ సిగ్నల్", "టవర్"],
        "gu": ["ઇન્ટરનેટ", "નેટવર્ક", "મોબાઇલ સિગ્નલ", "ટાવર"],
        "ml": ["ഇന്റർനെറ്റ്", "നെറ്റ്‌വർക്ക്", "മൊബൈൽ സിഗ്നൽ", "ടവർ"],
        "pa": ["ਇੰਟਰਨੈੱਟ", "ਨੈੱਟਵਰਕ", "ਸਿਗਨਲ"],
        "or": ["ଇଣ୍ଟରନେଟ୍", "ନେଟୱାର୍କ", "ସିଗନାଲ୍"],
        "ur": ["انٹرنیٹ", "نیٹ ورک", "سگنل", "ٹاور"],
        "pt": ["internet", "rede", "sinal", "banda larga", "torre", "celular"],
        "es": ["internet", "red", "señal", "banda ancha", "torre", "cobertura"],
        "zu": ["i-inthanethi", "inethiwekhi", "isignali", "umbhoshongo"],
        "xh": ["i-intanethi", "umnatha", "isignali"],
        "af": ["internet", "netwerk", "sein", "breëband", "toring"],
        "ru": ["интернет", "сеть", "связь", "вышка", "сигнал"],
        "zh": ["网络", "互联网", "信号", "宽带", "基站"],
    },
    "agriculture": {
        "en": ["irrigation", "canal", "crop", "farmer", "fertilizer", "seed", "harvest", "drought",
               "soil", "tractor", "mandi", "farm", "pump set", "cold storage"],
        "hi": ["सिंचाई", "नहर", "फसल", "किसान", "खाद", "बीज", "सूखा", "खेत", "मंडी", "ट्रैक्टर"],
        "mr": ["सिंचन", "कालवा", "पीक", "शेतकरी", "खत", "बियाणे", "दुष्काळ", "शेत"],
        "bn": ["সেচ", "খাল", "ফসল", "কৃষক", "সার", "বীজ", "খরা", "জমি"],
        "kn": ["ನೀರಾವರಿ", "ಕಾಲುವೆ", "ಬೆಳೆ", "ರೈತ", "ಗೊಬ್ಬರ", "ಬೀಜ", "ಬರ", "ಹೊಲ"],
        "ta": ["பாசனம்", "கால்வாய்", "பயிர்", "விவசாயி", "உரம்", "விதை", "வறட்சி", "வயல்"],
        "te": ["నీటిపారుదల", "కాలువ", "పంట", "రైతు", "ఎరువు", "విత్తనం", "కరువు", "పొలం"],
        "gu": ["સિંચાઈ", "નહેર", "પાક", "ખેડૂત", "ખાતર", "બીજ", "દુષ્કાળ", "ખેતર"],
        "ml": ["ജലസേചനം", "കനാൽ", "വിള", "കർഷകൻ", "വളം", "വിത്ത്", "വരൾച്ച"],
        "pa": ["ਸਿੰਚਾਈ", "ਨਹਿਰ", "ਫਸਲ", "ਕਿਸਾਨ", "ਖਾਦ", "ਬੀਜ"],
        "or": ["ଜଳସେଚନ", "କେନାଲ", "ଫସଲ", "ଚାଷୀ", "ସାର", "ମରୁଡ଼ି"],
        "ur": ["آبپاشی", "نہر", "فصل", "کسان", "کھاد", "بیج", "خشک سالی"],
        "pt": ["irrigação", "lavoura", "agricultor", "safra", "adubo", "semente", "seca", "colheita"],
        "es": ["riego", "canal", "cultivo", "agricultor", "fertilizante", "semilla", "sequía"],
        "zu": ["ukunisela", "isivuno", "umlimi", "umquba", "imbewu", "isomiso"],
        "xh": ["unkcenkceshelo", "isivuno", "umlimi", "imbewu"],
        "af": ["besproeiing", "oes", "boer", "kunsmis", "saad", "droogte"],
        "ru": ["орошение", "урожай", "фермер", "удобрение", "семена", "засуха"],
        "zh": ["灌溉", "农作物", "农民", "化肥", "种子", "干旱"],
    },
    "jobs": {
        "en": ["job", "employment", "unemployed", "wages", "mgnrega", "nrega", "skill training",
               "livelihood", "work", "factory", "labour", "labor", "income"],
        "hi": ["नौकरी", "रोजगार", "बेरोजगार", "मजदूरी", "मनरेगा", "काम", "आजीविका", "मजदूर"],
        "mr": ["नोकरी", "रोजगार", "बेरोजगार", "मजुरी", "काम"],
        "bn": ["চাকরি", "কর্মসংস্থান", "বেকার", "মজুরি", "কাজ"],
        "kn": ["ಉದ್ಯೋಗ", "ಕೆಲಸ", "ನಿರುದ್ಯೋಗ", "ಕೂಲಿ", "ಜೀವನೋಪಾಯ"],
        "ta": ["வேலை", "வேலைவாய்ப்பு", "வேலையில்லா", "கூலி", "வாழ்வாதாரம்"],
        "te": ["ఉద్యోగం", "ఉపాధి", "నిరుద్యోగం", "కూలీ", "పని"],
        "gu": ["નોકરી", "રોજગાર", "બેરોજગાર", "મજૂરી", "કામ"],
        "ml": ["ജോലി", "തൊഴിൽ", "തൊഴിലില്ലായ്മ", "കൂലി"],
        "pa": ["ਨੌਕਰੀ", "ਰੁਜ਼ਗਾਰ", "ਬੇਰੁਜ਼ਗਾਰ", "ਮਜ਼ਦੂਰੀ"],
        "or": ["ଚାକିରି", "ନିଯୁକ୍ତି", "ବେକାର", "ମଜୁରି"],
        "ur": ["نوکری", "روزگار", "بے روزگار", "مزدوری"],
        "pt": ["emprego", "desemprego", "salário", "trabalho", "renda", "capacitação"],
        "es": ["empleo", "desempleo", "salario", "trabajo", "ingreso", "capacitación"],
        "zu": ["umsebenzi", "intengo", "ukungasebenzi", "iholo"],
        "xh": ["umsebenzi", "ukungaphangeli", "umvuzo"],
        "af": ["werk", "werkloosheid", "loon", "indiensneming"],
        "ru": ["работа", "занятость", "безработица", "зарплата"],
        "zh": ["就业", "工作", "失业", "工资", "收入"],
    },
}

# ---------------------------------------------------------------------------
# Urgency lexicon. Weighted: a "critical" hit dominates a "high" hit.
# ---------------------------------------------------------------------------
URGENCY_LEXICON = {
    "critical": {
        "en": ["died", "death", "dying", "emergency", "collapsed", "outbreak", "epidemic", "fatal",
               "life threatening", "children are sick", "no water at all", "cholera", "flooded",
               "urgent", "immediately", "danger", "electrocuted", "killed"],
        "hi": ["मौत", "मर गए", "आपातकाल", "गिर गया", "महामारी", "जानलेवा", "तुरंत", "खतरा", "बीमारी फैल"],
        "mr": ["मृत्यू", "आणीबाणी", "कोसळला", "साथीचा रोग", "तातडीने", "धोका"],
        "bn": ["মৃত্যু", "মারা গেছে", "জরুরি", "ধসে", "মহামারী", "বিপদ"],
        "kn": ["ಸಾವು", "ಮರಣ", "ತುರ್ತು", "ಕುಸಿದಿದೆ", "ಸಾಂಕ್ರಾಮಿಕ", "ಅಪಾಯ"],
        "ta": ["இறப்பு", "இறந்த", "அவசர", "இடிந்து", "தொற்று", "ஆபத்து"],
        "te": ["మరణం", "చనిపోయారు", "అత్యవసర", "కూలిపోయింది", "ప్రమాదం"],
        "gu": ["મૃત્યુ", "કટોકટી", "ધરાશાયી", "રોગચાળો", "ભય"],
        "ml": ["മരണം", "അടിയന്തര", "തകർന്നു", "പകർച്ചവ്യാധി", "അപകടം"],
        "pa": ["ਮੌਤ", "ਐਮਰਜੈਂਸੀ", "ਡਿੱਗ", "ਖ਼ਤਰਾ"],
        "or": ["ମୃତ୍ୟୁ", "ଜରୁରୀ", "ଭାଙ୍ଗି", "ବିପଦ"],
        "ur": ["موت", "ہنگامی", "گر گیا", "وبا", "خطرہ"],
        "pt": ["morreu", "morte", "emergência", "desabou", "surto", "risco de vida", "urgente"],
        "es": ["murió", "muerte", "emergencia", "colapsó", "brote", "urgente", "peligro"],
        "zu": ["ukufa", "washona", "isimo esiphuthumayo", "ingozi", "ubhubhane"],
        "xh": ["ukufa", "ingxaki ekhawulezayo", "ingozi"],
        "af": ["dood", "gesterf", "noodgeval", "ineengestort", "uitbraak", "gevaar"],
        "ru": ["смерть", "умер", "чрезвычайная", "обрушился", "эпидемия", "опасно"],
        "zh": ["死亡", "紧急", "倒塌", "疫情", "危险"],
    },
    "high": {
        "en": ["months", "no supply", "not working", "broken", "closed", "shortage", "severe",
               "cannot", "unable", "children", "elderly", "pregnant", "hospital far", "many families",
               "repeatedly", "again and again", "still not", "no response"],
        "hi": ["महीनों", "बंद", "खराब", "टूटा", "कमी", "गंभीर", "बार बार", "कोई जवाब नहीं", "बच्चे", "बुजुर्ग"],
        "mr": ["महिने", "बंद", "खराब", "तुटले", "गंभीर", "वारंवार"],
        "bn": ["মাস", "বন্ধ", "নষ্ট", "ভাঙা", "ঘাটতি", "গুরুতর", "বারবার"],
        "kn": ["ತಿಂಗಳು", "ಮುಚ್ಚಿದೆ", "ಕೆಟ್ಟಿದೆ", "ಕೊರತೆ", "ಗಂಭೀರ", "ಪದೇ ಪದೇ"],
        "ta": ["மாதங்கள்", "மூடப்பட்ட", "பழுது", "பற்றாக்குறை", "தீவிர", "மீண்டும்"],
        "te": ["నెలలు", "మూసివేయబడింది", "పాడైంది", "కొరత", "తీవ్రమైన", "మళ్లీ"],
        "gu": ["મહિના", "બંધ", "ખરાબ", "તૂટેલું", "અછત", "ગંભીર"],
        "ml": ["മാസങ്ങൾ", "അടച്ചു", "തകരാർ", "ക്ഷാമം", "ഗുരുതരം"],
        "pa": ["ਮਹੀਨੇ", "ਬੰਦ", "ਖਰਾਬ", "ਟੁੱਟਿਆ", "ਘਾਟ"],
        "or": ["ମାସ", "ବନ୍ଦ", "ଖରାପ", "ଅଭାବ", "ଗମ୍ଭୀର"],
        "ur": ["مہینے", "بند", "خراب", "ٹوٹا", "قلت", "سنگین"],
        "pt": ["meses", "sem", "quebrado", "fechado", "falta", "grave", "novamente", "sem resposta"],
        "es": ["meses", "sin", "roto", "cerrado", "falta", "grave", "otra vez", "sin respuesta"],
        "zu": ["izinyanga", "akusebenzi", "kuvaliwe", "kwephukile", "ukushoda"],
        "xh": ["iinyanga", "ayisebenzi", "ivaliwe", "yaphukile"],
        "af": ["maande", "werk nie", "gebreek", "gesluit", "tekort", "ernstig"],
        "ru": ["месяцев", "не работает", "сломан", "закрыт", "нехватка", "серьёзно"],
        "zh": ["几个月", "不能用", "坏了", "关闭", "短缺", "严重"],
    },
    "low": {
        "en": ["request", "would like", "suggestion", "consider", "in future", "plan", "someday",
               "it would be good", "propose", "recommend"],
        "hi": ["अनुरोध", "सुझाव", "भविष्य में", "विचार करें", "अच्छा होगा"],
        "mr": ["विनंती", "सूचना", "भविष्यात", "विचार"],
        "bn": ["অনুরোধ", "প্রস্তাব", "ভবিষ্যতে", "বিবেচনা"],
        "kn": ["ಮನವಿ", "ಸಲಹೆ", "ಭವಿಷ್ಯದಲ್ಲಿ", "ಪರಿಗಣಿಸಿ"],
        "ta": ["கோரிக்கை", "பரிந்துரை", "எதிர்காலத்தில்", "பரிசீலிக்க"],
        "te": ["అభ్యర్థన", "సూచన", "భవిష్యత్తులో", "పరిగణించండి"],
        "gu": ["વિનંતી", "સૂચન", "ભવિષ્યમાં"],
        "ml": ["അഭ്യർത്ഥന", "നിർദ്ദേശം", "ഭാവിയിൽ"],
        "pa": ["ਬੇਨਤੀ", "ਸੁਝਾਅ", "ਭਵਿੱਖ ਵਿੱਚ"],
        "or": ["ଅନୁରୋଧ", "ପରାମର୍ଶ", "ଭବିଷ୍ୟତରେ"],
        "ur": ["درخواست", "تجویز", "مستقبل میں"],
        "pt": ["sugestão", "proposta", "no futuro", "seria bom", "considerar"],
        "es": ["sugerencia", "propuesta", "en el futuro", "sería bueno", "considerar"],
        "zu": ["isicelo", "isiphakamiso", "esikhathini esizayo"],
        "xh": ["isicelo", "isindululo"],
        "af": ["versoek", "voorstel", "in die toekoms"],
        "ru": ["предложение", "просьба", "в будущем"],
        "zh": ["建议", "请求", "将来", "考虑"],
    },
}

# Phrases implying how many people are affected. Multiplier applied to a
# baseline household size to estimate reach when no explicit number is given.
SCALE_LEXICON = {
    "village": (["entire village", "whole village", "पूरा गांव", "गांव", "ಇಡೀ ಗ್ರಾಮ", "ಗ್ರಾಮ",
                 "முழு கிராமம்", "கிராமம்", "గ్రామం", "পুরো গ্রাম", "গ্রাম", "गाव", "ગામ",
                 "vila", "aldeia", "pueblo", "umuzi", "dorp", "деревня", "村",
                 "community", "comunidade", "comunidad", "umphakathi", "gemeenskap",
                 "बस्ती", "समुदाय", "ಸಮುದಾಯ", "சமூகம்"], 1800),
    "ward": (["ward", "colony", "mohalla", "वार्ड", "कॉलोनी", "मोहल्ला", "ವಾರ್ಡ್", "வார்டு",
              "వార్డు", "ওয়ার্ড", "bairro", "barrio", "wyk", "район"], 900),
    "block": (["block", "taluk", "tehsil", "mandal", "ब्लॉक", "तहसील", "ತಾಲ್ಲೂಕು", "தாலுகா",
               "మండలం", "ব্লক", "município", "distrito"], 9000),
    "families": (["families", "households", "परिवार", "ಕುಟುಂಬ", "குடும்பங்கள்", "కుటుంబాలు",
                  "পরিবার", "कुटुंब", "પરિવાર", "famílias", "familias", "imindeni", "gesinne",
                  "семей", "户"], 5),
    "street": (["street", "lane", "गली", "ಬೀದಿ", "தெரு", "వీధి", "গলি", "rua", "calle", "straat"], 300),
}

# Patterns that must never reach an analyst screen or a training set.
PII_PATTERNS = [
    (r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "[ID-REDACTED]"),          # Aadhaar-like 12-digit
    (r"\b(?:\+?\d{1,3}[\s-]?)?\d{10}\b", "[PHONE-REDACTED]"),        # phone
    (r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", "[EMAIL-REDACTED]"),            # email
    (r"\b[A-Z]{5}\d{4}[A-Z]\b", "[TAXID-REDACTED]"),                 # PAN-like
    (r"\b\d{2}/\d{2}/\d{4}\b", "[DATE-REDACTED]"),                   # DOB-like
]

# Minimal gloss table: lets the offline engine emit a readable English summary
# without a translation model. The LLM adapter replaces this with real MT.
GLOSS = {
    "water": "water supply / sanitation",
    "roads": "road condition / connectivity",
    "health": "healthcare access",
    "education": "school / education",
    "power": "electricity supply",
    "transport": "public transport",
    "housing": "housing / shelter",
    "digital": "internet / mobile connectivity",
    "agriculture": "irrigation / farming support",
    "jobs": "employment / livelihood",
}
