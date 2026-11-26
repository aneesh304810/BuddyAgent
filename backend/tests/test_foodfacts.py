"""Food, supplement and medicine facts: only the words typed go out, the numbers are worked out by code, and a
supplement's label is set against the adult upper limits."""

import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from buddy import foodfacts
from buddy.memory import Memory
from buddy.tools import ToolError

KEY = {"BUDDY_USDA_KEY": "k-123"}
FOODS = {"foods": [
    {"fdcId": 9, "description": "IDLI RICE CAKES", "dataType": "Branded", "brandOwner": "Some Foods Inc.",
     "servingSize": 100, "servingSizeUnit": "g", "householdServingFullText": "2 pieces",
     "foodNutrients": [{"nutrientId": 1008, "unitName": "KCAL", "value": 150}, {"nutrientId": 1003, "value": 5}]},
    {"fdcId": 2705433, "description": "Idli", "dataType": "Survey (FNDDS)", "foodNutrients": [
        {"nutrientId": 1062, "unitName": "kJ", "value": 552.0}, {"nutrientId": 1008, "unitName": "KCAL", "value": 132.0},
        {"nutrientId": 1003, "unitName": "G", "value": 4.3}, {"nutrientId": 1093, "unitName": "MG", "value": 210}],
     "foodMeasures": [{"disseminationText": "1 idli", "gramWeight": 39.0},
                      {"disseminationText": "Quantity not specified", "gramWeight": 78}]},
    {"fdcId": 3, "description": "Water", "dataType": "Foundation", "foodNutrients": []},
]}
HITS = {"hits": [
    {"_id": "175207", "_source": {"fullName": "Vitamin D3 2000 IU", "brandName": "Old Brand", "offMarket": 1,
                                  "allIngredients": [{"name": "Vitamin D"}]}},
    {"_id": "62677", "_source": {"fullName": "Extra Strength Vitamin D3 2000 IU", "brandName": "Kirkland Signature",
                                 "offMarket": 0, "allIngredients": [{"name": "Vitamin D3"}, {"name": "Gelatin"},
                                                                    {"name": "Vitamin D3"}]}},
    {"_id": "not-a-number", "_source": {"fullName": "Odd"}},
]}


def _q(amount, unit, dv=None):
    return [{"servingSizeOrder": 1, "quantity": amount, "unit": unit,
             "dailyValueTargetGroup": [{"percent": dv}] if dv is not None else []}]


LABEL = {"fullName": "Big D and Minerals", "brandName": "Test", "offMarket": 0,
         "servingSizes": [{"minQuantity": 1, "maxQuantity": 1, "unit": "Tablet(s)", "maxDailyServings": 2}],
         "ingredientRows": [
             {"name": "Calories", "quantity": _q(5, "{Calories}")},
             {"name": "Vitamin D", "quantity": _q(5000, "IU", 625)},       # 2 a day: 10,000 IU = 250 mcg, 250%
             {"name": "Copper", "quantity": _q(2, "mg", 222)},             # 4 mg against 10 mg: 40%
             {"name": "Vitamin E", "quantity": _q(30, "IU", 100)},          # IU can't be set against mg: no share
             {"name": "Ashwagandha", "quantity": _q(300, "mg")}]}


class Fake:
    def __init__(self, body):
        self.body, self.calls = body, []

    def __call__(self, url, data=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        return json.dumps(self.body)


def test_foods_need_the_owners_key_and_send_only_the_words(tmp_path):
    m = Memory(tmp_path / "b.db")
    with pytest.raises(ToolError, match="set.sh BUDDY_USDA_KEY"):
        foodfacts.foods(m, "idli", env={})
    fake = Fake(FOODS)
    got = foodfacts.foods(m, "idli <script>", env=KEY, fetch=fake)
    url, headers = fake.calls[0]
    assert url.startswith("https://api.nal.usda.gov/fdc/v1/foods/search?") and headers["X-Api-Key"] == "k-123"
    assert urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"] == ["idli script"]
    assert [f["kind"] for f in got] == ["As people eat it", "Branded product"]  # water has no numbers: left out
    idli, cakes = got
    assert idli["per100"] == {"kcal": 132.0, "protein_g": 4.3, "sodium_mg": 210.0}  # the kJ figure is ignored
    assert idli["portions"] == [{"label": "1 idli", "grams": 39.0}]
    assert cakes["portions"][0] == {"label": "2 pieces (label serving)", "grams": 100.0} and cakes["brand"]
    assert foodfacts.portion(idli, 78) == {"kcal": 103, "protein_g": 3.4, "sodium_mg": 163.8}
    with pytest.raises(ToolError):
        foodfacts.portion(idli, 0)
    with pytest.raises(ToolError, match="Type a food"):
        foodfacts.foods(m, " ! ", env=KEY, fetch=fake)


def test_supplements_list_products_sold_now_first(tmp_path):
    foodfacts.CACHE.clear()
    got = foodfacts.supplements(Memory(tmp_path / "b.db"), "vitamin d3", fetch=Fake(HITS))
    assert [p["id"] for p in got] == ["62677", "175207"] and got[1]["off_market"]
    assert got[0]["ingredients"] == ["Vitamin D3", "Gelatin"]


def test_a_label_against_the_adult_upper_limits(tmp_path):
    foodfacts.CACHE.clear()
    fake = Fake(LABEL)
    lab = foodfacts.label(Memory(tmp_path / "b.db"), "555", fetch=fake)
    assert fake.calls[0][0] == "https://api.ods.od.nih.gov/dsld/v9/label/555"
    rows = {r["name"]: r for r in lab["rows"]}
    assert "Calories" not in rows and lab["serving"] == "1 Tablet(s)" and lab["most_servings"] == 2
    assert rows["Vitamin D"]["upper_pct"] == 250 and rows["Vitamin D"]["daily_value_pct"] == 625
    assert rows["Vitamin D"]["fact_sheet"] == "https://ods.od.nih.gov/factsheets/VitaminD-Consumer/"
    assert rows["Copper"]["upper_pct"] == 40
    assert "upper_pct" not in rows["Vitamin E"] and "upper_pct" not in rows["Ashwagandha"]
    assert "Vitamin D would be above the adult upper limit" in lab["warning"]
    assert lab["page"] == "https://dsld.od.nih.gov/label/555"
    with pytest.raises(ToolError, match="label number"):
        foodfacts.label(Memory(tmp_path / "b.db"), "../etc", fetch=fake)


def test_medicines_come_from_the_fda_label(tmp_path):
    foodfacts.CACHE.clear()

    def fake(url, data=None, headers=None):
        if "/drug/label.json" in url:
            return json.dumps({"results": [{"openfda": {"brand_name": ["Glucophage"], "generic_name": ["METFORMIN"]},
                                            "indications_and_usage": ["For type 2 diabetes."]}]})
        return json.dumps({"results": []})
    got = foodfacts.medicine(Memory(tmp_path / "b.db"), "metformin", fetch=fake)
    assert got["label"]["name"] == "Glucophage" and got["label"]["used_for"] == "For type 2 diabetes."
    assert got["recalls"] == [] and "not endorsed by the FDA" in got["note"]


def test_the_page_endpoint(tmp_path, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", Memory(tmp_path / "b.db"))
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    monkeypatch.setattr(foodfacts, "supplements", lambda memory, q: [{"id": "1", "name": q}])
    monkeypatch.delenv("BUDDY_USDA_KEY", raising=False)
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    assert client.get("/api/health/facts?kind=supplement&q=zinc", headers=auth).json()["products"][0]["name"] == "zinc"
    missing = client.get("/api/health/facts?kind=food&q=idli", headers=auth)
    assert missing.status_code == 400 and "BUDDY_USDA_KEY" in missing.json()["detail"]
    assert client.get("/api/health/facts?kind=nope&q=x", headers=auth).status_code == 400
