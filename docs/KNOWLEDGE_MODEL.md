# Knowledge model and result semantics (Stage 1)

## Concepts and their types

Every ingredient concept has a stable internal id (its slug) and exactly one type. FoodOn identifiers
are added only where a concept's own name equals exactly one FoodOn label. 93 of the curated concepts
carry one; the rest use internal identifiers only.

| Type | Meaning | Examples |
|---|---|---|
| `food_source` | an animal, plant or group of them | milk, egg, wheat, oats, peanut, fish, tree nut |
| `ingredient` | a culinary ingredient made from a source | butter, flour, tofu, semolina |
| `derivative` | a substance extracted or processed from a source | whey, lactose, oils, starches, caseinates |
| `protein` | a protein | casein, whey protein, gluten, lysozyme |
| `allergen_substance` | a substance that is itself regulated | sulphites |
| `additive` | a food additive | lecithin, guar gum, citric acid |
| `prepared_food` | a made food with several components | mayonnaise, marzipan, chocolate, sausage |
| `general_term` | a name that does not say what is in it | spices, seasoning, flavouring, seafood |

## Relations

Relations run from subject to object and are either *definite* or *possible*. A possible link never
makes an allergen PRESENT.

| Relation | Reads as | Example |
|---|---|---|
| `derived_from` / `is_source_of` | made from | sodium caseinate derived from casein |
| `is_a` | a kind of (same type only) | almond is a tree nut |
| `contains_component` | contains | mayonnaise contains egg |
| `contains_protein` | the source contains the protein; read from the protein as "is a protein of" | milk contains casein |
| `is_regulated_as` | falls under a legally declared allergen category | oats → cereals containing gluten |
| `associated_with_allergen` | a scientific association that is not a regulatory category | none seeded |

- **Synonyms** are each concept's aliases (its `has_synonym` set), not relations.
- **Proteins.** A protein shared by several sources is linked to each of them only as *possible*.
  Gluten, for example, is certainly in the "cereals containing gluten" category, but the protein alone
  does not say whether the cereal was wheat. "Wheat gluten" names the cereal, so it is its own concept
  and links to wheat definitely.
- **Homonyms.** A name with several meanings keeps every reading, and an allergen is definite only if
  every reading carries it. "Curd" means yogurt in Indian English and bean curd elsewhere, so it is
  *possibly* milk and *possibly* soy.

## Scientific and regulatory knowledge are separate

The ingredient relations describe what a food is made from. `is_regulated_as` and the regional lists
describe what the law requires to be declared.

- **Oats and gluten.** Oats contain avenin rather than gluten. They are regulated as a "cereal
  containing gluten" under EU and Indian rules, so this is a regulatory link, not a scientific one.
- **Wheat and gluten.** Wheat is one of the "cereals containing gluten". The allergen category list
  records this as category membership, so a wheat finding also counts for that category. This is not a
  claim that wheat is a kind of gluten.
- **Regional lists.** EU (Regulation 1169/2011, Annex II), US (FALCPA as amended by the FASTER Act) and
  India (FSSAI Labelling and Display Regulations 2020) are kept as separate lists. Each has its legal
  reference, version and status. The US list has wheat but no gluten category, and no molluscs.
- **India.** The Indian list is marked *unverified*: the regulation text could not be consulted from the
  build environment. Verifying it needs the gazetted text.
- **Not supported yet:** jurisdiction-specific thresholds (only the sulphite 10 mg/kg wording is
  recorded), and precautionary-labelling rules.

## Rules every change must pass

`backend/app/knowledge/validation.py` checks the seed data and every approved knowledge change, before
it is published:

1. Every concept has a known type, and every relation has known endpoints and certainty.
2. `is_regulated_as` / `associated_with_allergen` point to an allergen category. Only a food source,
   protein or regulated substance is regulated as one.
3. `is_a` links concepts of the same type.
4. Nothing is derived from a made food, a general term or an additive. A food source is not derived
   from a non-source (this catches reversed direction).
5. `contains_protein` points to a protein, from a source, ingredient or derivative.
6. A general term can only *possibly* contain something. An unstated-source concept (lecithin,
   chocolate) can never *definitely* lead to an allergen.
7. No concept relates to itself, and following relations never leads back to the start (no cycles).
8. Every allergen category is reached by at least one concept.

On top of these rules, a change must pass the regression cases and the safety benchmark (Step F).

**FoodOn mapping changes** go through a `foodon_mapping` proposal, which needs evidence and a second
reviewer. The change log keeps the previous identifier. The summary endpoint names the FoodOn release
the original mappings came from.

## Normalization, ambiguity and unknown text

- **Kept for every item:** the original text, the normalized text, the method, a confidence, the
  concept and the relationship path.
- **Unresolved items** also keep:
  - the close matches that were considered but *not* accepted (`candidates`)
  - the reasons (`unresolved_reasons`): unknown words, several equally close matches, a guess that
    would have meant "no allergen", or no close match at all
- **The unresolved-ingredient queue** stores both, with no user identity.
- **A name with a source qualifier** ("lecithins (soya)") takes its source from the brackets.
- **A made food or general term with its components listed** ("chocolate (sugar, cocoa mass, cocoa
  butter)") keeps them as components. When every component is identified and the list is complete, the
  components replace the name's *possible* ingredients.
- **Incomplete component lists:** a list ending in "etc.", "and others" or "…", a bracket that is never
  closed (often a cut-off photo), or empty brackets. Each one is recorded on the item and makes the
  source *partial*.

## Result semantics

For each allergen in the customer's profile the engine returns exactly one outcome. The first rule
that applies wins (the rule ids are those shown in results and metrics):

| Outcome | Meaning | Rule |
|---|---|---|
| PRESENT | A trusted source names an ingredient that carries the allergen (directly or through a definite relation), or declares it | R1 |
| POSSIBLE CROSS-CONTACT | A source reports cross-contact, or a precautionary statement names it | R2 |
| UNKNOWN | Only unverified or possibly-mismatched sources suggest it, or an ingredient may come from it, or a free-from claim is all there is, or absence cannot be established | R3, R4, R6 |
| NOT LISTED IN VERIFIED RECORDS | No evidence of it **and** absence is supported | R5 |

**When absence is supported.** At least one source is *eligible*, and no two eligible sources'
ingredient lists clearly disagree. A source is eligible only when all of these hold:

- it can establish absence: a label, the product database, the manufacturer, or a verified restaurant
  record
- it belongs to the item checked: identification confidence ≥ 0.7, or it is the customer's own label
- it states the full list, not a partial one
- it was read reliably (confidence ≥ 0.6)
- it lists at least one ingredient
- it is within its freshness limit
- every ingredient in it was identified
- no part of its list is cut off or open-ended

**NOT LISTED IN VERIFIED RECORDS** means only that the allergen is not in the verified records checked.
It is never a statement that the food is safe or allergen-free. UNKNOWN means the evidence cannot
settle the question, and is never treated as reassurance.
