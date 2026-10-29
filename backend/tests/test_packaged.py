"""Packaged-food pipeline checks.

Real Tesseract OCR and real barcode decoding run on generated images. Open Food Facts is replaced by
an in-process fake HTTP transport (the live host isn't reachable from the test environment).
"""

import io

import httpx
import numpy as np
import pytest
import zxingcpp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.api.routes.packaged import get_off_client
from app.main import app
from app.packaged.barcode import normalize_barcode, valid_gtin
from app.packaged.ocr import extract_ingredient_section
from app.packaged.off import OpenFoodFactsClient, name_similarity
from app.seed.seed import DEMO_CUSTOMER_EMAIL, DEMO_PASSWORD

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BARCODE = "5000159407236"

LABEL_LINES = [
    "CRUNCHY OAT BISCUITS",
    "INGREDIENTS: Wholegrain oat flakes (38%),",
    "wheat flour, sugar, palm oil, butter (milk),",
    "golden syrup, raising agent (sodium",
    "bicarbonate), salt.",
    "Allergy advice: may contain hazelnuts.",
    "Nutrition information per 100g",
]

OFF_PRODUCT = {
    "code": BARCODE,
    "product_name": "Crunchy Oat Biscuits",
    "brands": "Acme Bakery",
    "quantity": "300 g",
    "ingredients_text": "Wholegrain oat flakes (38%), wheat flour, sugar, palm oil, butter (milk), "
    "golden syrup, raising agent (sodium bicarbonate), salt",
    "allergens_tags": ["en:gluten", "en:milk"],
    "traces_tags": ["en:nuts", "en:peanuts"],
    "image_front_url": "https://images.example/front.jpg",
}


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def label_image(lines=LABEL_LINES, barcode: str | None = None) -> bytes:
    font = ImageFont.truetype(FONT, 28)
    img = Image.new("RGB", (1100, 560 + (260 if barcode else 0)), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((40, 30 + i * 58), line, fill="black", font=font)
    if barcode:
        code = zxingcpp.create_barcode(barcode, zxingcpp.BarcodeFormat.EAN13)
        img.paste(Image.fromarray(np.asarray(code.to_image(scale=4))).convert("RGB"), (40, 520))
    return png(img)


def barcode_only_image(code=BARCODE) -> bytes:
    canvas = Image.new("RGB", (700, 400), "white")
    bc = zxingcpp.create_barcode(code, zxingcpp.BarcodeFormat.EAN13)
    canvas.paste(Image.fromarray(np.asarray(bc.to_image(scale=4))).convert("RGB"), (100, 80))
    return png(canvas)


class FakeOff:
    """Fake Open Food Facts HTTP API."""

    def __init__(self, products=None, search=None, fail=False):
        self.products = products if products is not None else {BARCODE: OFF_PRODUCT}
        self.search_results = search or []
        self.fail = fail
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if self.fail:
            raise httpx.ConnectError("unreachable", request=request)
        path = request.url.path
        if path.startswith("/api/v2/product/"):
            code = path.rsplit("/", 1)[-1].removesuffix(".json")
            if code in self.products:
                return httpx.Response(200, json={"status": 1, "product": self.products[code]})
            return httpx.Response(200, json={"status": 0, "status_verbose": "product not found"})
        if path == "/cgi/search.pl":
            return httpx.Response(200, json={"count": len(self.search_results), "products": self.search_results})
        return httpx.Response(404)

    def client(self) -> OpenFoodFactsClient:
        return OpenFoodFactsClient(http=httpx.Client(transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def off(client):
    fake = FakeOff()
    app.dependency_overrides[get_off_client] = fake.client
    yield fake
    app.dependency_overrides.pop(get_off_client, None)


@pytest.fixture
def customer(client):
    r = client.post("/api/auth/login", json={"email": DEMO_CUSTOMER_EMAIL, "password": DEMO_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def post(client, headers, images=(), **form):
    files = [("images", (f"p{i}.png", data, "image/png")) for i, data in enumerate(images)]
    return client.post("/api/analyze/packaged", headers=headers, files=files or None, data=form)


def mine(body):
    return {a["code"]: a["outcome"] for a in body["result"]["your_allergens"]}


# ---- unit checks ----


def test_gtin_validation():
    assert valid_gtin("5000159407236")
    assert not valid_gtin("5000159407237")
    assert normalize_barcode(" 5000-1594-07236 ") == "5000159407236"
    assert normalize_barcode("123") is None


def test_ingredient_section_extraction():
    s = extract_ingredient_section(
        "ACME\nIngredients: rice, salt.\nAllergy advice: may contain milk.\nStore in a cool dry place."
    )
    assert s.found_heading
    assert "rice, salt" in s.ingredients_text and "may contain milk" in s.ingredients_text
    assert "store" not in s.ingredients_text.lower()
    no_heading = extract_ingredient_section("Milk chocolate bar")
    assert no_heading and not no_heading.found_heading


def test_name_similarity():
    assert name_similarity("Acme Bakery Crunchy Oat Biscuits", "Acme Bakery Crunchy Oat Biscuits 300 g") > 0.8
    assert name_similarity("Acme Oat Biscuits", "Zeta Salted Crisps") < 0.4


# ---- pipeline through the API ----


def test_label_photo_only(client, customer, off):
    off.products = {}
    r = post(client, customer, [label_image()])
    assert r.status_code == 200, r.text
    body = r.json()
    # Demo customer: milk + peanuts. Label has butter (milk) and "may contain hazelnuts" (not peanuts).
    assert mine(body) == {"milk": "PRESENT", "peanuts": "NOT_LISTED_IN_VERIFIED_RECORDS"}
    assert body["label"]["heading_found"] and body["label"]["confidence"] > 0.6
    assert {a["code"] for a in body["result"]["other_allergens"]} == {"gluten", "tree_nuts"}
    assert body["product"] is None


def test_barcode_in_photo_adds_product_database(client, customer, off):
    r = post(client, customer, [label_image(barcode=BARCODE)])
    body = r.json()
    assert body["product"]["barcode"] == BARCODE
    assert body["product"]["identification_method"] == "barcode"
    assert body["product"]["confident"] is True
    # The database lists peanut traces that the label photo doesn't mention.
    assert mine(body) == {"milk": "PRESENT", "peanuts": "POSSIBLE_CROSS_CONTACT"}
    assert {s["type"] for s in body["result"]["sources"]} == {"label_ocr", "product_database"}
    assert not [c for c in body["result"]["conflicts"] if c["kind"] == "ingredient_list_mismatch"]


def test_typed_barcode_only(client, customer, off):
    off.products = {
        BARCODE: {**OFF_PRODUCT, "ingredients_text": "rice, sugar, salt", "allergens_tags": [], "traces_tags": []}
    }
    body = post(client, customer, barcode=BARCODE).json()
    assert mine(body) == {
        "milk": "NOT_LISTED_IN_VERIFIED_RECORDS",
        "peanuts": "NOT_LISTED_IN_VERIFIED_RECORDS",
    }
    assert any("community-maintained" in lim for lim in body["result"]["limitations"])


def test_barcode_not_in_database(client, customer, off):
    off.products = {}
    body = post(client, customer, barcode=BARCODE).json()
    assert mine(body) == {"milk": "UNKNOWN", "peanuts": "UNKNOWN"}
    assert any("not found in the product database" in lim for lim in body["result"]["limitations"])


def test_database_unreachable(client, customer, off):
    off.fail = True
    body = post(client, customer, [label_image()], barcode=BARCODE).json()
    assert any("could not be reached" in lim for lim in body["result"]["limitations"])
    assert mine(body)["milk"] == "PRESENT"  # the label still works
    assert [s for s in body["steps"] if s["name"] == "product_lookup"][0]["status"] == "failed"


def test_name_search_match_and_confidence(client, customer, off):
    off.products = {}
    off.search_results = [
        {**OFF_PRODUCT, "code": "1111111111116", "product_name": "Salted Crisps", "brands": "Zeta"},
        OFF_PRODUCT,
    ]
    body = post(client, customer, barcode="", product_name="Acme Bakery Crunchy Oat Biscuits").json()
    assert body["product"]["barcode"] == BARCODE
    assert body["product"]["identification_method"] == "name_search"
    assert 0.7 <= body["product"]["identity_confidence"] <= 0.9
    assert mine(body)["milk"] == "PRESENT"


def test_name_search_no_good_match(client, customer, off):
    off.products = {}
    off.search_results = [{**OFF_PRODUCT, "product_name": "Salted Crisps", "brands": "Zeta"}]
    body = post(client, customer, product_name="Acme Oat Biscuits").json()
    assert body["product"] is None
    assert mine(body) == {"milk": "UNKNOWN", "peanuts": "UNKNOWN"}


def test_blurry_photo_without_text(client, customer, off):
    off.products = {}
    blurry = Image.open(io.BytesIO(label_image())).filter(ImageFilter.GaussianBlur(12))
    body = post(client, customer, [png(blurry)]).json()
    assert any("blurry" in lim for lim in body["result"]["limitations"])
    assert body["result"]["overall_outcome"] in ("UNKNOWN", "PRESENT")
    assert "NOT_LISTED_IN_VERIFIED_RECORDS" not in mine(body).values()


def test_barcode_photo_no_label(client, customer, off):
    body = post(client, customer, [barcode_only_image()]).json()
    assert body["product"]["identification_method"] == "barcode"
    assert any("No ingredient list could be read" in lim for lim in body["result"]["limitations"])


def test_input_validation(client, customer, off):
    assert post(client, customer).status_code == 422
    r = client.post(
        "/api/analyze/packaged", headers=customer, files=[("images", ("x.png", b"not an image", "image/png"))]
    )
    assert r.status_code == 422 and "not a readable image" in r.text
    assert post(client, customer, [label_image()] * 4).status_code == 422


def test_invalid_typed_barcode_is_reported(client, customer, off):
    off.products = {}
    body = post(client, customer, [label_image()], barcode="12345").json()
    assert any("not a valid EAN/UPC" in lim for lim in body["result"]["limitations"])


def test_image_quality_checks():
    from PIL import ImageEnhance

    from app.packaged.images import load_image

    im = Image.open(io.BytesIO(label_image()))
    assert load_image(png(im), 10**8).issues == []
    assert load_image(png(im.filter(ImageFilter.GaussianBlur(4))), 10**8).issues == ["The image looks blurry."]
    assert load_image(png(ImageEnhance.Brightness(im).enhance(0.15)), 10**8).issues == ["The image is very dark."]
    assert "low contrast" in load_image(png(ImageEnhance.Contrast(im).enhance(0.2)), 10**8).issues[0]
    assert "resolution is low" in load_image(png(im.resize((200, 100))), 10**8).issues[0]
