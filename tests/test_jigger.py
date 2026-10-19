import json
from fractions import Fraction

import pytest

import jigger
from jigger import CliError, UNIT_ML

NEGRONI = """\
# house book
recipe Negroni
method stir 30s
gin 1 oz 40%
campari 1 oz 25%
sweet vermouth 1 oz 16%
"""

MARTINI = """\
recipe Martini
method stir 30s
gin 2 1/2 oz 40% @ 28.00/750ml
dry vermouth 3/4 oz 18% @ 12.00/750ml
"""


def write(tmp_path, text, name="recipes.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def recipe_from(text):
    recipes, problems = jigger.lint_text(text)
    assert not [p for p in problems if p.level == "error"], problems
    return recipes[0]


def test_parses_mixed_number_quantities():
    recipe = recipe_from("recipe Rye\nrye 1 1/2 oz 45%\n")
    assert recipe.ingredients[0].qty == Fraction(3, 2)
    assert recipe.ingredients[0].unit == "oz"


def test_converts_units_to_exact_millilitres():
    recipe = recipe_from("recipe Dash\nbitters 2 dash 0%\nlime 1 tsp 0%\n")
    assert recipe.ingredients[0].ml == 2 * UNIT_ML["dash"]
    assert recipe.ingredients[1].ml == UNIT_ML["tsp"]


def test_price_line_gives_exact_cost_per_drink():
    recipe = recipe_from(MARTINI)
    gin = recipe.ingredients[0]
    assert gin.price == Fraction("28.00")
    assert gin.pack_ml == 750
    assert gin.cost_for(30) == Fraction(30 * 28, 750)


def test_syntax_error_reports_its_line_number():
    _, problems = jigger.lint_text("recipe Bad\ngin 1 oz 40%\ngin one bottle\n")
    errors = [p for p in problems if p.level == "error"]
    assert [p.line for p in errors] == [3]
    assert "cannot read line" in errors[0].message


def test_ingredient_before_recipe_is_an_error():
    _, problems = jigger.lint_text("gin 1 oz 40%\n")
    assert problems[0].line == 1
    assert "before any 'recipe' line" in problems[0].message


def test_duplicate_recipe_and_duplicate_ingredient_are_errors():
    text = "recipe A\ngin 1 oz 40%\ngin 2 oz 40%\nrecipe a\nlime 1 oz 0%\n"
    _, problems = jigger.lint_text(text)
    messages = [p.message for p in problems if p.level == "error"]
    assert any("listed twice" in m for m in messages)
    assert any("duplicate recipe" in m for m in messages)


def test_zero_amount_and_impossible_abv_are_errors():
    _, problems = jigger.lint_text("recipe X\ngin 0 oz 40%\nrum 1 oz 120%\n")
    errors = {p.line: p.message for p in problems if p.level == "error"}
    assert "must be above zero" in errors[2]
    assert "cannot be above 100%" in errors[3]


def test_unknown_method_is_an_error():
    _, problems = jigger.lint_text("recipe X\nmethod whisk 10s\ngin 1 oz 40%\n")
    assert problems[0].line == 2 and problems[0].level == "error"


def test_missing_abv_is_a_warning_not_an_error():
    recipes, problems = jigger.lint_text("recipe X\nlime 1 oz\n")
    assert [p.level for p in problems] == ["warning"]
    assert recipes[0].ingredients[0].abv == 0


def test_cross_recipe_abv_mismatch_is_flagged():
    text = "recipe A\ngin 1 oz 40%\nrecipe B\ngin 1 oz 47%\n"
    _, problems = jigger.lint_text(text)
    assert [p.line for p in problems] == [4]
    assert "differs from line 2" in problems[0].message


def test_dilution_from_method_time_with_cap():
    stir30 = recipe_from("recipe A\nmethod stir 30s\ngin 1 oz 40%\n")
    shake12 = recipe_from("recipe B\nmethod shake 12s\ngin 1 oz 40%\n")
    stir_long = recipe_from("recipe C\nmethod stir 90s\ngin 1 oz 40%\n")
    assert jigger.dilution_for(stir30) == Fraction(24, 100)
    assert jigger.dilution_for(shake12) == Fraction(18, 100)
    assert jigger.dilution_for(stir_long) == Fraction(35, 100)


def test_mix_adds_exact_water_and_reports_abv():
    recipe = recipe_from(NEGRONI)
    mix = jigger.mix_for_scale(recipe, 1, Fraction(24, 100))
    base = 3 * UNIT_ML["oz"]
    ethanol = UNIT_ML["oz"] * (Fraction(40, 100) + Fraction(25, 100) + Fraction(16, 100))
    assert mix.base_ml == base
    assert mix.water_ml == base * Fraction(24, 100) / Fraction(76, 100)
    assert mix.final_ml == base / Fraction(76, 100)
    assert mix.abv == ethanol / mix.final_ml * 100


def test_show_scales_with_drinks():
    recipe = recipe_from(NEGRONI)
    mix, drinks, per_drink = jigger.drink_mix(recipe, drinks=2)
    assert drinks == 2
    assert mix.base_ml == 2 * 3 * UNIT_ML["oz"]
    assert per_drink == mix.final_ml / 2


def test_batch_fills_exact_bottle_volume():
    recipe = recipe_from(NEGRONI)
    mix, whole, per_drink, leftover = jigger.batch_plan(recipe, 2, 750)
    assert mix.final_ml == 1500
    assert whole == 12 and 0 <= leftover < per_drink
    assert whole * per_drink + leftover == 1500


def test_shopping_list_rounds_up_to_whole_bottles():
    recipes, _ = jigger.lint_text(MARTINI + "\n" + NEGRONI)
    rows = {r["name"]: r for r in jigger.shopping_list(recipes, [("Martini", 20)], Fraction(750))}
    gin = rows["gin"]
    assert gin["ml"] == 20 * Fraction(5, 2) * UNIT_ML["oz"]
    assert gin["bottles"] == 2
    assert gin["leftover_ml"] == 2 * 750 - gin["ml"]
    assert gin["cost"] == 2 * Fraction("28.00")


def test_shopping_list_uses_default_pack_without_price():
    recipes, _ = jigger.lint_text(NEGRONI)
    rows = {r["name"]: r for r in jigger.shopping_list(recipes, [("Negroni", 20)], Fraction(750))}
    assert rows["campari"]["pack_ml"] == 750
    assert rows["campari"]["cost"] is None


def test_plan_parser_rejects_bad_items():
    assert jigger.parse_plan("Negroni=10, Martini=6") == [("Negroni", 10), ("Martini", 6)]
    with pytest.raises(CliError):
        jigger.parse_plan("Negroni")
    with pytest.raises(CliError):
        jigger.parse_plan("Negroni=0")


def test_dilution_override_is_bounded():
    assert jigger.parse_percent("25") == Fraction(1, 4)
    with pytest.raises(CliError):
        jigger.parse_percent("60")
    with pytest.raises(CliError):
        jigger.parse_percent("wet")


def test_fmt_rounds_half_up_exactly():
    assert jigger.fmt(Fraction(5, 2), 0) == "3"
    assert jigger.fmt(Fraction(1, 8), 2) == "0.13"
    assert jigger.fmt(Fraction(2, 3), 1) == "0.7"


def test_cli_show_json(tmp_path, capsys):
    path = write(tmp_path, NEGRONI)
    assert jigger.main(["show", path, "negroni", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["recipe"] == "Negroni"
    assert data["method"] == "stir"
    assert data["dilution_pct"] == pytest.approx(24.0)
    assert data["final_ml"] == pytest.approx(3 * 29.5735 / 0.76, rel=1e-9)


def test_cli_show_table_has_proof_and_cost(tmp_path, capsys):
    path = write(tmp_path, MARTINI)
    assert jigger.main(["show", path, "Martini"]) == 0
    out = capsys.readouterr().out
    assert "ABV" in out and "proof" in out
    assert "cost" in out


def test_cli_lint_exit_codes(tmp_path, capsys):
    good = write(tmp_path, NEGRONI, "good.txt")
    bad = write(tmp_path, "recipe X\ngin nope\n", "bad.txt")
    assert jigger.main(["lint", good]) == 0
    assert "no problems" in capsys.readouterr().out
    assert jigger.main(["lint", bad]) == 1
    assert "bad.txt:2: error:" in capsys.readouterr().out


def test_cli_planning_refuses_a_file_with_errors(tmp_path, capsys):
    bad = write(tmp_path, "recipe X\ngin nope\n", "bad.txt")
    assert jigger.main(["show", bad, "X"]) == 2
    assert "run lint first" in capsys.readouterr().err


def test_cli_batch_and_shop_run_end_to_end(tmp_path, capsys):
    path = write(tmp_path, MARTINI + "\n" + NEGRONI)
    assert jigger.main(["batch", path, "Martini", "--bottles", "4", "--size", "750"]) == 0
    out = capsys.readouterr().out
    assert "whole drink" in out and "water to add" in out
    assert jigger.main(["shop", path, "--plan", "Martini=10,Negroni=5"]) == 0
    assert "bottles" in capsys.readouterr().out
