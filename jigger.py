#!/usr/bin/env python3
"""Jigger: offline cocktail batch planner.

Reads plain-text recipe files and works out scaled ingredient lists, ABV and
proof, a dilution estimate from stirring or shaking time, bottled batch plans,
per-drink cost, shopping lists and line-numbered lint. Standard library only.
All arithmetic uses fractions.Fraction; numbers are only rounded for display.
"""

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

UNIT_ML = {
    "ml": Fraction(1),
    "cl": Fraction(10),
    "oz": Fraction("29.5735"),  # US fluid ounce
    "tsp": Fraction("4.92892"),  # US teaspoon
    "tbsp": Fraction("14.7868"),  # US tablespoon
    "dash": Fraction("0.9"),  # bitters dash, a common convention
}

# Dilution per second of mixing, and the most water the method can add.
METHODS = {
    "build": (Fraction(0), Fraction(0)),
    "stir": (Fraction(8, 1000), Fraction(35, 100)),
    "shake": (Fraction(15, 1000), Fraction(40, 100)),
    "blend": (Fraction(20, 1000), Fraction(45, 100)),
}
MAX_DILUTION = Fraction(60, 100)
MAX_METHOD_SECONDS = 120

ERROR = "error"
WARNING = "warning"

RECIPE_RE = re.compile(r"recipe\s+(.+)", re.I)
SERVES_RE = re.compile(r"serves\s+(\d+)", re.I)
METHOD_RE = re.compile(r"method\s+([a-z]+)(?:\s+(\d+)\s*s)?", re.I)
INGREDIENT_RE = re.compile(
    r"([a-z][a-z' \-]*?)\s+"  # name
    r"(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*"  # quantity: 1 1/2, 3/4, 1.5
    r"(ml|cl|oz|tsp|tbsp|dash)\b"  # unit
    r"(?:\s+(\d+(?:\.\d+)?)%)?"  # ABV
    r"(?:\s*@\s*(\d+(?:\.\d{1,2})?)/(\d+(?:\.\d+)?)\s*(ml|cl|oz))?$",  # price per pack
    re.I,
)


class CliError(Exception):
    """A usage problem that should stop the command with exit code 2."""


@dataclass
class Problem:
    line: int
    level: str
    message: str

    def __str__(self):
        return f"{self.line}: {self.level}: {self.message}"


@dataclass
class Ingredient:
    name: str
    line: int
    qty: Fraction
    unit: str
    abv: Fraction
    price: Fraction | None = None
    pack_ml: Fraction | None = None

    @property
    def ml(self):
        return self.qty * UNIT_ML[self.unit]

    def cost_for(self, ml):
        if self.price is None:
            return None
        return ml * self.price / self.pack_ml


@dataclass
class Recipe:
    name: str
    line: int
    serves: int = 1
    method: str = "build"
    seconds: int = 0
    ingredients: list = field(default_factory=list)

    @property
    def base_ml(self):
        return sum((i.ml for i in self.ingredients), Fraction(0))

    @property
    def ethanol_ml(self):
        return sum((i.ml * i.abv / 100 for i in self.ingredients), Fraction(0))


@dataclass
class MixRow:
    ingredient: Ingredient
    ml: Fraction
    cost: Fraction | None


@dataclass
class Mix:
    rows: list
    dilution: Fraction
    base_ml: Fraction
    water_ml: Fraction
    ethanol_ml: Fraction
    cost: Fraction | None

    @property
    def final_ml(self):
        return self.base_ml + self.water_ml

    @property
    def abv(self):
        return self.ethanol_ml / self.final_ml * 100

    @property
    def proof(self):
        return 2 * self.abv


def fmt(value, places=1):
    """Round an exact Fraction half-up to a fixed number of decimal places."""
    number = Decimal(value.numerator) / Decimal(value.denominator)
    return str(number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def parse_qty(text):
    parts = text.split()
    if len(parts) == 2:
        return Fraction(parts[0]) + Fraction(parts[1])
    return Fraction(parts[0])


def parse(text):
    """Turn recipe text into (recipes, problems). Syntax problems only."""
    recipes = []
    problems = []
    current = None
    recipe_lines = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if match := RECIPE_RE.fullmatch(line):
            name = match.group(1).strip()
            key = name.lower()
            if key in recipe_lines:
                problems.append(Problem(number, ERROR, f"duplicate recipe '{name}' (first at line {recipe_lines[key]})"))
            recipe_lines.setdefault(key, number)
            current = Recipe(name=name, line=number)
            recipes.append(current)
            continue
        if current is None:
            problems.append(Problem(number, ERROR, "line appears before any 'recipe' line"))
            continue
        if match := SERVES_RE.fullmatch(line):
            serves = int(match.group(1))
            if serves < 1:
                problems.append(Problem(number, ERROR, "serves must be at least 1"))
            else:
                current.serves = serves
            continue
        if match := METHOD_RE.fullmatch(line):
            method = match.group(1).lower()
            if method not in METHODS:
                problems.append(Problem(number, ERROR, f"unknown method '{method}' (use build, stir, shake or blend)"))
                continue
            current.method = method
            current.seconds = int(match.group(2)) if match.group(2) else 0
            continue
        if match := INGREDIENT_RE.fullmatch(line):
            ingredient = parse_ingredient(match, number, problems)
            if ingredient is None:
                continue
            if any(i.name.lower() == ingredient.name.lower() for i in current.ingredients):
                problems.append(Problem(number, ERROR, f"ingredient '{ingredient.name}' listed twice in '{current.name}'"))
                continue
            current.ingredients.append(ingredient)
            continue
        problems.append(Problem(number, ERROR, f"cannot read line {line!r} (units: ml, cl, oz, tsp, tbsp, dash)"))
    return recipes, problems


def parse_ingredient(match, number, problems):
    name = match.group(1).strip()
    qty = parse_qty(match.group(2))
    unit = match.group(3).lower()
    if qty <= 0:
        problems.append(Problem(number, ERROR, f"amount of '{name}' must be above zero"))
        return None
    if match.group(4) is None:
        abv = Fraction(0)
        problems.append(Problem(number, WARNING, f"no ABV given for '{name}', treated as 0%"))
    else:
        abv = Fraction(match.group(4))
        if abv > 100:
            problems.append(Problem(number, ERROR, f"ABV of '{name}' cannot be above 100%"))
            return None
    price = pack_ml = None
    if match.group(5) is not None:
        price = Fraction(match.group(5))
        pack = Fraction(match.group(6))
        pack_ml = pack * UNIT_ML[match.group(7).lower()]
        if pack_ml <= 0:
            problems.append(Problem(number, ERROR, f"pack size of '{name}' must be above zero"))
            return None
    return Ingredient(name=name, line=number, qty=qty, unit=unit, abv=abv, price=price, pack_ml=pack_ml)


def check(recipes):
    """Semantic checks that need the whole parsed file."""
    problems = []
    seen = {}
    for recipe in recipes:
        if not recipe.ingredients:
            problems.append(Problem(recipe.line, ERROR, f"recipe '{recipe.name}' has no ingredients"))
        if recipe.method != "build" and recipe.seconds == 0:
            problems.append(Problem(recipe.line, WARNING, f"method '{recipe.method}' has no time, so no dilution is applied"))
        if recipe.seconds > MAX_METHOD_SECONDS:
            problems.append(Problem(recipe.line, WARNING, f"method time of {recipe.seconds} s is over {MAX_METHOD_SECONDS} s"))
        for ing in recipe.ingredients:
            key = ing.name.lower()
            if key not in seen:
                seen[key] = ing
                continue
            first = seen[key]
            if ing.abv != first.abv:
                problems.append(Problem(ing.line, WARNING, f"ABV of '{ing.name}' differs from line {first.line}"))
            if (ing.price, ing.pack_ml) != (first.price, first.pack_ml):
                problems.append(Problem(ing.line, WARNING, f"price of '{ing.name}' differs from line {first.line}"))
    return problems


def lint_text(text):
    recipes, problems = parse(text)
    problems += check(recipes)
    problems.sort(key=lambda p: p.line)
    return recipes, problems


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise CliError(f"cannot read {path}: {exc.strerror}") from exc
    return text


def load_usable(path):
    """Parse a file for planning; refuse to plan from a file with errors."""
    recipes, problems = lint_text(load(path))
    errors = [p for p in problems if p.level == ERROR]
    if errors:
        raise CliError(f"{path} has {len(errors)} error(s), run lint first: " + "; ".join(str(p) for p in errors[:3]))
    return recipes


def find_recipe(recipes, name):
    for recipe in recipes:
        if recipe.name.lower() == name.lower():
            return recipe
    names = ", ".join(r.name for r in recipes) or "none"
    raise CliError(f"no recipe called '{name}' (have: {names})")


def parse_percent(text):
    try:
        value = Fraction(text) / 100
    except (ValueError, ZeroDivisionError) as exc:
        raise CliError(f"dilution must be a percentage, got {text!r}") from exc
    if value < 0 or value >= MAX_DILUTION:
        raise CliError(f"dilution must be from 0 to under {int(MAX_DILUTION * 100)}%")
    return value


def dilution_for(recipe, override=None):
    if override is not None:
        return override
    rate, cap = METHODS[recipe.method]
    return min(rate * recipe.seconds, cap)


def mix_for_scale(recipe, scale, dilution):
    """Scale the recipe's amounts and add the water that the method gives."""
    rows = []
    total_cost = Fraction(0)
    priced = True
    for ing in recipe.ingredients:
        ml = ing.ml * scale
        cost = ing.cost_for(ml)
        if cost is None:
            priced = False
        else:
            total_cost += cost
        rows.append(MixRow(ing, ml, cost))
    base = sum((row.ml for row in rows), Fraction(0))
    water = base * dilution / (1 - dilution)
    ethanol = recipe.ethanol_ml * scale
    return Mix(rows, dilution, base, water, ethanol, total_cost if priced else None)


def drink_mix(recipe, drinks=None, dilution=None):
    drinks = recipe.serves if drinks is None else drinks
    if drinks < 1:
        raise CliError("--drinks must be at least 1")
    d = dilution_for(recipe, dilution)
    mix = mix_for_scale(recipe, Fraction(drinks, recipe.serves), d)
    return mix, drinks, mix.final_ml / drinks


def batch_plan(recipe, bottles, size_ml, dilution=None):
    if bottles < 1:
        raise CliError("--bottles must be at least 1")
    if size_ml <= 0:
        raise CliError("--size must be above zero")
    d = dilution_for(recipe, dilution)
    total = Fraction(bottles) * size_ml
    mix = mix_for_scale(recipe, total * (1 - d) / recipe.base_ml, d)
    per_drink = recipe.base_ml / (recipe.serves * (1 - d))
    whole = math.floor(total / per_drink)
    leftover = total - whole * per_drink
    return mix, whole, per_drink, leftover


def parse_plan(text):
    plan = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, count = item.rpartition("=")
        if not sep or not name.strip() or not count.strip().isdigit() or int(count) < 1:
            raise CliError(f"plan item {item!r} must look like Name=drinks")
        plan.append((name.strip(), int(count)))
    if not plan:
        raise CliError("--plan is empty")
    return plan


def shopping_list(recipes, plan, default_pack_ml):
    needs = {}
    for recipe_name, drinks in plan:
        recipe = find_recipe(recipes, recipe_name)
        scale = Fraction(drinks, recipe.serves)
        for ing in recipe.ingredients:
            entry = needs.setdefault(ing.name.lower(), {"name": ing.name, "ml": Fraction(0), "price": None, "pack": None})
            entry["ml"] += ing.ml * scale
            if entry["price"] is None and ing.price is not None:
                entry["price"], entry["pack"] = ing.price, ing.pack_ml
    rows = []
    for key in sorted(needs):
        entry = needs[key]
        pack = entry["pack"] or default_pack_ml
        bottles = math.ceil(entry["ml"] / pack)
        cost = bottles * entry["price"] if entry["price"] is not None else None
        rows.append({
            "name": entry["name"],
            "ml": entry["ml"],
            "pack_ml": pack,
            "bottles": bottles,
            "leftover_ml": bottles * pack - entry["ml"],
            "cost": cost,
        })
    return rows


def money(value):
    return fmt(value, 2) if value is not None else "n/a"


def print_mix(mix):
    print(f"  {'ingredient':<20}{'ml':>9}{'oz':>8}{'ABV':>8}{'cost':>9}")
    for row in mix.rows:
        ing = row.ingredient
        print(f"  {ing.name:<20}{fmt(row.ml):>9}{fmt(row.ml / UNIT_ML['oz'], 2):>8}"
              f"{fmt(ing.abv) + '%':>8}{money(row.cost):>9}")
    print(f"  {'base mix':<20}{fmt(mix.base_ml):>9}")
    print(f"  {'water from method':<20}{fmt(mix.water_ml):>9}")
    print(f"  {'final volume':<20}{fmt(mix.final_ml):>9}")
    print(f"  ABV {fmt(mix.abv)}%   proof {fmt(mix.proof)}   dilution {fmt(mix.dilution * 100)}%")


def cmd_lint(args):
    recipes, problems = lint_text(load(args.file))
    errors = [p for p in problems if p.level == ERROR]
    warnings = [p for p in problems if p.level == WARNING]
    for problem in problems:
        print(f"{args.file}:{problem}")  # e.g. recipes.txt:7: error: ...
    if not problems:
        print(f"ok: {len(recipes)} recipe(s), no problems")
    else:
        print(f"{len(errors)} error(s), {len(warnings)} warning(s) in {len(recipes)} recipe(s)")
    if errors or (args.strict and warnings):
        return 1
    return 0


def cmd_show(args):
    recipe = find_recipe(load_usable(args.file), args.recipe)
    override = parse_percent(args.dilution) if args.dilution is not None else None
    mix, drinks, per_drink = drink_mix(recipe, args.drinks, override)
    cost = mix.cost
    if args.json:
        data = {
            "recipe": recipe.name,
            "drinks": drinks,
            "method": recipe.method,
            "dilution_pct": float(mix.dilution * 100),
            "base_ml": float(mix.base_ml),
            "water_ml": float(mix.water_ml),
            "final_ml": float(mix.final_ml),
            "per_drink_ml": float(per_drink),
            "abv_pct": float(mix.abv),
            "proof": float(mix.proof),
            "cost": float(cost) if cost is not None else None,
            "ingredients": [{"name": r.ingredient.name, "ml": float(r.ml)} for r in mix.rows],
        }
        print(json.dumps(data, indent=2))
        return 0
    print(f"{recipe.name}: {drinks} drink(s), method {recipe.method} {recipe.seconds} s")
    print_mix(mix)
    print(f"  per drink {fmt(per_drink)} ml   cost {money(cost)} total, "
          f"{money(cost / drinks if cost is not None else None)} per drink")
    return 0


def cmd_batch(args):
    recipe = find_recipe(load_usable(args.file), args.recipe)
    override = parse_percent(args.dilution) if args.dilution is not None else None
    mix, whole, per_drink, leftover = batch_plan(recipe, args.bottles, Fraction(args.size), override)
    print(f"{recipe.name}: {args.bottles} x {fmt(Fraction(args.size), 0)} ml bottles")
    print_mix(mix)
    print(f"  bottled total {fmt(mix.final_ml)} ml, water to add {fmt(mix.water_ml)} ml")
    print(f"  {whole} whole drink(s) at {fmt(per_drink)} ml each, {fmt(leftover)} ml left over")
    print(f"  cost {money(mix.cost)} total")
    return 0


def cmd_shop(args):
    recipes = load_usable(args.file)
    plan = parse_plan(args.plan)
    default_pack = Fraction(args.pack)
    if default_pack <= 0:
        raise CliError("--pack must be above zero")
    rows = shopping_list(recipes, plan, default_pack)
    print(f"{'item':<20}{'need ml':>10}{'pack ml':>9}{'bottles':>9}{'spare ml':>10}{'cost':>9}")
    total = Fraction(0)
    for row in rows:
        if row["cost"] is not None:
            total += row["cost"]
        print(f"{row['name']:<20}{fmt(row['ml']):>10}{fmt(row['pack_ml'], 0):>9}"
              f"{row['bottles']:>9}{fmt(row['leftover_ml']):>10}{money(row['cost']):>9}")
    print(f"priced items total {money(total)}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="jigger", description="Offline cocktail batch planner.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lint", help="check a recipe file with line-numbered problems")
    p.add_argument("file")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("show", help="scale one recipe, with water, ABV, proof and cost")
    p.add_argument("file")
    p.add_argument("recipe")
    p.add_argument("--drinks", type=int, help="how many drinks to make (default: the recipe's serves)")
    p.add_argument("--dilution", help="override the method's water share, as a percentage")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("batch", help="plan bottles of a recipe with water added")
    p.add_argument("file")
    p.add_argument("recipe")
    p.add_argument("--bottles", type=int, required=True)
    p.add_argument("--size", default="750", help="bottle size in ml (default 750)")
    p.add_argument("--dilution", help="override the method's water share, as a percentage")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("shop", help="total ingredients for a plan and round up to bottles")
    p.add_argument("file")
    p.add_argument("--plan", required=True, help='drinks per recipe, e.g. "Negroni=10,Martini=6"')
    p.add_argument("--pack", default="750", help="pack size in ml for items without a price (default 750)")
    p.set_defaults(func=cmd_shop)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"jigger: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
