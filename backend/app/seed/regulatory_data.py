"""Regional allergen lists, kept separate from the scientific ingredient-to-allergen relationships.

Each list records its legal reference and a verification status:
  "curated"    entered by the project team from the cited regulation
  "unverified" the regulation text could not be consulted when this list was entered; check before
               relying on it
"""

REGULATORY_LISTS = [
    {
        "jurisdiction": "EU",
        "name": "EU major allergens",
        "legal_reference": "Regulation (EU) No 1169/2011, Annex II",
        "version": "2011/1169 Annex II",
        "status": "curated",
        "note": "Substances or products causing allergies or intolerances.",
        "entries": [
            ("gluten", "Cereals containing gluten (wheat, rye, barley, oats, spelt, kamut or their hybridised strains)"),
            ("wheat", "Wheat (named as a cereal containing gluten)"),
            ("crustaceans", "Crustaceans and products thereof"),
            ("eggs", "Eggs and products thereof"),
            ("fish", "Fish and products thereof"),
            ("peanuts", "Peanuts and products thereof"),
            ("soy", "Soybeans and products thereof"),
            ("milk", "Milk and products thereof (including lactose)"),
            ("tree_nuts", "Nuts: almonds, hazelnuts, walnuts, cashews, pecans, Brazil nuts, pistachios, macadamia/Queensland nuts"),
            ("celery", "Celery and products thereof"),
            ("mustard", "Mustard and products thereof"),
            ("sesame", "Sesame seeds and products thereof"),
            ("sulphites", "Sulphur dioxide and sulphites at concentrations of more than 10 mg/kg or 10 mg/litre"),
            ("lupin", "Lupin and products thereof"),
            ("molluscs", "Molluscs and products thereof"),
        ],
    },
    {
        "jurisdiction": "US",
        "name": "US major food allergens",
        "legal_reference": "FALCPA 2004 (Public Law 108-282) as amended by the FASTER Act 2021 (Public Law 117-11)",
        "version": "FASTER Act 2021",
        "status": "curated",
        "note": "Nine major food allergens; sesame added with effect from 1 January 2023.",
        "entries": [
            ("milk", "Milk"),
            ("eggs", "Eggs"),
            ("fish", "Fish"),
            ("crustaceans", "Crustacean shellfish"),
            ("tree_nuts", "Tree nuts"),
            ("peanuts", "Peanuts"),
            ("wheat", "Wheat"),
            ("soy", "Soybeans"),
            ("sesame", "Sesame"),
        ],
    },
    {
        "jurisdiction": "IN",
        "name": "India allergen declaration (FSSAI)",
        "legal_reference": "Food Safety and Standards (Labelling and Display) Regulations, 2020",
        "version": "2020",
        "status": "unverified",
        "note": "Entered from the project team's reading of the regulation; the official text could not be "
        "retrieved from the build environment. Verify against the gazetted regulation before relying on it.",
        "entries": [
            ("gluten", "Cereals containing gluten"),
            ("wheat", "Wheat (as a cereal containing gluten)"),
            ("crustaceans", "Crustacean and their products"),
            ("milk", "Milk and milk products"),
            ("eggs", "Eggs and egg products"),
            ("fish", "Fish and fish products"),
            ("peanuts", "Peanuts and their products"),
            ("tree_nuts", "Tree nuts and their products"),
            ("soy", "Soybeans and their products"),
            ("sulphites", "Sulphite in concentration of 10 mg/kg or more"),
        ],
    },
]
