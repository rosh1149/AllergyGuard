from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.api.deps import DB, CurrentCustomer
from app.api.routes.packaged import load_uploads
from app.restaurant.pipeline import RestaurantAnalysisOut, run_restaurant_pipeline

router = APIRouter(prefix="/analyze", tags=["analysis"])


@router.post("/restaurant", response_model=RestaurantAnalysisOut)
def analyze_restaurant(
    user: CurrentCustomer,
    db: DB,
    images: Annotated[list[UploadFile] | None, File(description="Up to 3 photos of the dish")] = None,
    restaurant_id: Annotated[int | None, Form()] = None,
    branch_id: Annotated[int | None, Form()] = None,
    menu_item_id: Annotated[int | None, Form()] = None,
    restaurant_name: Annotated[str, Form(max_length=200)] = "",
    location: Annotated[str, Form(max_length=200)] = "",
    dish_name: Annotated[str, Form(max_length=200)] = "",
) -> RestaurantAnalysisOut:
    """Analyze a restaurant dish against the customer's saved allergies.

    Registered restaurant: pass restaurant_id (+ branch_id if it has several branches) and a dish
    photo, dish name or menu_item_id. Unregistered: pass restaurant_name (+ location) and a dish
    name or photo; public recipes are then used as unverified candidates only.
    """
    images = [f for f in images or [] if f.filename]
    if restaurant_id is None and not restaurant_name.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a restaurant or enter its name.")
    if not images and not dish_name.strip() and menu_item_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Upload a dish photo or enter the dish name.")
    loaded = load_uploads(images)
    return run_restaurant_pipeline(
        db, user,
        restaurant_id=restaurant_id, branch_id=branch_id, restaurant_name=restaurant_name, location=location,
        dish_name=dish_name, menu_item_id=menu_item_id, images=loaded,
    )
