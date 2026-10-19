# Jigger

**Offline cocktail batch planner.** Write each recipe once in a plain-text file, then get exact scaled amounts, ABV and proof, a dilution estimate from your stir or shake time, bottled batch plans, per-drink cost and a shopping list. Standard library only, no network, no accounts.

## Features

- **Plain-text recipe files** with many recipes per file and `#` comments.
- **Exact fractions** for amounts such as `1 1/2 oz` and `3/4 oz`, with all maths done in `Fraction`.
- **Six units**: `ml`, `cl`, `oz` (US fl oz), `tsp`, `tbsp` and `dash`.
- **Dilution estimate** from `method stir 30s`, `shake 12s` or `blend 10s`, capped per method. `build` adds no water.
- **Final ABV and US proof** after the water from mixing is added, reported to 0.1.
- **Show a drink at any count** with `--drinks N`: scaled amounts, water, final volume and volume per drink.
- **Dilution override** with `--dilution PCT` when your own measurements differ from the estimate.
- **Bottled batch plans** with `batch --bottles N --size ML`: water to add, whole drinks per bottle and millilitres left over.
- **Cost per drink** from `@price/pack` on each line, rounded half-up to cents. Ingredients without a price report `n/a`.
- **Shopping list** with `shop --plan "Name=drinks,..."`: totals per ingredient, rounded up to whole bottles, with spare millilitres and cost.
- **Line-numbered lint** for syntax errors, duplicate names, zero or impossible amounts, unknown methods and missing ABV, plus cross-recipe checks that flag an ingredient whose ABV or price differs between recipes.
- **CI-friendly exit codes**: `lint` exits 1 on errors (and on warnings with `--strict`); planning commands refuse a file with errors and exit 2.
- **JSON output** from `show --json` for scripts.

## Recipe format

```
# House book
recipe Negroni
method stir 30s
gin 1 oz 40% @ 28.00/750ml
campari 1 oz 25% @ 21.50/1000ml
sweet vermouth 1 oz 16% @ 14.00/750ml
```

- `recipe NAME` starts a recipe. Names must be unique, ignoring case.
- `serves N` says how many drinks the listed amounts make (default 1).
- `method build|stir|shake|blend [SECONDS s]` sets the mixing method. The default is `build` with no water.
- An ingredient line is `NAME AMOUNT UNIT [ABV%] [@ PRICE/PACKunit]`. A missing ABV is treated as 0% with a warning, so write `0%` for juices and syrups.

The dilution estimate uses about 0.8% water per second of stirring (up to 35%), 1.5% per second of shaking (up to 40%) and 2% per second of blending (up to 45%). Treat it as a starting point and calibrate it with `--dilution` against your own drinks.

## Quick start

The repository includes `recipes.txt` with Negroni and Martini recipes. Run these from the project directory:

```
python3 jigger.py lint recipes.txt
python3 jigger.py show recipes.txt Negroni
python3 jigger.py show recipes.txt Martini --drinks 4 --json
python3 jigger.py batch recipes.txt Negroni --bottles 2 --size 750
python3 jigger.py shop recipes.txt --plan "Negroni=10,Martini=8"
```

`show` prints a table of ingredients in ml, oz, ABV and cost, followed by the base mix, water from the method, final volume, ABV, proof and cost per drink. `batch` reports how many whole drinks each bottle yields and the millilitres left over.

## Running

Python 3.10 or later is required. There are no third-party runtime dependencies.

```
python3 jigger.py --help
python3 jigger.py <lint|show|batch|shop> ...
```

## Testing

The tests use pytest and cover parsing, line-numbered errors, the dilution and ABV maths, batch and shop rounding, and the CLI exit codes.

```
python3 -m pytest -q
```

Run the command from the project root, or from `tests/`. The tests write their fixtures to `tmp_path` and do not depend on the current date or the working directory.
