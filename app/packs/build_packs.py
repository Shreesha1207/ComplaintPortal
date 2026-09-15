"""
Country Pack builder.

A Country Pack is the single pluggable unit that makes this platform portable
across BRICS nations. It declares a country's administrative hierarchy, languages,
map layout, demographic indicators, infrastructure indices and public
investment pipeline. Adding a country == adding one JSON file. No code change.

DATA PROVENANCE  (read this before quoting any number)
------------------------------------------------------
* Region/district NAMES and the administrative hierarchy are real.
* POPULATION figures are approximate, rounded public-domain census-era values.
* Every INDEX (literacy, urbanisation, poverty share, smartphone penetration,
  per-sector infrastructure index) and every INVESTMENT record is SYNTHETIC
  demo data, generated deterministically by this script. They are calibrated
  to plausible ranges so the prototype behaves realistically, but they are
  NOT official statistics and must not be cited as such.
* In production these fields are populated by the source adapters described in
  docs/ARCHITECTURE.md (national census APIs, NITI Aayog NDAP / data.gov.in,
  IBGE, Stats SA, budget portals). The schema is the contract; this generator
  is a stand-in for the feed.

MODELLING ASSUMPTION WORTH NAMING
---------------------------------
Investment allocation here is generated as a function of urbanisation and
literacy -- i.e. of political salience -- NOT of need. That is a deliberate,
documented model of a well-observed phenomenon: public capital tends to follow
organised voice. It is what produces the "blind spots" this platform is built to find.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

OUT_DIR = Path(__file__).parent

# --------------------------------------------------------------------------
# Global sector taxonomy. Shared across countries so cross-country comparison
# and reuse of classifier lexicons is possible -- a DPG requirement.
# --------------------------------------------------------------------------
SECTORS = [
    {"code": "water",       "name": "Water & Sanitation",     "icon": "droplet",  "benchmark": 80, "cost_weight": 0.84},
    {"code": "roads",       "name": "Roads & Connectivity",   "icon": "road",     "benchmark": 75, "cost_weight": 1.30},
    {"code": "health",      "name": "Healthcare",             "icon": "health",   "benchmark": 80, "cost_weight": 1.16},
    {"code": "education",   "name": "Education",              "icon": "school",   "benchmark": 82, "cost_weight": 0.72},
    {"code": "power",       "name": "Electricity & Energy",   "icon": "bolt",     "benchmark": 85, "cost_weight": 0.96},
    {"code": "transport",   "name": "Public Transport",       "icon": "bus",      "benchmark": 70, "cost_weight": 1.44},
    {"code": "housing",     "name": "Housing & Urban Dev",    "icon": "home",     "benchmark": 72, "cost_weight": 1.76},
    {"code": "digital",     "name": "Digital & Telecom",      "icon": "signal",   "benchmark": 78, "cost_weight": 0.48},
    {"code": "agriculture", "name": "Agriculture & Irrigation","icon": "sprout",  "benchmark": 70, "cost_weight": 0.78},
    {"code": "jobs",        "name": "Livelihoods & Jobs",     "icon": "briefcase","benchmark": 68, "cost_weight": 0.62},
]

# --------------------------------------------------------------------------
# INDIA
# (code, name, hex_col, hex_row, population, literacy%, urbanisation%,
#  poverty_share%, smartphone_penetration%, [(district, population, deprivation)])
# deprivation in 0..1 shifts that district's indices below its state mean.
# --------------------------------------------------------------------------
IN_REGIONS = [
    ("LA", "Ladakh", 5, 0, 290_000, 77.2, 22.9, 12.1, 46.0, [
        ("Leh", 160_000, 0.30), ("Kargil", 130_000, 0.55)]),
    ("JK", "Jammu & Kashmir", 4, 1, 13_600_000, 68.7, 27.2, 10.4, 52.0, [
        ("Srinagar", 1_270_000, 0.22), ("Jammu", 1_530_000, 0.20),
        ("Anantnag", 1_080_000, 0.48), ("Baramulla", 1_010_000, 0.50)]),
    ("HP", "Himachal Pradesh", 5, 2, 7_300_000, 82.8, 10.0, 8.1, 58.0, [
        ("Shimla", 810_000, 0.20), ("Kangra", 1_510_000, 0.34),
        ("Mandi", 999_000, 0.40), ("Solan", 580_000, 0.28)]),
    ("PB", "Punjab", 4, 2, 30_100_000, 75.8, 37.5, 8.3, 62.0, [
        ("Ludhiana", 3_500_000, 0.18), ("Amritsar", 2_490_000, 0.30),
        ("Jalandhar", 2_190_000, 0.26), ("Patiala", 1_900_000, 0.32),
        ("Bathinda", 1_390_000, 0.44)]),
    ("CH", "Chandigarh", 5, 3, 1_160_000, 86.4, 97.2, 4.2, 74.0, [
        ("Chandigarh", 1_160_000, 0.12)]),
    ("UK", "Uttarakhand", 6, 3, 11_400_000, 78.8, 30.2, 11.3, 55.0, [
        ("Dehradun", 1_900_000, 0.20), ("Haridwar", 2_060_000, 0.38),
        ("Nainital", 1_010_000, 0.34), ("Pithoragarh", 500_000, 0.62)]),
    ("HR", "Haryana", 4, 3, 28_900_000, 75.6, 34.9, 11.2, 63.0, [
        ("Gurugram", 1_800_000, 0.12), ("Faridabad", 2_020_000, 0.24),
        ("Hisar", 1_840_000, 0.42), ("Karnal", 1_570_000, 0.36),
        ("Nuh (Mewat)", 1_280_000, 0.86)]),
    ("DL", "Delhi", 5, 4, 20_600_000, 86.2, 97.5, 9.9, 76.0, [
        ("New Delhi", 260_000, 0.10), ("North East Delhi", 2_660_000, 0.52),
        ("South West Delhi", 2_370_000, 0.24), ("Shahdara", 1_900_000, 0.44)]),
    ("RJ", "Rajasthan", 3, 4, 79_500_000, 66.1, 24.9, 14.7, 51.0, [
        ("Jaipur", 7_100_000, 0.20), ("Jodhpur", 3_960_000, 0.38),
        ("Barmer", 2_850_000, 0.78), ("Udaipur", 3_400_000, 0.50),
        ("Bharatpur", 2_800_000, 0.54), ("Banswara", 1_960_000, 0.82)]),
    ("UP", "Uttar Pradesh", 6, 4, 231_500_000, 67.7, 22.3, 22.9, 46.0, [
        ("Lucknow", 4_990_000, 0.22), ("Kanpur Nagar", 4_950_000, 0.32),
        ("Varanasi", 4_010_000, 0.44), ("Gorakhpur", 4_800_000, 0.56),
        ("Bahraich", 3_800_000, 0.84), ("Shrawasti", 1_270_000, 0.92)]),
    ("SK", "Sikkim", 8, 4, 690_000, 81.4, 25.2, 8.2, 57.0, [
        ("Gangtok (East)", 300_000, 0.24), ("West Sikkim", 140_000, 0.56),
        ("North Sikkim", 45_000, 0.66)]),
    ("AR", "Arunachal Pradesh", 10, 4, 1_580_000, 65.4, 22.9, 17.3, 41.0, [
        ("Papum Pare (Itanagar)", 260_000, 0.34), ("Tawang", 55_000, 0.68),
        ("Changlang", 160_000, 0.74)]),
    ("GJ", "Gujarat", 2, 5, 70_100_000, 78.0, 42.6, 11.7, 61.0, [
        ("Ahmedabad", 8_200_000, 0.16), ("Surat", 7_100_000, 0.22),
        ("Rajkot", 4_200_000, 0.32), ("Kachchh", 2_300_000, 0.62),
        ("Dahod", 2_500_000, 0.86)]),
    ("MP", "Madhya Pradesh", 4, 5, 85_000_000, 69.3, 27.6, 20.6, 47.0, [
        ("Bhopal", 2_600_000, 0.22), ("Indore", 3_600_000, 0.20),
        ("Jabalpur", 2_800_000, 0.40), ("Jhabua", 1_200_000, 0.90),
        ("Rewa", 2_600_000, 0.64), ("Sheopur", 790_000, 0.88)]),
    ("BR", "Bihar", 7, 5, 128_500_000, 61.8, 11.3, 33.7, 38.0, [
        ("Patna", 6_300_000, 0.28), ("Muzaffarpur", 5_200_000, 0.62),
        ("Gaya", 4_900_000, 0.68), ("Araria", 3_200_000, 0.92),
        ("Sitamarhi", 3_700_000, 0.88), ("Purnia", 3_600_000, 0.80)]),
    ("AS", "Assam", 9, 5, 35_600_000, 72.2, 14.1, 19.7, 45.0, [
        ("Kamrup Metro (Guwahati)", 1_300_000, 0.22), ("Dhubri", 2_100_000, 0.86),
        ("Barpeta", 1_900_000, 0.74), ("Dibrugarh", 1_400_000, 0.46)]),
    ("NL", "Nagaland", 10, 5, 2_250_000, 79.6, 28.9, 15.4, 44.0, [
        ("Kohima", 300_000, 0.34), ("Dimapur", 420_000, 0.30), ("Mon", 280_000, 0.80)]),
    ("DD", "Dadra & Nagar Haveli and Daman & Diu", 2, 6, 620_000, 76.2, 50.1, 9.4, 58.0, [
        ("Dadra & Nagar Haveli", 390_000, 0.46), ("Daman", 190_000, 0.32),
        ("Diu", 55_000, 0.36)]),
    ("MH", "Maharashtra", 3, 6, 126_000_000, 82.3, 45.2, 14.9, 63.0, [
        ("Mumbai Suburban", 9_400_000, 0.18), ("Pune", 10_500_000, 0.16),
        ("Nagpur", 5_000_000, 0.28), ("Nandurbar", 1_800_000, 0.88),
        ("Gadchiroli", 1_200_000, 0.90), ("Chh. Sambhajinagar", 4_100_000, 0.48)]),
    ("CG", "Chhattisgarh", 5, 6, 30_100_000, 70.3, 23.2, 24.8, 43.0, [
        ("Raipur", 2_700_000, 0.26), ("Bilaspur", 2_100_000, 0.44),
        ("Korba", 1_300_000, 0.52), ("Bastar", 1_500_000, 0.86),
        ("Sukma", 300_000, 0.94)]),
    ("JH", "Jharkhand", 6, 6, 39_500_000, 66.4, 24.1, 27.1, 41.0, [
        ("Ranchi", 3_400_000, 0.30), ("Dhanbad", 3_000_000, 0.42),
        ("Pakur", 1_100_000, 0.90), ("West Singhbhum", 1_700_000, 0.82),
        ("Sahibganj", 1_300_000, 0.88)]),
    ("WB", "West Bengal", 8, 6, 99_600_000, 76.3, 31.9, 18.3, 52.0, [
        ("Kolkata", 4_600_000, 0.20), ("North 24 Parganas", 11_100_000, 0.36),
        ("Murshidabad", 8_100_000, 0.78), ("Purulia", 3_200_000, 0.84),
        ("Darjeeling", 1_900_000, 0.50)]),
    ("ML", "Meghalaya", 9, 6, 3_360_000, 74.4, 20.1, 20.9, 42.0, [
        ("East Khasi Hills (Shillong)", 880_000, 0.32),
        ("West Garo Hills", 650_000, 0.80), ("Ri Bhoi", 300_000, 0.70)]),
    ("MN", "Manipur", 10, 6, 3_200_000, 76.9, 29.2, 18.2, 46.0, [
        ("Imphal West", 520_000, 0.30), ("Churachandpur", 300_000, 0.76),
        ("Ukhrul", 190_000, 0.78)]),
    ("GA", "Goa", 3, 7, 1_590_000, 88.7, 62.2, 5.1, 71.0, [
        ("North Goa", 830_000, 0.18), ("South Goa", 760_000, 0.24)]),
    ("TG", "Telangana", 4, 7, 38_500_000, 72.8, 46.8, 12.9, 60.0, [
        ("Hyderabad", 4_400_000, 0.18), ("Rangareddy", 5_800_000, 0.30),
        ("Warangal", 1_600_000, 0.50), ("Adilabad", 1_000_000, 0.82),
        ("Nalgonda", 1_800_000, 0.60)]),
    ("OD", "Odisha", 6, 7, 46_400_000, 73.5, 17.0, 21.4, 44.0, [
        ("Khordha (Bhubaneswar)", 2_600_000, 0.26), ("Ganjam", 3_800_000, 0.60),
        ("Kalahandi", 1_700_000, 0.90), ("Koraput", 1_500_000, 0.88),
        ("Mayurbhanj", 2_700_000, 0.80)]),
    ("TR", "Tripura", 9, 7, 4_100_000, 87.2, 26.2, 16.8, 50.0, [
        ("West Tripura (Agartala)", 950_000, 0.30), ("Dhalai", 400_000, 0.82),
        ("South Tripura", 480_000, 0.64)]),
    ("MZ", "Mizoram", 10, 7, 1_240_000, 91.3, 52.1, 13.9, 49.0, [
        ("Aizawl", 420_000, 0.28), ("Lunglei", 170_000, 0.66),
        ("Lawngtlai", 120_000, 0.86)]),
    ("KA", "Karnataka", 3, 8, 67_600_000, 75.4, 38.6, 13.2, 64.0, [
        ("Bengaluru Urban", 12_800_000, 0.14), ("Mysuru", 3_200_000, 0.34),
        ("Belagavi", 5_100_000, 0.56), ("Kalaburagi", 2_900_000, 0.80),
        ("Raichur", 2_100_000, 0.86), ("Yadgir", 1_400_000, 0.92)]),
    ("AP", "Andhra Pradesh", 5, 8, 53_900_000, 67.4, 29.6, 14.8, 56.0, [
        ("Visakhapatnam", 4_600_000, 0.26), ("Guntur", 4_900_000, 0.42),
        ("Kurnool", 4_300_000, 0.70), ("Anantapur", 4_200_000, 0.76),
        ("Srikakulam", 2_800_000, 0.78)]),
    ("KL", "Kerala", 3, 9, 35_700_000, 94.0, 47.7, 7.1, 72.0, [
        ("Thiruvananthapuram", 3_500_000, 0.20), ("Ernakulam", 3_400_000, 0.16),
        ("Malappuram", 4_500_000, 0.40), ("Wayanad", 850_000, 0.58),
        ("Idukki", 1_100_000, 0.54)]),
    ("TN", "Tamil Nadu", 4, 9, 77_800_000, 80.1, 48.4, 11.3, 67.0, [
        ("Chennai", 7_100_000, 0.16), ("Coimbatore", 3_800_000, 0.22),
        ("Madurai", 3_300_000, 0.36), ("Tiruvallur", 4_100_000, 0.40),
        ("Ramanathapuram", 1_400_000, 0.72), ("Dharmapuri", 1_600_000, 0.74)]),
    ("PY", "Puducherry", 5, 9, 1_400_000, 85.8, 68.3, 6.3, 69.0, [
        ("Puducherry", 1_100_000, 0.22), ("Karaikal", 230_000, 0.42)]),
    ("LD", "Lakshadweep", 1, 9, 68_000, 91.8, 78.1, 5.9, 55.0, [
        ("Lakshadweep", 68_000, 0.40)]),
    ("AN", "Andaman & Nicobar", 8, 9, 420_000, 86.6, 37.7, 7.4, 57.0, [
        ("South Andaman", 240_000, 0.32), ("Nicobar", 40_000, 0.76)]),
]

IN_LANGS = [
    ("hi", "Hindi", "हिन्दी", "hi-IN"),
    ("bn", "Bengali", "বাংলা", "bn-IN"),
    ("mr", "Marathi", "मराठी", "mr-IN"),
    ("te", "Telugu", "తెలుగు", "te-IN"),
    ("ta", "Tamil", "தமிழ்", "ta-IN"),
    ("gu", "Gujarati", "ગુજરાતી", "gu-IN"),
    ("kn", "Kannada", "ಕನ್ನಡ", "kn-IN"),
    ("ml", "Malayalam", "മലയാളം", "ml-IN"),
    ("or", "Odia", "ଓଡିଆ", "or-IN"),
    ("pa", "Punjabi", "ਪੰਜਾਬੀ", "pa-IN"),
    ("as", "Assamese", "অসমীয়া", "as-IN"),
    ("ur", "Urdu", "اردو", "ur-IN"),
    ("en", "English", "English", "en-IN"),
]

# --------------------------------------------------------------------------
# BRAZIL
# --------------------------------------------------------------------------
BR_REGIONS = [
    ("RR", "Roraima", 3, 0, 650_000, 90.2, 76.1, 22.4, 61.0, [("Boa Vista", 420_000, 0.34), ("Rorainopolis", 30_000, 0.78)]),
    ("AP", "Amapa", 5, 0, 860_000, 92.1, 89.8, 25.1, 62.0, [("Macapa", 510_000, 0.38), ("Santana", 120_000, 0.66)]),
    ("AM", "Amazonas", 2, 1, 4_270_000, 91.0, 79.1, 27.9, 59.0, [("Manaus", 2_250_000, 0.30), ("Tabatinga", 70_000, 0.84)]),
    ("PA", "Para", 4, 1, 8_780_000, 88.9, 68.5, 26.8, 57.0, [("Belem", 1_500_000, 0.34), ("Maraba", 280_000, 0.70), ("Altamira", 120_000, 0.80)]),
    ("MA", "Maranhao", 6, 1, 7_150_000, 83.2, 63.1, 33.4, 50.0, [("Sao Luis", 1_110_000, 0.36), ("Imperatriz", 260_000, 0.62), ("Codo", 120_000, 0.88)]),
    ("CE", "Ceara", 8, 1, 9_240_000, 87.0, 75.1, 28.6, 58.0, [("Fortaleza", 2_700_000, 0.30), ("Sobral", 210_000, 0.62), ("Crateus", 75_000, 0.82)]),
    ("RN", "Rio Grande do Norte", 9, 1, 3_540_000, 86.4, 77.8, 26.1, 60.0, [("Natal", 890_000, 0.32), ("Mossoro", 300_000, 0.58)]),
    ("AC", "Acre", 1, 2, 830_000, 87.1, 72.6, 28.4, 55.0, [("Rio Branco", 410_000, 0.40), ("Cruzeiro do Sul", 90_000, 0.82)]),
    ("RO", "Rondonia", 2, 2, 1_800_000, 91.3, 73.6, 21.3, 60.0, [("Porto Velho", 540_000, 0.40), ("Ji-Parana", 130_000, 0.62)]),
    ("TO", "Tocantins", 5, 2, 1_610_000, 88.7, 78.8, 24.2, 58.0, [("Palmas", 310_000, 0.34), ("Araguaina", 180_000, 0.60)]),
    ("PI", "Piaui", 7, 2, 3_280_000, 82.6, 65.8, 31.5, 52.0, [("Teresina", 870_000, 0.38), ("Parnaiba", 150_000, 0.68)]),
    ("PB", "Paraiba", 9, 2, 4_060_000, 83.9, 75.4, 28.9, 57.0, [("Joao Pessoa", 830_000, 0.32), ("Campina Grande", 410_000, 0.48)]),
    ("PE", "Pernambuco", 9, 3, 9_620_000, 85.6, 80.2, 29.4, 60.0, [("Recife", 1_650_000, 0.30), ("Caruaru", 370_000, 0.56), ("Petrolina", 360_000, 0.68)]),
    ("AL", "Alagoas", 9, 4, 3_360_000, 81.9, 73.6, 33.1, 54.0, [("Maceio", 1_030_000, 0.36), ("Arapiraca", 240_000, 0.70)]),
    ("SE", "Sergipe", 9, 5, 2_320_000, 85.2, 73.5, 29.2, 57.0, [("Aracaju", 670_000, 0.32), ("Nossa Senhora do Socorro", 180_000, 0.64)]),
    ("MT", "Mato Grosso", 4, 3, 3_830_000, 91.4, 81.8, 18.7, 62.0, [("Cuiaba", 650_000, 0.34), ("Sinop", 190_000, 0.52)]),
    ("BA", "Bahia", 7, 4, 14_990_000, 84.7, 72.1, 28.3, 58.0, [("Salvador", 2_420_000, 0.30), ("Feira de Santana", 620_000, 0.52), ("Juazeiro", 220_000, 0.76)]),
    ("GO", "Goias", 5, 4, 7_210_000, 91.5, 90.3, 17.4, 64.0, [("Goiania", 1_540_000, 0.26), ("Aparecida de Goiania", 600_000, 0.46)]),
    ("DF", "Distrito Federal", 6, 4, 2_920_000, 95.4, 96.6, 12.1, 76.0, [("Brasilia", 2_920_000, 0.20)]),
    ("MS", "Mato Grosso do Sul", 4, 5, 2_760_000, 92.0, 85.6, 17.1, 63.0, [("Campo Grande", 900_000, 0.30), ("Dourados", 230_000, 0.54)]),
    ("MG", "Minas Gerais", 6, 5, 20_540_000, 92.3, 85.3, 16.4, 66.0, [("Belo Horizonte", 2_320_000, 0.24), ("Uberlandia", 700_000, 0.34), ("Teofilo Otoni", 140_000, 0.74)]),
    ("ES", "Espirito Santo", 8, 5, 4_060_000, 92.5, 83.4, 15.3, 67.0, [("Vitoria", 360_000, 0.24), ("Serra", 520_000, 0.48)]),
    ("SP", "Sao Paulo", 6, 6, 44_420_000, 95.2, 96.2, 11.2, 74.0, [("Sao Paulo", 11_450_000, 0.20), ("Campinas", 1_140_000, 0.28), ("Guarulhos", 1_290_000, 0.46)]),
    ("RJ", "Rio de Janeiro", 7, 6, 16_050_000, 94.4, 96.7, 14.8, 72.0, [("Rio de Janeiro", 6_210_000, 0.26), ("Duque de Caxias", 920_000, 0.62), ("Nova Iguacu", 820_000, 0.58)]),
    ("PR", "Parana", 5, 7, 11_440_000, 93.6, 85.3, 13.2, 68.0, [("Curitiba", 1_770_000, 0.22), ("Londrina", 560_000, 0.36)]),
    ("SC", "Santa Catarina", 6, 7, 7_610_000, 95.0, 84.0, 9.8, 71.0, [("Florianopolis", 500_000, 0.22), ("Joinville", 590_000, 0.32)]),
    ("RS", "Rio Grande do Sul", 5, 8, 11_230_000, 94.2, 85.1, 12.4, 69.0, [("Porto Alegre", 1_330_000, 0.24), ("Caxias do Sul", 500_000, 0.34)]),
]

BR_LANGS = [
    ("pt", "Portuguese", "Português", "pt-BR"),
    ("es", "Spanish", "Español", "es-419"),
    ("en", "English", "English", "en-US"),
]

# --------------------------------------------------------------------------
# SOUTH AFRICA
# --------------------------------------------------------------------------
ZA_REGIONS = [
    ("LP", "Limpopo", 4, 0, 5_930_000, 84.2, 15.8, 29.4, 57.0, [("Polokwane", 830_000, 0.42), ("Vhembe", 1_400_000, 0.78), ("Sekhukhune", 1_190_000, 0.82)]),
    ("MP", "Mpumalanga", 5, 1, 4_740_000, 85.1, 41.2, 26.1, 60.0, [("Mbombela", 700_000, 0.40), ("Gert Sibande", 1_140_000, 0.66)]),
    ("GP", "Gauteng", 4, 1, 16_100_000, 95.4, 97.0, 14.2, 78.0, [("Johannesburg", 5_640_000, 0.26), ("Tshwane", 3_820_000, 0.30), ("Ekurhuleni", 3_780_000, 0.44)]),
    ("NW", "North West", 3, 1, 4_110_000, 84.8, 39.4, 27.8, 58.0, [("Rustenburg", 630_000, 0.46), ("Bojanala", 1_660_000, 0.66)]),
    ("FS", "Free State", 4, 2, 2_950_000, 88.0, 76.4, 25.3, 62.0, [("Mangaung", 790_000, 0.38), ("Thabo Mofutsanyana", 780_000, 0.72)]),
    ("KZ", "KwaZulu-Natal", 5, 2, 11_530_000, 87.1, 46.5, 27.6, 63.0, [("eThekwini (Durban)", 3_900_000, 0.32), ("uMkhanyakude", 690_000, 0.86), ("Zululand", 900_000, 0.80)]),
    ("NC", "Northern Cape", 2, 2, 1_300_000, 84.9, 66.1, 26.5, 57.0, [("Sol Plaatje", 260_000, 0.44), ("Namakwa", 120_000, 0.70)]),
    ("EC", "Eastern Cape", 4, 3, 7_230_000, 84.0, 40.1, 32.2, 56.0, [("Nelson Mandela Bay", 1_270_000, 0.36), ("OR Tambo", 1_500_000, 0.88), ("Alfred Nzo", 900_000, 0.90)]),
    ("WC", "Western Cape", 2, 3, 7_430_000, 94.3, 92.1, 16.1, 74.0, [("Cape Town", 4_770_000, 0.28), ("Cape Winelands", 950_000, 0.50)]),
]

ZA_LANGS = [
    ("en", "English", "English", "en-ZA"),
    ("zu", "isiZulu", "isiZulu", "zu-ZA"),
    ("xh", "isiXhosa", "isiXhosa", "xh-ZA"),
    ("af", "Afrikaans", "Afrikaans", "af-ZA"),
    ("st", "Sesotho", "Sesotho", "st-ZA"),
]


def _rand(*key) -> float:
    """Deterministic pseudo-random in [0,1) from a string key. Stable across runs
    and across machines -- important so the demo is reproducible."""
    h = hashlib.sha256("|".join(str(k) for k in key).encode()).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def build_country(code, name, currency, admin_levels, regions, langs, map_dims, notes):
    """Derive indices + investment pipeline from the structural facts above."""
    # Country means, used to z-score each region against its own country.
    lit_m = sum(r[5] for r in regions) / len(regions)
    urb_m = sum(r[6] for r in regions) / len(regions)
    pov_m = sum(r[7] for r in regions) / len(regions)

    out_regions, investments = [], []
    inv_seq = 0

    for (rcode, rname, hx, hy, pop, lit, urb, pov, phone, districts) in regions:
        # A region's "development headroom" -- higher = better served already.
        dev = (0.40 * (lit - lit_m) / 12.0
               + 0.40 * (urb - urb_m) / 25.0
               + 0.20 * (pov_m - pov) / 8.0)

        r_infra = {}
        for s in SECTORS:
            base = s["benchmark"] - 12.0
            noise = (_rand(code, rcode, s["code"]) - 0.5) * 16.0
            r_infra[s["code"]] = round(_clamp(base + dev * 17.0 + noise, 6.0, 96.0), 1)

        d_out = []
        for (dname, dpop, depriv) in districts:
            # District index = state index pulled down by local deprivation.
            d_infra = {}
            for s in SECTORS:
                shift = (0.5 - depriv) * 34.0
                noise = (_rand(code, rcode, dname, s["code"]) - 0.5) * 9.0
                d_infra[s["code"]] = round(_clamp(r_infra[s["code"]] + shift + noise, 4.0, 97.0), 1)

            d_out.append({
                "code": f"{rcode}-{abs(hash(dname)) % 9973:04d}",
                "name": dname,
                "population": dpop,
                "deprivation": depriv,
                "demographics": {
                    "literacy": round(_clamp(lit + (0.5 - depriv) * 20.0, 25.0, 99.0), 1),
                    "urbanization": round(_clamp(urb + (0.5 - depriv) * 34.0, 2.0, 100.0), 1),
                    "poverty_share": round(_clamp(pov + (depriv - 0.5) * 24.0, 1.5, 62.0), 1),
                    "smartphone_pen": round(_clamp(phone + (0.5 - depriv) * 30.0, 8.0, 93.0), 1),
                },
                "infra": d_infra,
            })

        out_regions.append({
            "code": rcode, "name": rname, "hex": [hx, hy], "population": pop,
            "demographics": {"literacy": lit, "urbanization": urb,
                             "poverty_share": pov, "smartphone_pen": phone},
            "infra": r_infra,
            "districts": d_out,
        })

        # ---- Investment pipeline -------------------------------------------
        # Modelled as a function of political salience (urbanisation, literacy),
        # NOT of need. See module docstring.
        salience = _clamp(0.30 + 0.5 * (urb / 100.0) + 0.3 * (lit / 100.0), 0.0, 1.0)
        for s in SECTORS:
            for d in d_out:
                p = salience * (1.0 - 0.70 * d["deprivation"])
                if _rand(code, "inv", rcode, d["name"], s["code"]) < p * 0.42:
                    inv_seq += 1
                    # Project SIZE also tracks salience, not need: better-off
                    # districts attract both more projects and larger ones.
                    size_factor = 0.5 + 1.0 * (1.0 - d["deprivation"])
                    per_capita = (200 + _rand(code, "amt", rcode, d["name"], s["code"]) * 800) * size_factor
                    budget = round(d["population"] * per_capita / currency["unit_value"], 2)
                    roll = _rand(code, "st", rcode, d["name"], s["code"])
                    status = "ongoing" if roll < 0.42 else ("planned" if roll < 0.88 else "completed")
                    investments.append({
                        "id": f"{code}-INV-{inv_seq:05d}",
                        "region": rcode, "district": d["code"], "sector": s["code"],
                        "title": f"{s['name']} programme — {d['name']}",
                        "budget": budget, "status": status,
                        "start_year": 2023 + int(_rand(code, "yr", rcode, d["name"], s["code"]) * 4),
                        "source": "synthetic-demo",
                    })

    return {
        "code": code, "name": name, "currency": currency,
        "admin_levels": admin_levels,
        "pivot_language": "en",
        "languages": [{"code": c, "name": n, "native": nat, "bcp47": b} for (c, n, nat, b) in langs],
        "sectors": SECTORS,
        "map": {"type": "hex-cartogram", "cols": map_dims[0], "rows": map_dims[1]},
        "data_notice": notes,
        "regions": out_regions,
        "investments": investments,
    }


def main():
    packs = [
        build_country(
            "IN", "India",
            {"code": "INR", "symbol": "₹", "unit": "crore", "unit_value": 10_000_000,
             "capex_per_capita": 5000},
            ["Country", "State / UT", "District"], IN_REGIONS, IN_LANGS, (12, 11),
            "Region and district names and approximate populations are real. All indices "
            "and investment records are synthetic demo data generated by build_packs.py.",
        ),
        build_country(
            "BR", "Brazil",
            {"code": "BRL", "symbol": "R$", "unit": "million", "unit_value": 1_000_000,
             "capex_per_capita": 900},
            ["Country", "State", "Municipality"], BR_REGIONS, BR_LANGS, (11, 9),
            "State and municipality names and approximate populations are real. All indices "
            "and investment records are synthetic demo data generated by build_packs.py.",
        ),
        build_country(
            "ZA", "South Africa",
            {"code": "ZAR", "symbol": "R", "unit": "million", "unit_value": 1_000_000,
             "capex_per_capita": 2500},
            ["Country", "Province", "District Municipality"], ZA_REGIONS, ZA_LANGS, (7, 5),
            "Province and district-municipality names and approximate populations are real. "
            "All indices and investment records are synthetic demo data generated by build_packs.py.",
        ),
    ]
    for p in packs:
        path = OUT_DIR / f"{p['code']}.json"
        path.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        nd = sum(len(r["districts"]) for r in p["regions"])
        print(f"{p['code']}: {len(p['regions'])} regions, {nd} districts, "
              f"{len(p['investments'])} investments -> {path.name}")


if __name__ == "__main__":
    main()
