from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import DB, CurrentCustomer
from app.core.config import get_settings
from app.packaged.images import ImageError, load_image
from app.packaged.off import OpenFoodFactsClient
from app.packaged.pipeline import PackagedAnalysisOut, run_packaged_pipeline

router = APIRouter(prefix="/analyze", tags=["analysis"])

MAX_IMAGES = 3


def get_off_client() -> OpenFoodFactsClient | None:
    return OpenFoodFactsClient()


@router.post("/packaged", response_model=PackagedAnalysisOut)
def analyze_packaged(
    user: CurrentCustomer,
    db: DB,
    off: Annotated[OpenFoodFactsClient | None, Depends(get_off_client)],
    images: Annotated[list[UploadFile] | None, File(description="1-3 photos: front of pack and/or ingredient label")] = None,
    barcode: Annotated[str, Form(max_length=20)] = "",
    product_name: Annotated[str, Form(max_length=200)] = "",
) -> PackagedAnalysisOut:
    """Analyze a packaged food from photos (and/or a typed barcode) against the customer's allergies."""
    images = [f for f in images or [] if f.filename]
    if not images and not barcode.strip() and not product_name.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Upload at least one photo, or enter a barcode or product name."
        )
    if len(images) > MAX_IMAGES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Upload at most {MAX_IMAGES} photos.")

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    loaded = []
    for i, f in enumerate(images, 1):
        data = f.file.read(max_bytes + 1)
        try:
            loaded.append(load_image(data, max_bytes))
        except ImageError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Photo {i}: {e}")

    return run_packaged_pipeline(db, user, loaded, barcode=barcode, product_name=product_name, off=off)
