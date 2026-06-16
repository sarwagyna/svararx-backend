"""
GET /api/v1/drugs/search?q={}  — fuzzy drug lookup
"""
from fastapi import APIRouter, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from rapidfuzz import process, fuzz

from app.database import get_db
from app.models import Drug
from app.schemas import DrugResult
from app.config import get_settings, Settings
from app.core.dependencies import get_current_doctor

router = APIRouter()


@router.get("/drugs/search", response_model=list[DrugResult])
async def search_drugs(
    q: str = Query(min_length=1, description="Drug name query"),
    limit: int = Query(default=10, le=50),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _doctor=Depends(get_current_doctor),
):
    """
    Fuzzy search drugs by brand or generic name.
    Returns results sorted by match score descending.
    """
    result = await db.execute(select(Drug))
    all_drugs = result.scalars().all()

    if not all_drugs:
        return []

    # Build lookup: display_name → Drug object
    drug_map: dict[str, Drug] = {}
    for drug in all_drugs:
        drug_map[drug.brand_name.upper()] = drug
        # Also index generic name
        generic_key = f"{drug.generic_name.upper()}__generic__{drug.id}"
        drug_map[generic_key] = drug

    choices = list(drug_map.keys())
    matches = process.extract(
        q.upper(),
        choices,
        scorer=fuzz.WRatio,
        limit=limit * 2,  # Over-fetch, deduplicate by drug id
        score_cutoff=settings.drug_match_threshold,
    )

    seen_ids: set[str] = set()
    results: list[DrugResult] = []

    for match_name, score, _ in matches:
        drug = drug_map[match_name]
        if drug.id in seen_ids:
            continue
        seen_ids.add(drug.id)
        results.append(
            DrugResult(
                id=drug.id,
                brand_name=drug.brand_name,
                generic_name=drug.generic_name,
                category=drug.category,
                score=round(score, 1),
            )
        )
        if len(results) >= limit:
            break

    return results
