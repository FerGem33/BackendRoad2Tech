"""Static, hand-curated catalog definitions (zones, categories, products, suppliers, store anchors).

Everything here is synthetic. Product brands are real consumer brands sold in Mexican convenience
stores; retail prices are approximate 2024-2026 shelf prices, not data from any company.
Suppliers are fictional. Store coordinates are approximate points in Saltillo neighborhoods.
"""

# --------------------------------------------------------------------------------------------
# Zones
# --------------------------------------------------------------------------------------------
# zone_type -> (description, peak_hours, notes)
ZONES = {
    "residential": ("Housing neighborhoods; neighbors buy daily groceries, drinks and party supplies.",
                    "07:00-09:00|18:00-22:00", "Strongest reaction to parties, matches and holidays."),
    "business": ("Offices and downtown commerce; weekday breakfast and lunch traffic.",
                 "07:00-09:30|13:00-15:30", "Sharp weekend and holiday drop."),
    "school": ("Next to K-12 schools (SEP calendar); students buy candy, snacks and juice.",
               "07:00-08:00|12:30-14:30", "Sharp drop on weekends, SEP vacations and teacher-council days; minimal alcoholic beverages/cigarettes."),
    "college": ("Next to universities (UAdeC, Tec de Monterrey); young adults buy alcoholic beverages, energy drinks, coffee.",
                "08:00-10:00|13:00-15:00|20:00-23:00", "University calendar; alcoholic beverages peak Thursday-Saturday."),
    "highway": ("On Highway 57, Periferico Luis Echeverria or the Monterrey/Zacatecas exits; travelers and truckers.",
                "06:00-10:00|16:00-20:00", "Snacks, water, soft drinks, energy drinks and coffee lead every day; long weekends increase traffic."),
    "industrial": ("Industrial parks and the corridor toward Derramadero/Ramos Arizpe; shift workers.",
                   "05:30-07:30|13:30-15:00|21:30-22:30", "Weekday shift peaks; Saturday half shifts; low Sunday."),
    "shopping_center": ("Inside or next to malls; leisure shoppers.",
                        "12:00-15:00|17:00-21:00", "Weekend peak; Buen Fin boost."),
    "hospital": ("Next to hospitals and clinics; visitors, patients and staff 24/7.",
                 "07:00-10:00|19:00-22:00", "Flat week; pharmacy, coffee, flowers and baby products over-index."),
}

# Weekday multipliers per zone, Monday..Sunday.
WEEKDAY_MULT = {
    "residential":     [0.95, 0.95, 0.97, 1.00, 1.10, 1.18, 1.05],
    "business":        [1.12, 1.15, 1.15, 1.15, 1.12, 0.55, 0.40],
    "school":          [1.20, 1.20, 1.20, 1.20, 1.15, 0.45, 0.35],
    "college":         [1.10, 1.10, 1.10, 1.15, 1.10, 0.70, 0.55],
    "highway":         [0.97, 0.95, 0.95, 1.00, 1.15, 1.10, 1.15],
    "industrial":      [1.12, 1.12, 1.12, 1.12, 1.10, 0.80, 0.50],
    "shopping_center": [0.90, 0.88, 0.90, 0.95, 1.10, 1.30, 1.25],
    "hospital":        [1.02, 1.02, 1.02, 1.02, 1.00, 0.95, 0.95],
}

# --------------------------------------------------------------------------------------------
# Categories: key -> (category_id, name, is_perishable, typical_shelf_life_days, requires_refrigeration)
# --------------------------------------------------------------------------------------------
CATEGORIES = {
    "bev":  ("C01", "Non-Alcoholic Beverages", 0, 180, 0),
    "alc":  ("C02", "Alcoholic Beverages", 0, 180, 0),
    "snk":  ("C03", "Snacks and Chips", 0, 120, 0),
    "cdy":  ("C04", "Candy and Chocolate", 0, 240, 0),
    "prep": ("C05", "Prepared Foods", 1, 6, 1),
    "cof":  ("C06", "Coffee and Hot Beverages", 0, 180, 0),
    "dai":  ("C07", "Dairy and Refrigerated Products", 1, 18, 1),
    "bak":  ("C08", "Bakery and Cookies", 1, 10, 0),
    "gro":  ("C09", "Groceries", 0, 365, 0),
    "frz":  ("C10", "Frozen Foods and Ice Cream", 0, 180, 1),
    "ice":  ("C11", "Ice", 0, 365, 1),
    "cig":  ("C12", "Cigarettes", 0, 365, 0),
    "hyg":  ("C13", "Personal Hygiene", 0, 730, 0),
    "cln":  ("C14", "Cleaning Products", 0, 730, 0),
    "phm":  ("C15", "Pharmacy (OTC)", 0, 730, 0),
    "pet":  ("C16", "Pet Supplies", 0, 540, 0),
    "bby":  ("C17", "Baby Products", 0, 730, 0),
    "fru":  ("C18", "Fruits and Vegetables", 1, 5, 1),
    "dsp":  ("C19", "Disposables and Party Supplies", 0, 1095, 0),
    "flw":  ("C20", "Flowers and Gifts", 1, 7, 0),
}

# --------------------------------------------------------------------------------------------
# Products (exactly 120): (name, category_key, brand, unit, shelf_life_days, retail_price,
#                          weather_sensitivity, base_daily_demand, tags, is_seasonal)
# base_daily_demand = expected units/day in an average medium store on a neutral day.
# Tags drive event/zone rules in build_demand.py and are not exported.
# --------------------------------------------------------------------------------------------
PRODUCTS = [
    # Non-Alcoholic Beverages (21)
    ("Coca-Cola 600 ml", "bev", "Coca-Cola", "bottle", 180, 20.00, "hot", 30.0, {"soft_drink"}, 0),
    ("Coca-Cola 2.5 L", "bev", "Coca-Cola", "bottle", 180, 45.00, "hot", 9.0, {"soft_drink_large"}, 0),
    ("Coca-Cola 3 L", "bev", "Coca-Cola", "bottle", 180, 52.00, "hot", 6.0, {"soft_drink_large"}, 0),
    ("Coca-Cola Zero Sugar 600 ml", "bev", "Coca-Cola", "bottle", 180, 20.00, "hot", 10.0, {"soft_drink"}, 0),
    ("Pepsi 600 ml", "bev", "Pepsi", "bottle", 180, 18.00, "hot", 8.0, {"soft_drink"}, 0),
    ("Pepsi 3 L", "bev", "Pepsi", "bottle", 180, 42.00, "hot", 4.5, {"soft_drink_large"}, 0),
    ("Sprite 600 ml", "bev", "Sprite", "bottle", 180, 19.00, "hot", 7.0, {"soft_drink"}, 0),
    ("Fanta Orange 600 ml", "bev", "Fanta", "bottle", 180, 19.00, "hot", 6.0, {"soft_drink"}, 0),
    ("Squirt Grapefruit 600 ml", "bev", "Squirt", "bottle", 180, 18.00, "hot", 5.0, {"soft_drink"}, 0),
    ("Ciel Purified Water 600 ml", "bev", "Ciel", "bottle", 365, 12.00, "hot", 18.0, {"water"}, 0),
    ("Ciel Purified Water 1 L", "bev", "Ciel", "bottle", 365, 15.00, "hot", 14.0, {"water"}, 0),
    ("Bonafont Water 1.5 L", "bev", "Bonafont", "bottle", 365, 17.50, "hot", 8.0, {"water"}, 0),
    ("Electrolit Strawberry 625 ml", "bev", "Electrolit", "bottle", 365, 27.00, "hot", 8.0, {"hydration", "hangover"}, 0),
    ("Gatorade Lemon-Lime 1 L", "bev", "Gatorade", "bottle", 270, 30.00, "hot", 6.0, {"hydration"}, 0),
    ("Red Bull Energy Drink 250 ml", "bev", "Red Bull", "can", 365, 39.00, "neutral", 6.0, {"energy"}, 0),
    ("Monster Energy 473 ml", "bev", "Monster", "can", 365, 42.00, "neutral", 8.0, {"energy"}, 0),
    ("Volt Energy Drink 473 ml", "bev", "Volt", "can", 365, 22.00, "neutral", 6.0, {"energy"}, 0),
    ("Jumex Mango Nectar 335 ml", "bev", "Jumex", "can", 270, 18.00, "neutral", 7.0, {"juice"}, 0),
    ("Del Valle Orange Juice 1 L", "bev", "Del Valle", "carton", 120, 32.00, "neutral", 4.0, {"juice"}, 0),
    ("Boing Mango 500 ml", "bev", "Boing", "carton", 180, 16.00, "neutral", 6.0, {"juice"}, 0),
    ("Arizona Green Tea 680 ml", "bev", "Arizona", "can", 270, 22.00, "hot", 5.0, {"tea"}, 0),
    # Alcoholic Beverages (12)
    ("Tecate Light 12-Pack 355 ml Cans", "alc", "Tecate", "pack", 180, 230.00, "hot", 4.0, {"beer"}, 0),
    ("Tecate Original 473 ml Can", "alc", "Tecate", "can", 180, 25.00, "hot", 10.0, {"beer"}, 0),
    ("Carta Blanca 1.2 L Bottle", "alc", "Carta Blanca", "bottle", 180, 42.00, "hot", 6.0, {"beer"}, 0),
    ("Indio 1.2 L Bottle", "alc", "Indio", "bottle", 180, 45.00, "hot", 6.0, {"beer"}, 0),
    ("Corona Extra 355 ml Bottle", "alc", "Corona", "bottle", 180, 22.00, "hot", 12.0, {"beer"}, 0),
    ("Corona Familiar 940 ml Bottle", "alc", "Corona", "bottle", 180, 38.00, "hot", 7.0, {"beer"}, 0),
    ("Victoria 355 ml Can", "alc", "Victoria", "can", 180, 22.00, "hot", 9.0, {"beer"}, 0),
    ("Modelo Especial 355 ml Can", "alc", "Modelo", "can", 180, 26.00, "hot", 8.0, {"beer"}, 0),
    ("Michelob Ultra 355 ml Can", "alc", "Michelob", "can", 180, 28.00, "hot", 5.0, {"beer"}, 0),
    ("Bud Light 12-Pack 355 ml Cans", "alc", "Bud Light", "pack", 180, 215.00, "hot", 2.0, {"beer"}, 0),
    ("Jose Cuervo Especial Tequila 990 ml", "alc", "Jose Cuervo", "bottle", 1095, 330.00, "neutral", 0.6, {"spirits"}, 0),
    ("Bacardi Blanco Rum 980 ml", "alc", "Bacardi", "bottle", 1095, 280.00, "neutral", 0.5, {"spirits"}, 0),
    # Snacks and Chips (13)
    ("Sabritas Original Potato Chips 45 g", "snk", "Sabritas", "bag", 120, 21.00, "neutral", 10.0, {"chips"}, 0),
    ("Doritos Nacho 58 g", "snk", "Doritos", "bag", 120, 22.00, "neutral", 10.0, {"chips"}, 0),
    ("Cheetos Flamin' Hot 52 g", "snk", "Cheetos", "bag", 120, 19.00, "neutral", 8.0, {"chips"}, 0),
    ("Ruffles Cheese 50 g", "snk", "Ruffles", "bag", 120, 22.00, "neutral", 7.0, {"chips"}, 0),
    ("Takis Fuego 70 g", "snk", "Takis", "bag", 120, 21.00, "neutral", 10.0, {"chips"}, 0),
    ("Tostitos Salsa Verde 62 g", "snk", "Tostitos", "bag", 120, 22.00, "neutral", 6.0, {"chips"}, 0),
    ("Sabritas Original Potato Chips 150 g", "snk", "Sabritas", "bag", 120, 58.00, "neutral", 3.0, {"chips", "party_snack"}, 0),
    ("Doritos Nacho Party Size 230 g", "snk", "Doritos", "bag", 120, 72.00, "neutral", 2.0, {"chips", "party_snack"}, 0),
    ("Churrumais 63 g", "snk", "Churrumais", "bag", 120, 14.00, "neutral", 6.0, {"chips"}, 0),
    ("Mafer Japanese-Style Peanuts 100 g", "snk", "Mafer", "bag", 240, 22.00, "neutral", 4.0, {"nuts"}, 0),
    ("Barcel Chip's Jalapeno 46 g", "snk", "Barcel", "bag", 120, 20.00, "neutral", 6.0, {"chips"}, 0),
    ("Pringles Original 124 g", "snk", "Pringles", "can", 365, 52.00, "neutral", 3.0, {"chips", "party_snack"}, 0),
    ("Act II Butter Microwave Popcorn 80 g", "snk", "Act II", "bag", 240, 18.00, "neutral", 2.0, {"popcorn"}, 0),
    # Candy and Chocolate (12)
    ("Snickers Bar 52.7 g", "cdy", "Snickers", "piece", 270, 20.00, "neutral", 6.0, {"chocolate"}, 0),
    ("Carlos V Chocolate Bar 18 g", "cdy", "Carlos V", "piece", 270, 9.00, "neutral", 7.0, {"chocolate"}, 0),
    ("Kinder Delice 39 g", "cdy", "Kinder", "piece", 180, 16.00, "neutral", 5.0, {"chocolate"}, 0),
    ("Ferrero Rocher 3 pcs", "cdy", "Ferrero", "box", 240, 38.00, "neutral", 2.0, {"chocolate", "gift_chocolate"}, 0),
    ("Hershey's Milk Chocolate 40 g", "cdy", "Hershey's", "piece", 365, 20.00, "neutral", 4.0, {"chocolate"}, 0),
    ("Pulparindo Tamarind Candy 14 g", "cdy", "De la Rosa", "piece", 365, 4.00, "neutral", 8.0, {"candy"}, 0),
    ("De la Rosa Peanut Marzipan 28 g", "cdy", "De la Rosa", "piece", 365, 6.00, "neutral", 8.0, {"candy"}, 0),
    ("Skwinkles Rellenos 26 g", "cdy", "Skwinkles", "bag", 365, 14.00, "neutral", 6.0, {"candy"}, 0),
    ("Trident Spearmint Gum 18 pcs", "cdy", "Trident", "pack", 365, 18.00, "neutral", 6.0, {"gum"}, 0),
    ("Halls Menthol Drops 25 g", "cdy", "Halls", "pack", 540, 15.00, "cold", 5.0, {"candy"}, 0),
    ("Lucas Muecas Chamoy Lollipop 24 g", "cdy", "Lucas", "piece", 365, 12.00, "neutral", 5.0, {"candy"}, 0),
    ("Ricolino Bubulubu 35 g", "cdy", "Ricolino", "piece", 270, 13.00, "neutral", 5.0, {"chocolate"}, 0),
    # Prepared Foods (10)
    ("Ham and Cheese Sandwich", "prep", "Private Label", "piece", 6, 39.00, "neutral", 6.0, set(), 0),
    ("Chipotle Chicken Torta", "prep", "Private Label", "piece", 5, 49.00, "neutral", 4.0, set(), 0),
    ("Bean and Cheese Burrito", "prep", "Private Label", "piece", 7, 25.00, "neutral", 6.0, set(), 0),
    ("Machaca Burrito", "prep", "Private Label", "piece", 7, 32.00, "neutral", 5.0, set(), 0),
    ("Jumbo Hot Dog", "prep", "Private Label", "piece", 6, 25.00, "neutral", 8.0, set(), 0),
    ("Pepperoni Pizza Slice", "prep", "Private Label", "piece", 5, 35.00, "neutral", 6.0, set(), 0),
    ("Chicken Wrap", "prep", "Private Label", "piece", 6, 45.00, "neutral", 3.0, set(), 0),
    ("Tamales 3-Pack", "prep", "Private Label", "pack", 6, 45.00, "cold", 2.0, {"tamales"}, 0),
    ("Chilaquiles Bowl", "prep", "Private Label", "piece", 4, 49.00, "cold", 3.0, set(), 0),
    ("Classic Hamburger", "prep", "Private Label", "piece", 5, 49.00, "neutral", 3.0, set(), 0),
    # Coffee and Hot Beverages (6)
    ("Andatti Americano Coffee Large", "cof", "Andatti", "cup", 180, 29.00, "cold", 20.0, {"hot_coffee"}, 0),
    ("Andatti Cappuccino Medium", "cof", "Andatti", "cup", 180, 35.00, "cold", 10.0, {"hot_coffee"}, 0),
    ("Andatti Latte Large", "cof", "Andatti", "cup", 180, 42.00, "cold", 7.0, {"hot_coffee"}, 0),
    ("Andatti Hot Chocolate Medium", "cof", "Andatti", "cup", 180, 32.00, "cold", 5.0, {"hot_chocolate"}, 0),
    ("Andatti Iced Coffee Frappe Medium", "cof", "Andatti", "cup", 180, 45.00, "hot", 6.0, {"iced_coffee"}, 0),
    ("Abuelita Chocolate Tablet 90 g", "cof", "Abuelita", "piece", 365, 25.00, "cold", 2.0, {"hot_chocolate"}, 0),
    # Dairy and Refrigerated Products (7)
    ("Lala Whole Milk 1 L", "dai", "Lala", "carton", 12, 28.00, "neutral", 10.0, {"milk"}, 0),
    ("Lala Light Milk 1 L", "dai", "Lala", "carton", 12, 30.00, "neutral", 5.0, {"milk"}, 0),
    ("Yoplait Strawberry Drinkable Yogurt 242 g", "dai", "Yoplait", "bottle", 25, 18.00, "neutral", 6.0, set(), 0),
    ("Danonino Strawberry 4-Pack", "dai", "Danone", "pack", 25, 32.00, "neutral", 3.0, set(), 0),
    ("Santa Clara Chocolate Milk 330 ml", "dai", "Santa Clara", "bottle", 20, 22.00, "neutral", 5.0, {"chocolate_milk"}, 0),
    ("FUD Turkey Ham 250 g", "dai", "FUD", "pack", 25, 45.00, "neutral", 3.0, set(), 0),
    ("Lala Panela Cheese 400 g", "dai", "Lala", "pack", 20, 75.00, "neutral", 2.0, set(), 0),
    # Bakery and Cookies (8)
    ("Bimbo White Bread Large 680 g", "bak", "Bimbo", "bag", 10, 52.00, "cold", 6.0, {"bread"}, 0),
    ("Bimbo Mantecadas 4 pcs", "bak", "Bimbo", "pack", 15, 30.00, "cold", 5.0, {"sweet_bread"}, 0),
    ("Marinela Gansito 50 g", "bak", "Marinela", "piece", 30, 18.00, "neutral", 8.0, {"sweet_bread"}, 0),
    ("Bimbo Conchas 2 pcs", "bak", "Bimbo", "pack", 7, 28.00, "cold", 4.0, {"sweet_bread"}, 0),
    ("Bimbo Glazed Donuts 6 pcs", "bak", "Bimbo", "pack", 15, 33.00, "cold", 3.0, {"sweet_bread"}, 0),
    ("Gamesa Emperador Chocolate Cookies 109 g", "bak", "Gamesa", "pack", 180, 22.00, "neutral", 5.0, {"cookies"}, 0),
    ("Marinela Principe Cookies 105 g", "bak", "Marinela", "pack", 180, 22.00, "neutral", 4.0, {"cookies"}, 0),
    ("Bimbo Three Kings' Cake Small", "bak", "Bimbo", "piece", 6, 150.00, "cold", 0.25, {"rosca"}, 1),
    # Groceries (7)
    ("Maruchan Instant Soup Shrimp 64 g", "gro", "Maruchan", "cup", 365, 18.00, "cold", 8.0, {"soup"}, 0),
    ("Maruchan Instant Soup Chicken 64 g", "gro", "Maruchan", "cup", 365, 18.00, "cold", 6.0, {"soup"}, 0),
    ("Valentina Hot Sauce 370 ml", "gro", "Valentina", "bottle", 730, 22.00, "neutral", 2.0, {"dip"}, 0),
    ("Tostitos Cheese Dip 255 g", "gro", "Tostitos", "jar", 270, 52.00, "neutral", 2.0, {"dip"}, 0),
    ("Dolores Tuna in Water 140 g", "gro", "Dolores", "can", 1095, 28.00, "neutral", 3.0, {"tuna"}, 0),
    ("La Costena Refried Beans 430 g", "gro", "La Costena", "can", 730, 28.00, "neutral", 2.0, set(), 0),
    ("Nescafe Clasico Instant Coffee 42 g", "gro", "Nescafe", "jar", 540, 52.00, "cold", 2.0, set(), 0),
    # Frozen Foods and Ice Cream (5)
    ("Holanda Magnum Almond 100 ml", "frz", "Holanda", "piece", 270, 40.00, "hot", 3.0, {"ice_cream"}, 0),
    ("Holanda Mordisko Vanilla", "frz", "Holanda", "piece", 270, 26.00, "hot", 4.0, {"ice_cream"}, 0),
    ("Holanda Fruit Ice Pop", "frz", "Holanda", "piece", 270, 12.00, "hot", 5.0, {"ice_cream"}, 0),
    ("Holanda Cornetto Chocolate", "frz", "Holanda", "piece", 270, 28.00, "hot", 3.0, {"ice_cream"}, 0),
    ("Holanda Vanilla Ice Cream 1 L", "frz", "Holanda", "tub", 270, 75.00, "hot", 1.5, {"ice_cream"}, 0),
    # Ice (2)
    ("Ice Bag 5 kg", "ice", "Private Label", "bag", 365, 35.00, "hot", 5.0, {"ice"}, 0),
    ("Ice Bag 3 kg", "ice", "Private Label", "bag", 365, 25.00, "hot", 4.0, {"ice"}, 0),
    # Cigarettes (3)
    ("Marlboro Red 20 Cigarettes", "cig", "Marlboro", "pack", 365, 90.00, "neutral", 5.0, set(), 0),
    ("Marlboro Gold 20 Cigarettes", "cig", "Marlboro", "pack", 365, 90.00, "neutral", 4.0, set(), 0),
    ("Pall Mall Blue 20 Cigarettes", "cig", "Pall Mall", "pack", 365, 72.00, "neutral", 3.0, set(), 0),
    # Personal Hygiene (3)
    ("Colgate Triple Action Toothpaste 75 ml", "hyg", "Colgate", "tube", 730, 30.00, "neutral", 1.2, set(), 0),
    ("Head & Shoulders Shampoo 180 ml", "hyg", "Head & Shoulders", "bottle", 730, 62.00, "neutral", 0.8, set(), 0),
    ("Petalo Toilet Paper 4 Rolls", "hyg", "Petalo", "pack", 1095, 38.00, "neutral", 1.5, set(), 0),
    # Cleaning Products (3)
    ("Cloralex Bleach 950 ml", "cln", "Cloralex", "bottle", 365, 25.00, "neutral", 1.0, set(), 0),
    ("Fabuloso Lavender Cleaner 1 L", "cln", "Fabuloso", "bottle", 730, 32.00, "neutral", 0.8, set(), 0),
    ("Axion Dish Soap 400 ml", "cln", "Axion", "bottle", 730, 28.00, "neutral", 0.8, set(), 0),
    # Pharmacy (OTC) (2)
    ("Alka-Seltzer 12 Tablets", "phm", "Alka-Seltzer", "box", 730, 45.00, "neutral", 1.0, {"hangover"}, 0),
    ("Desenfriol-D Cold Relief 12 Tablets", "phm", "Desenfriol", "box", 730, 48.00, "cold", 0.8, set(), 0),
    # Pet Supplies (1)
    ("Pedigree Adult Wet Dog Food 100 g", "pet", "Pedigree", "pouch", 540, 17.00, "neutral", 1.5, set(), 0),
    # Baby Products (1)
    ("Huggies Diapers Stage 4 14 pcs", "bby", "Huggies", "pack", 1095, 115.00, "neutral", 0.6, set(), 0),
    # Fruits and Vegetables (1)
    ("Fresh Fruit Cup 300 g", "fru", "Private Label", "cup", 5, 35.00, "hot", 3.0, set(), 0),
    # Disposables and Party Supplies (2)
    ("Plastic Party Cups 20 pcs", "dsp", "Private Label", "pack", 1095, 32.00, "neutral", 0.8, {"disposable"}, 0),
    ("Disposable Plates 20 pcs", "dsp", "Private Label", "pack", 1095, 30.00, "neutral", 0.6, {"disposable"}, 0),
    # Flowers and Gifts (1)
    ("Rose Bouquet 6 Stems", "flw", "Private Label", "bouquet", 7, 99.00, "neutral", 0.4, {"flowers"}, 1),
]

# Seasonal sales windows (month, day) inclusive; outside the window demand is zero.
SEASON_WINDOWS = {"rosca": ((12, 26), (1, 10))}

# --------------------------------------------------------------------------------------------
# Suppliers (19, fictional): (name, city, base_lead_time_days, category_keys)
# --------------------------------------------------------------------------------------------
SUPPLIERS = [
    ("Northern Beverage Distributors", "Saltillo", 2, ["bev", "alc", "ice"]),
    ("Coahuila Bottling Logistics", "Saltillo", 1, ["bev", "ice", "snk"]),
    ("Sierra Madre Beer & Spirits", "Monterrey", 3, ["alc", "ice", "dsp"]),
    ("Altiplano Wholesale Grocers", "Saltillo", 2, ["gro", "cln", "hyg", "pet", "bby", "dsp", "phm"]),
    ("Zapalinamé Snacks & Sweets", "Saltillo", 2, ["snk", "cdy", "cig"]),
    ("Monterrey Confectionery Hub", "Monterrey", 3, ["cdy", "snk", "bak"]),
    ("Fresh Kitchen Commissary del Norte", "Saltillo", 1, ["prep", "fru", "dai"]),
    ("Ramos Arizpe Cold Chain", "Ramos Arizpe", 1, ["dai", "frz", "ice", "prep"]),
    ("Golden Crumb Bakeries", "Saltillo", 1, ["bak", "prep", "cof"]),
    ("Highland Coffee Supply", "Monterrey", 3, ["cof", "gro", "bak"]),
    ("Desert Pharma Distribution", "Monterrey", 3, ["phm", "hyg", "bby"]),
    ("Tobacco & Convenience Supply MX", "Monterrey", 2, ["cig", "cdy", "snk", "bev"]),
    ("Bajío Household Goods", "Querétaro", 5, ["cln", "hyg", "dsp", "pet"]),
    ("Northeast Multi-Category Wholesale", "Monterrey", 3, ["bev", "alc", "snk", "gro", "cln", "cig", "cof"]),
    ("Arteaga Farms & Florals", "Arteaga", 1, ["fru", "flw", "dai"]),
    ("Gulf Frozen Foods", "Monterrey", 2, ["frz", "dai", "prep"]),
    ("Valle Florist Wholesale", "Saltillo", 1, ["flw", "fru", "dsp"]),
    ("Laguna Cold & Care Distribution", "Torreón", 4, ["frz", "phm", "pet", "bby"]),
    ("Saltillo Seasonal Gifts", "Saltillo", 2, ["flw", "cdy", "dsp"]),
]

# --------------------------------------------------------------------------------------------
# Stores (25): (colonia / landmark, lat, lon, zone_type, secondary_zone_type, size_category, is_24h)
# Approximate coordinates inside Saltillo's urban area; jittered slightly at generation time.
# --------------------------------------------------------------------------------------------
STORE_ANCHORS = [
    ("República Oriente", 25.4300, -100.9830, "residential", None, "medium", 1),
    ("Saltillo 2000", 25.4580, -101.0080, "residential", "school", "large", 1),
    ("Lomas de Lourdes", 25.4010, -100.9900, "residential", None, "medium", 0),
    ("Valle de las Flores", 25.4650, -100.9800, "residential", None, "medium", 0),
    ("Mirasierra", 25.4030, -100.9720, "residential", None, "large", 1),
    ("Los Pinos", 25.4380, -100.9700, "residential", None, "small", 0),
    ("Morelos", 25.4180, -101.0200, "residential", None, "small", 0),
    ("Bellavista", 25.4180, -101.0000, "residential", "business", "medium", 1),
    ("Brisas Poniente", 25.4330, -101.0420, "residential", None, "medium", 0),
    ("Satélite Norte", 25.4700, -101.0150, "residential", None, "medium", 1),
    ("Virreyes Residencial", 25.4450, -100.9950, "residential", None, "small", 0),
    ("Zona Centro - Plaza de Armas", 25.4245, -101.0030, "business", "shopping_center", "medium", 1),
    ("Blvd. Venustiano Carranza Corporate Corridor", 25.4400, -100.9880, "business", "residential", "medium", 0),
    ("Ateneo Fuente School Area", 25.4260, -100.9930, "school", "residential", "small", 0),
    ("Escuela Normal de Coahuila Area", 25.4160, -100.9890, "school", None, "small", 0),
    ("Tec de Monterrey Campus Saltillo", 25.4520, -100.9820, "college", "residential", "medium", 0),
    ("UAdeC Unidad Camporredondo", 25.4330, -101.0080, "college", "school", "medium", 1),
    ("Highway 57 - Monterrey Exit", 25.4780, -100.9550, "highway", "industrial", "large", 1),
    ("Periférico LEA - Zacatecas Exit (Hwy 54)", 25.3850, -101.0450, "highway", None, "large", 1),
    ("Saltillo-Ramos Arizpe Industrial Park", 25.4850, -100.9650, "industrial", None, "medium", 1),
    ("Periférico LEA - Derramadero Corridor", 25.3760, -101.0300, "industrial", "highway", "medium", 1),
    ("Galerías Saltillo", 25.4430, -100.9800, "shopping_center", "business", "large", 0),
    ("Sendero Saltillo", 25.4600, -101.0300, "shopping_center", None, "medium", 0),
    ("Hospital Universitario Area", 25.4220, -100.9910, "hospital", None, "medium", 1),
    ("IMSS Hospital General de Zona 2 Area", 25.4350, -100.9760, "hospital", "residential", "medium", 1),
]

# Rough exclusion areas (hills) inside the bounding box: (center_lat, center_lon, radius_deg),
# plus the Sierra de Zapalinamé foothills on the south-east edge.
HILL_CIRCLES = [(25.4080, -101.0120, 0.0060)]


def on_hill(lat: float, lon: float) -> bool:
    if lat < 25.380 and lon > -100.995:
        return True
    return any(((lat - a) ** 2 + (lon - b) ** 2) ** 0.5 < r for a, b, r in HILL_CIRCLES)
