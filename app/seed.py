# -*- coding: utf-8 -*-
"""
Synthetic citizen-request generator.

Composes requests from per-language sentence frames and sector nouns, so the
corpus is genuinely multilingual rather than English text with a language tag
attached.

Two properties are built in on purpose, because they are what the platform
exists to handle:

1. PARTICIPATION BIAS. Request volume is generated proportional to a district's
   expected participation (smartphone penetration, literacy, urbanisation) and
   NOT to its need. This reproduces the real-world pattern where well-served
   districts dominate the feed. Without it the equity correction would have
   nothing to correct and the demo would prove nothing.

2. NEED-COHERENT TOPICS. Within a district, the sector a request is about is
   drawn weighted by that district's actual infrastructure gaps. People report
   what is broken around them.

A deliberate third property: roughly one request in nine is phrased obliquely,
without any lexicon keyword. Those land in the human review queue, which is the
honest depiction -- a real intake stream is not uniformly machine-readable.
"""
from __future__ import annotations

import hashlib
import random

from .ai.lexicon import SECTOR_LEXICON
from .engine.fusion import load_pack

# Dominant language by region. Real platforms resolve this per-user; here it
# gives the corpus a realistic linguistic geography.
REGION_LANG = {
    "IN": {
        "UP": "hi", "BR": "hi", "MP": "hi", "RJ": "hi", "HR": "hi", "DL": "hi",
        "UK": "hi", "HP": "hi", "CG": "hi", "JH": "hi", "CH": "pa", "PB": "pa",
        "MH": "mr", "GJ": "gu", "DD": "gu", "WB": "bn", "TR": "bn", "AS": "bn",
        "OD": "or", "TG": "te", "AP": "te", "TN": "ta", "PY": "ta",
        "KA": "kn", "KL": "ml", "LD": "ml", "JK": "ur",
    },
    "BR": {}, "ZA": {},
}
DEFAULT_LANG = {"IN": "en", "BR": "pt", "ZA": "en"}
ZA_LANGS = ["en", "zu", "xh", "af", "st"]

# Sentence frames: {issue} is a sector noun, {n} a family count.
FRAMES = {
    "hi": [
        ("critical", "हमारे गांव में {issue} की वजह से हालत बहुत गंभीर है, कई लोग बीमार हैं और एक मौत हो चुकी है"),
        ("high", "पिछले तीन महीने से {issue} की समस्या बनी हुई है, {n} परिवार परेशान हैं और कोई जवाब नहीं मिला"),
        ("medium", "हमारे वार्ड में {issue} की स्थिति ठीक नहीं है, कृपया इस पर ध्यान दें"),
        ("low", "भविष्य में {issue} को बेहतर करने का सुझाव देना चाहते हैं"),
    ],
    "mr": [
        ("critical", "आमच्या गावात {issue} मुळे परिस्थिती गंभीर आहे, लोक आजारी आहेत आणि एक मृत्यू झाला आहे"),
        ("high", "गेले तीन महिने {issue} ची समस्या आहे, {n} कुटुंब त्रस्त आहेत, काहीच उत्तर नाही"),
        ("medium", "आमच्या वार्डात {issue} ची स्थिती चांगली नाही, कृपया लक्ष द्या"),
        ("low", "भविष्यात {issue} सुधारण्याची सूचना आहे"),
    ],
    "bn": [
        ("critical", "আমাদের গ্রামে {issue} এর কারণে অবস্থা গুরুতর, অনেকে অসুস্থ এবং একজনের মৃত্যু হয়েছে"),
        ("high", "গত তিন মাস ধরে {issue} এর সমস্যা চলছে, {n} পরিবার ক্ষতিগ্রস্ত, কোনো উত্তর নেই"),
        ("medium", "আমাদের ওয়ার্ডে {issue} এর অবস্থা ভালো নয়, অনুগ্রহ করে দেখুন"),
        ("low", "ভবিষ্যতে {issue} উন্নত করার প্রস্তাব রাখছি"),
    ],
    "te": [
        ("critical", "మా గ్రామంలో {issue} వల్ల పరిస్థితి తీవ్రంగా ఉంది, చాలా మంది అనారోగ్యంతో ఉన్నారు, ఒక మరణం సంభవించింది"),
        ("high", "గత మూడు నెలలుగా {issue} సమస్య ఉంది, {n} కుటుంబాలు ఇబ్బంది పడుతున్నాయి, స్పందన లేదు"),
        ("medium", "మా వార్డులో {issue} పరిస్థితి బాగోలేదు, దయచేసి పరిశీలించండి"),
        ("low", "భవిష్యత్తులో {issue} మెరుగుపరచాలని సూచన"),
    ],
    "ta": [
        ("critical", "எங்கள் கிராமத்தில் {issue} காரணமாக நிலைமை மோசமாக உள்ளது, பலர் நோய்வாய்ப்பட்டுள்ளனர், ஒரு இறப்பு நிகழ்ந்துள்ளது"),
        ("high", "கடந்த மூன்று மாதங்களாக {issue} பிரச்சினை நீடிக்கிறது, {n} குடும்பங்கள் பாதிக்கப்பட்டுள்ளன, பதில் இல்லை"),
        ("medium", "எங்கள் வார்டில் {issue} நிலை சரியில்லை, தயவுசெய்து கவனியுங்கள்"),
        ("low", "எதிர்காலத்தில் {issue} மேம்படுத்த பரிந்துரை"),
    ],
    "kn": [
        ("critical", "ನಮ್ಮ ಗ್ರಾಮದಲ್ಲಿ {issue} ಕಾರಣದಿಂದ ಪರಿಸ್ಥಿತಿ ಗಂಭೀರವಾಗಿದೆ, ಹಲವರು ಅನಾರೋಗ್ಯದಿಂದ ಇದ್ದಾರೆ, ಒಂದು ಸಾವು ಸಂಭವಿಸಿದೆ"),
        ("high", "ಕಳೆದ ಮೂರು ತಿಂಗಳಿಂದ {issue} ಸಮಸ್ಯೆ ಮುಂದುವರಿದಿದೆ, {n} ಕುಟುಂಬಗಳು ತೊಂದರೆಯಲ್ಲಿವೆ, ಯಾವುದೇ ಉತ್ತರವಿಲ್ಲ"),
        ("medium", "ನಮ್ಮ ವಾರ್ಡ್‌ನಲ್ಲಿ {issue} ಸ್ಥಿತಿ ಸರಿಯಿಲ್ಲ, ದಯವಿಟ್ಟು ಗಮನಿಸಿ"),
        ("low", "ಭವಿಷ್ಯದಲ್ಲಿ {issue} ಸುಧಾರಿಸಲು ಸಲಹೆ"),
    ],
    "gu": [
        ("critical", "અમારા ગામમાં {issue} ને કારણે સ્થિતિ ગંભીર છે, ઘણા બીમાર છે અને એક મૃત્યુ થયું છે"),
        ("high", "છેલ્લા ત્રણ મહિનાથી {issue} ની સમસ્યા છે, {n} પરિવાર પરેશાન છે, કોઈ જવાબ નથી"),
        ("medium", "અમારા વોર્ડમાં {issue} ની સ્થિતિ સારી નથી, કૃપા કરીને ધ્યાન આપો"),
        ("low", "ભવિષ્યમાં {issue} સુધારવાનું સૂચન છે"),
    ],
    "ml": [
        ("critical", "ഞങ്ങളുടെ ഗ്രാമത്തിൽ {issue} കാരണം സ്ഥിതി ഗുരുതരമാണ്, പലരും രോഗബാധിതരാണ്, ഒരു മരണം സംഭവിച്ചു"),
        ("high", "കഴിഞ്ഞ മൂന്ന് മാസമായി {issue} പ്രശ്നം തുടരുന്നു, {n} കുടുംബങ്ങൾ ബുദ്ധിമുട്ടുന്നു, മറുപടിയില്ല"),
        ("medium", "ഞങ്ങളുടെ വാർഡിൽ {issue} സ്ഥിതി മോശമാണ്, ദയവായി ശ്രദ്ധിക്കുക"),
        ("low", "ഭാവിയിൽ {issue} മെച്ചപ്പെടുത്താൻ നിർദ്ദേശം"),
    ],
    "pa": [
        ("critical", "ਸਾਡੇ ਪਿੰਡ ਵਿੱਚ {issue} ਕਾਰਨ ਹਾਲਤ ਗੰਭੀਰ ਹੈ, ਕਈ ਬਿਮਾਰ ਹਨ ਅਤੇ ਇੱਕ ਮੌਤ ਹੋ ਚੁੱਕੀ ਹੈ"),
        ("high", "ਪਿਛਲੇ ਤਿੰਨ ਮਹੀਨੇ ਤੋਂ {issue} ਦੀ ਸਮੱਸਿਆ ਹੈ, {n} ਪਰਿਵਾਰ ਪਰੇਸ਼ਾਨ ਹਨ, ਕੋਈ ਜਵਾਬ ਨਹੀਂ"),
        ("medium", "ਸਾਡੇ ਵਾਰਡ ਵਿੱਚ {issue} ਦੀ ਹਾਲਤ ਠੀਕ ਨਹੀਂ, ਕਿਰਪਾ ਕਰਕੇ ਧਿਆਨ ਦਿਓ"),
        ("low", "ਭਵਿੱਖ ਵਿੱਚ {issue} ਸੁਧਾਰਨ ਦਾ ਸੁਝਾਅ ਹੈ"),
    ],
    "or": [
        ("critical", "ଆମ ଗାଁରେ {issue} କାରଣରୁ ପରିସ୍ଥିତି ଗମ୍ଭୀର, ଅନେକ ଅସୁସ୍ଥ ଏବଂ ଜଣେ ମୃତ୍ୟୁବରଣ କରିଛନ୍ତି"),
        ("high", "ଗତ ତିନି ମାସ ଧରି {issue} ସମସ୍ୟା ଚାଲିଛି, {n} ପରିବାର ପ୍ରଭାବିତ, କୌଣସି ଉତ୍ତର ନାହିଁ"),
        ("medium", "ଆମ ୱାର୍ଡରେ {issue} ସ୍ଥିତି ଭଲ ନାହିଁ, ଦୟାକରି ଧ୍ୟାନ ଦିଅନ୍ତୁ"),
        ("low", "ଭବିଷ୍ୟତରେ {issue} ଉନ୍ନତ କରିବା ପାଇଁ ପରାମର୍ଶ"),
    ],
    "ur": [
        ("critical", "ہمارے گاؤں میں {issue} کی وجہ سے حالت سنگین ہے، کئی لوگ بیمار ہیں اور ایک موت ہو چکی ہے"),
        ("high", "پچھلے تین مہینے سے {issue} کا مسئلہ ہے، {n} خاندان پریشان ہیں، کوئی جواب نہیں ملا"),
        ("medium", "ہمارے وارڈ میں {issue} کی حالت ٹھیک نہیں، براہ کرم توجہ دیں"),
        ("low", "مستقبل میں {issue} بہتر بنانے کی تجویز ہے"),
    ],
    "en": [
        ("critical", "The {issue} situation in our village has become an emergency, several people are ill and one death has been reported"),
        ("high", "For the past three months the {issue} problem has continued, {n} families are affected and there has been no response"),
        ("medium", "The {issue} situation in our ward is not good, please look into it"),
        ("low", "A suggestion to improve {issue} here in the future"),
    ],
    "pt": [
        ("critical", "Na nossa comunidade a situação de {issue} virou emergência, várias pessoas adoeceram e houve uma morte"),
        ("high", "Há três meses o problema de {issue} continua, {n} famílias afetadas e nenhuma resposta até agora"),
        ("medium", "A situação de {issue} no nosso bairro não está boa, por favor verifiquem"),
        ("low", "Uma sugestão para melhorar {issue} no futuro"),
    ],
    "zu": [
        ("critical", "Emphakathini wethu isimo se-{issue} sesibe yisimo esiphuthumayo, abantu abaningi bayagula futhi kukhona oshonile"),
        ("high", "Sekuyizinyanga ezintathu inkinga ye-{issue} iqhubeka, imindeni engu-{n} ithintekile, ayikho impendulo"),
        ("medium", "Isimo se-{issue} endaweni yethu asisihle, sicela nibheke"),
        ("low", "Isiphakamiso sokuthuthukisa i-{issue} esikhathini esizayo"),
    ],
    "xh": [
        ("critical", "Kuluntu lwethu imeko ye-{issue} ibe yingxaki enkulu, abantu abaninzi bayagula kwaye kukho osweleke"),
        ("high", "Sekuziinyanga ezintathu ingxaki ye-{issue} iqhubeka, iintsapho ezingu-{n} zichaphazelekile, akukho mpendulo"),
        ("medium", "Imeko ye-{issue} kwindawo yethu ayilunganga, nceda nijonge"),
        ("low", "Isindululo sokuphucula i-{issue} kwixesha elizayo"),
    ],
    "af": [
        ("critical", "In ons gemeenskap het die {issue}-situasie 'n noodgeval geword, verskeie mense is siek en een sterfte is aangemeld"),
        ("high", "Die afgelope drie maande duur die {issue} probleem voort, {n} gesinne is geraak en daar was geen reaksie nie"),
        ("medium", "Die {issue} situasie in ons wyk is nie goed nie, kyk asseblief daarna"),
        ("low", "'n Voorstel om {issue} in die toekoms te verbeter"),
    ],
    "st": [
        ("critical", "Sechabeng sa rona boemo ba {issue} bo fetohile tsietsi, batho ba bangata baa kula mme ho na le ea hlokahetseng"),
        ("high", "Likhoeli tse tharo tse fetileng bothata ba {issue} bo ntse bo tsoela pele, malapa a {n} a amehile"),
        ("medium", "Boemo ba {issue} sebakeng sa rona ha bo botle, ka kopo hlahlobang"),
        ("low", "Tlhahiso ea ho ntlafatsa {issue} nakong e tlang"),
    ],
}

# Oblique phrasings that contain no sector keyword. These are what a real
# intake stream is full of, and what the review queue exists for.
VAGUE = {
    "en": ["Nothing has been done here for years despite many promises, we are tired of waiting",
           "The officials came, took photographs, and we never heard from them again",
           "Our area is always the last to get anything, please do something"],
    "hi": ["सालों से यहाँ कुछ नहीं हुआ, बस वादे ही मिले हैं, हम थक चुके हैं",
           "अधिकारी आए, फोटो खींची और फिर कभी नहीं लौटे"],
    "bn": ["বছরের পর বছর এখানে কিছুই হয়নি, শুধু প্রতিশ্রুতি পেয়েছি",
           "কর্মকর্তারা এসেছিলেন, ছবি তুলেছেন, তারপর আর খবর নেই"],
    "ta": ["பல வருடங்களாக இங்கு எதுவும் நடக்கவில்லை, வாக்குறுதிகள் மட்டுமே",
           "அதிகாரிகள் வந்தார்கள், புகைப்படம் எடுத்தார்கள், பிறகு தகவல் இல்லை"],
    "pt": ["Há anos nada é feito aqui, só promessas, estamos cansados de esperar",
           "Os funcionários vieram, tiraram fotos e nunca mais voltaram"],
    "zu": ["Iminyaka eminingi akukho okwenziwayo lapha, izethembiso kuphela"],
}


def _issue_noun(sector: str, lang: str, rng: random.Random) -> str:
    """Pick a sector noun in the requester's language, preferring a later
    lexicon entry over the first so the corpus is not a mirror of the
    classifier's most obvious term."""
    terms = SECTOR_LEXICON[sector].get(lang) or SECTOR_LEXICON[sector]["en"]
    return rng.choice(terms[: min(len(terms), 6)])


def generate(country: str = "IN", target: int = 900, seed: int = 20260915) -> list[dict]:
    """Produce raw submissions. They are NOT analysed here -- they go through
    the same AI pipeline as a live submission, so the seeded corpus and the
    demo's live submissions are processed identically."""
    rng = random.Random(f"{country}-{seed}")
    pack = load_pack(country)
    districts = list(pack.districts())

    # Participation weight: what drives who actually files, in reality.
    weights = []
    for region, d in districts:
        dem = d["demographics"]
        reach = (0.50 * dem["smartphone_pen"] + 0.30 * dem["literacy"]
                 + 0.20 * dem["urbanization"]) / 100.0
        weights.append(max(0.02, reach ** 2.1) * (d["population"] ** 0.45))

    out = []
    for _ in range(target):
        region, d = rng.choices(districts, weights=weights, k=1)[0]
        if country == "ZA":
            lang = rng.choice(ZA_LANGS)
        else:
            lang = REGION_LANG.get(country, {}).get(region["code"], DEFAULT_LANG[country])
        if rng.random() < 0.13:              # code-mixing / migrant populations
            lang = "en" if country != "BR" else "pt"

        # Topic follows the district's real deficits.
        gaps = {s: max(1.0, pack.sectors[s]["benchmark"] - d["infra"][s])
                for s in pack.sectors}
        sector = rng.choices(list(gaps), weights=list(gaps.values()), k=1)[0]

        if rng.random() < 0.11:              # oblique, keyword-free
            pool = VAGUE.get(lang) or VAGUE["en"]
            text = rng.choice(pool)
        else:
            frames = FRAMES.get(lang) or FRAMES["en"]
            # Urgency skews toward the acute where the deficit is worst.
            sev = (pack.sectors[sector]["benchmark"] - d["infra"][sector]) / 100.0
            tier = rng.choices(
                ["critical", "high", "medium", "low"],
                weights=[0.04 + 0.30 * max(0, sev), 0.34, 0.40, 0.14], k=1)[0]
            frame = next(f for t, f in frames if t == tier)
            text = frame.format(issue=_issue_noun(sector, lang, rng),
                                n=rng.choice([12, 20, 35, 48, 60, 85, 120, 200, 350]))

        channel = rng.choices(
            ["voice", "whatsapp", "web", "sms", "ivr", "field_worker"],
            weights=[0.34, 0.24, 0.18, 0.10, 0.08, 0.06], k=1)[0]
        out.append({"text": text, "country": country, "district_code": d["code"],
                    "channel": channel, "language": None})
    return out
