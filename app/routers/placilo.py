from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_login
from ..database import get_db
from ..models import Clan
from ..placilo import (
    generiraj_epc_png,
    generiraj_epc_svg,
    napake_placila,
    pripravi_placilo,
)


router = APIRouter(prefix="/placilo")
templates = Jinja2Templates(directory="app/templates")


def _nalozi(
    db: Session,
    clan_id: int,
    leto: int,
):
    clan = db.query(Clan).filter(Clan.id == clan_id).first()
    if not clan:
        return None, None
    return clan, pripravi_placilo(db, clan, leto)


@router.get("/{clan_id}/{leto}", response_class=HTMLResponse)
async def predogled(
    request: Request,
    clan_id: int,
    leto: int,
    db: Session = Depends(get_db),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    clan, podatki = _nalozi(db, clan_id, leto)
    if not clan:
        return RedirectResponse(url="/clani", status_code=302)

    return templates.TemplateResponse(
        request,
        "placilo/predogled.html",
        {
            "request": request,
            "user": user,
            "clan": clan,
            "leto": leto,
            "placilo": podatki,
            "napake": napake_placila(podatki),
        },
    )


@router.get("/{clan_id}/{leto}/sepa.svg")
async def sepa_svg(
    request: Request,
    clan_id: int,
    leto: int,
    db: Session = Depends(get_db),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    clan, podatki = _nalozi(db, clan_id, leto)
    if not clan:
        return Response(content=b"", status_code=404)

    try:
        svg = generiraj_epc_svg(podatki)
    except ValueError as exc:
        return Response(
            content=str(exc),
            status_code=422,
            media_type="text/plain; charset=utf-8",
        )

    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{clan_id}/{leto}/sepa.png")
async def sepa_png(
    request: Request,
    clan_id: int,
    leto: int,
    db: Session = Depends(get_db),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    clan, podatki = _nalozi(db, clan_id, leto)
    if not clan:
        return Response(content=b"", status_code=404)

    try:
        png = generiraj_epc_png(podatki)
    except ValueError as exc:
        return Response(
            content=str(exc),
            status_code=422,
            media_type="text/plain; charset=utf-8",
        )

    oznaka = (
        clan.klicni_znak
        or str(clan.es_stevilka or clan.id)
    )
    filename = f"SEPA_{oznaka}_{leto}.png"

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
