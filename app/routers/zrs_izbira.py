from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..audit_log import log_akcija
from ..auth import is_editor, require_login
from ..config import get_clanarina_zneski
from ..csrf import csrf_protect
from ..database import get_db
from ..models import Clan, ZrsClanarina


router = APIRouter(prefix="/placilo")

ZRS_CENE: dict[str, float] = {
    "Brez ZRS": 0.00,
    "Redni član": 40.00,
    "Družinski član": 20.00,
    "Operater invalid": 20.00,
    "Mladi do 18 let": 20.00,
}


def _nalozi_clana(db: Session, clan_id: int) -> Clan | None:
    return db.query(Clan).filter(Clan.id == clan_id).first()


def _klubski_znesek(db: Session, clan: Clan) -> float:
    cene = get_clanarina_zneski(db)
    return float(cene.get(clan.tip_clanstva, 0.0) or 0.0)


@router.get("/{clan_id}/{leto}/zrs-stanje")
async def zrs_stanje(
    request: Request,
    clan_id: int,
    leto: int,
    db: Session = Depends(get_db),
) -> JSONResponse:
    user, redirect = require_login(request)
    if redirect:
        return JSONResponse(
            {"ok": False, "napaka": "Prijava je potekla."},
            status_code=401,
        )

    if not is_editor(user):
        return JSONResponse(
            {"ok": False, "napaka": "Dostop ni dovoljen."},
            status_code=403,
        )

    clan = _nalozi_clana(db, clan_id)
    if not clan:
        return JSONResponse(
            {"ok": False, "napaka": "Član ni najden."},
            status_code=404,
        )

    evidenca = (
        db.query(ZrsClanarina)
        .filter(
            ZrsClanarina.clan_id == clan_id,
            ZrsClanarina.leto == leto,
        )
        .first()
    )

    izbrana = (
        evidenca.zrs_vrsta
        if evidenca and evidenca.zrs_vrsta in ZRS_CENE
        else "Brez ZRS"
    )
    klub = _klubski_znesek(db, clan)
    zrs = float(ZRS_CENE[izbrana])

    return JSONResponse(
        {
            "ok": True,
            "izbrana": izbrana,
            "klub_znesek": round(klub, 2),
            "zrs_znesek": round(zrs, 2),
            "skupaj": round(klub + zrs, 2),
            "cene": ZRS_CENE,
        }
    )


@router.post("/{clan_id}/{leto}/zrs")
async def shrani_zrs_izbiro(
    request: Request,
    clan_id: int,
    leto: int,
    zrs_vrsta: str = Form("Brez ZRS"),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> RedirectResponse:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    if not is_editor(user):
        return RedirectResponse(
            url=f"/clani/{clan_id}",
            status_code=302,
        )

    if not 2000 <= leto <= 2100:
        request.session["flash_warning"] = "Neveljavno leto."
        return RedirectResponse(
            url=f"/clani/{clan_id}",
            status_code=302,
        )

    clan = _nalozi_clana(db, clan_id)
    if not clan:
        return RedirectResponse(url="/clani", status_code=302)

    if zrs_vrsta not in ZRS_CENE:
        zrs_vrsta = "Brez ZRS"

    klub_znesek = _klubski_znesek(db, clan)
    zrs_znesek = float(ZRS_CENE[zrs_vrsta])

    evidenca = (
        db.query(ZrsClanarina)
        .filter(
            ZrsClanarina.clan_id == clan_id,
            ZrsClanarina.leto == leto,
        )
        .first()
    )

    if evidenca is None:
        evidenca = ZrsClanarina(
            clan_id=clan_id,
            leto=leto,
            zrs_vrsta=zrs_vrsta,
            klub_znesek=klub_znesek,
            zrs_znesek=zrs_znesek,
        )
        db.add(evidenca)
    else:
        evidenca.zrs_vrsta = zrs_vrsta
        evidenca.klub_znesek = klub_znesek
        evidenca.zrs_znesek = zrs_znesek

    db.commit()

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        user.get("uporabnisko_ime") if user else None,
        "zrs_izbira_placilo",
        (
            f"clan_id={clan_id}, leto={leto}, "
            f"zrs_vrsta={zrs_vrsta}, "
            f"klub={klub_znesek:.2f}, "
            f"zrs={zrs_znesek:.2f}"
        ),
        ip=ip,
    )

    request.session["flash_success"] = (
        f"ZRS članarina za leto {leto} je shranjena: "
        f"{zrs_vrsta} ({zrs_znesek:.2f} €)."
    )

    return RedirectResponse(
        url=f"/placilo/{clan_id}/{leto}?zrs=shranjen",
        status_code=302,
    )
