import os
from typing import Optional

import httpx
from fastapi import Header, HTTPException

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Vérifie le token Supabase envoyé par le frontend et retourne l'utilisateur authentifié."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentification requise.")

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_URL / SUPABASE_ANON_KEY manquants dans les variables d'environnement du backend.",
        )

    token = authorization.split(" ", 1)[1].strip()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON_KEY},
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Impossible de vérifier l'authentification (Supabase injoignable).")

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Session invalide ou expirée.")

    return response.json()
