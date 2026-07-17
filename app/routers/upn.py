from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from ..auth import require_login
from ..database import get_db
from ..models import Clan
from ..placilo import pripravi_placilo
from ..upn import generiraj_upn_png, generiraj_upn_svg


router = APIRouter(prefix="/upn")


def _upn_argumenti(podatki) -> dict:
    return {
        "ime_placnika": podatki.placnik,
        "ulica_placnika": podatki.ulica_placnika,
        "kraj_placnika": podatki.kraj_placnika,
        "iban_prejemnika": podatki.iban,
        "referenca": podatki.referenca,
        "ime_prejemnika": podatki.prejemnik,
        "ulica_prejemnika": podatki.ulica_prejemnika,
        "kraj_prejemnika": podatki.kraj_prejemnika,
        "opis": podatki.opis,
        "znesek_eur": podatki.skupaj,
        "namen": podatki.namen,
    }


@router.get("/{clan_id}/{leto}")
async def upn_qr(
    request: Request,
    clan_id: int,
    leto: int,
    db: Session = Depends(get_db),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    clan = db.query(Clan).filter(Clan.id == clan_id).first()
    if not clan:
        return RedirectResponse(url="/clani", status_code=302)

    podatki = pripravi_placilo(db, clan, leto)
    svg = generiraj_upn_svg(**_upn_argumenti(podatki))
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{clan_id}/{leto}/png")
async def upn_qr_png(
    request: Request,
    clan_id: int,
    leto: int,
    db: Session = Depends(get_db),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    clan = db.query(Clan).filter(Clan.id == clan_id).first()
    if not clan:
        return Response(content=b"", status_code=404)

    podatki = pripravi_placilo(db, clan, leto)
    png = generiraj_upn_png(**_upn_argumenti(podatki))

    oznaka = (
        clan.klicni_znak
        or str(clan.es_stevilka or clan.id)
    )
    filename = f"UPN_{oznaka}_{leto}.png"

    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
            "Cache-Control": "no-store",
        },
    )
