"""Deterministic spelling normalization: regional variants and frequent misspellings -> canonical word.

Whole-word replacements only. Every entry is an exact, reviewed mapping (unlike fuzzy matching, which
is a guess), so a replacement here counts as a reliable identification.
"""

SPELLING_VARIANTS: dict[str, str] = {
    # US -> British-form canonical spellings used in the knowledge base
    "flavor": "flavour", "flavors": "flavours", "flavoring": "flavouring", "flavorings": "flavourings",
    "color": "colour", "colors": "colours", "coloring": "colouring",
    "sulfite": "sulphite", "sulfites": "sulphites", "sulfur": "sulphur",
    "stabilizer": "stabiliser", "stabilizers": "stabilisers",
    "hydrolyzed": "hydrolysed", "caramelized": "caramelised",
    "fiber": "fibre", "yogourt": "yogurt", "yoghourt": "yogurt",
    "chili": "chilli", "chilies": "chillies", "chiles": "chillies",
    "aubergines": "aubergine", "garbanzo": "chickpea", "garbanzos": "chickpeas",
    # frequent misspellings
    "mayonaise": "mayonnaise", "mayonnaisse": "mayonnaise",
    "parmesian": "parmesan", "mozarella": "mozzarella", "mozzerella": "mozzarella", "ricota": "ricotta",
    "worchestershire": "worcestershire", "worcester": "worcestershire",
    "tumeric": "turmeric", "cardamon": "cardamom", "cummin": "cumin", "corriander": "coriander",
    "cinammon": "cinnamon", "sesami": "sesame", "seseme": "sesame", "sesamee": "sesame",
    "pistacchio": "pistachio", "pistacio": "pistachio",
    "hazlenut": "hazelnut", "hazlenuts": "hazelnuts",
    "cashwe": "cashew", "almod": "almond",
    "peanunt": "peanut", "penut": "peanut",
    "soyabean": "soya bean", "soyabeans": "soya beans", "soybeen": "soybean",
    "lecitin": "lecithin", "lecitine": "lecithin", "lecithine": "lecithin",
    "casien": "casein", "caseine": "casein", "caseinat": "caseinate",
    "glutten": "gluten", "wheet": "wheat",
    "musturd": "mustard", "mustrad": "mustard", "celary": "celery", "selery": "celery",
    "prawnes": "prawns", "anchovie": "anchovy", "anchovys": "anchovies",
    "panner": "paneer", "panir": "paneer", "ghi": "ghee",
}

# Ingredient class names that introduce the actual ingredient: "emulsifier (soy lecithin)",
# "thickener: guar gum". The class name itself says nothing about allergens.
CLASS_LABELS = {
    "emulsifier", "emulsifiers", "thickener", "thickeners", "stabiliser", "stabilisers", "raising agent",
    "raising agents", "leavening agent", "flavour enhancer", "flavour enhancers", "acidity regulator",
    "acidity regulators", "preservative", "preservatives", "antioxidant", "antioxidants", "colour",
    "colours", "sweetener", "sweeteners", "gelling agent", "humectant", "firming agent", "glazing agent",
    "anti-caking agent", "anticaking agent", "bulking agent", "flour treatment agent", "acid",
}

# Ingredient-list headers that are not ingredients: "contains 2% or less of: salt, ..."
HEADER_PATTERNS = (
    r"^contains? (?:\d+(?:\.\d+)?% )?(?:or less )?of\b",
    r"^less than \d+(?:\.\d+)?% of\b",
    r"^made (?:with|from)\b",
)
