import html
from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit_log import log_akcija
from ..auth import is_editor, require_login
from ..csrf import csrf_protect
from ..database import get_db
from ..email import get_smtp_nastavitve, posli_email
from ..models import Clan, Clanarina
from ..placilo import pripravi_placilo

router = APIRouter(prefix="/placilo")


def _nazaj(clan_id: int) -> str:
    return f"/clani/{clan_id}#zrs-izbira-clana"


def _zapis(db: Session, clan_id: int, leto: int) -> Clanarina:
    zapis = (
        db.query(Clanarina)
        .filter(
            Clanarina.clan_id == clan_id,
            Clanarina.leto == leto,
        )
        .first()
    )

    if zapis is None:
        zapis = Clanarina(
            clan_id=clan_id,
            leto=leto,
            poziv_status="ni_poslano",
            poziv_poskusi=0,
        )
        db.add(zapis)
        db.flush()

    return zapis


@router.post("/{clan_id}/{leto}/poslji-poziv")
async def poslji_poziv(
    request: Request,
    clan_id: int,
    leto: int,
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> RedirectResponse:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    if not is_editor(user):
        return RedirectResponse(_nazaj(clan_id), status_code=302)

    clan = db.query(Clan).filter(Clan.id == clan_id).first()
    if not clan:
        return RedirectResponse("/clani", status_code=302)

    email = (clan.elektronska_posta or "").strip()
    if not email:
        request.session["flash_warning"] = (
            "Član nima vpisanega e-poštnega naslova."
        )
        return RedirectResponse(_nazaj(clan_id), status_code=302)

    zapis = _zapis(db, clan_id, leto)
    zapis.poziv_poskusi = int(zapis.poziv_poskusi or 0) + 1
    zapis.poziv_email = email

    placilo = pripravi_placilo(db, clan, leto)
    znesek = f"{float(placilo.skupaj):.2f}".replace(".", ",")

    zadeva = f"Plačilni poziv za članarino {leto}"
    telo = f"""
<p>Spoštovani {html.escape(clan.ime or '')}
{html.escape(clan.priimek or '')},</p>

<p>Pošiljamo vam plačilni poziv za članarino
za leto <strong>{leto}</strong>.</p>

<p>Znesek za plačilo:
<strong>{znesek} €</strong></p>

<p>{{{{ qr_koda }}}}</p>

<p>Lep pozdrav,<br>Radioklub Sevnica S50TTT</p>
"""

    try:
        posli_email(
            clan=clan,
            zadeva_predloga=zadeva,
            telo_predloga=telo,
            leto=leto,
            smtp_nastavitve=get_smtp_nastavitve(db),
            db=db,
            vkljuci_qr=True,
        )
    except Exception as exc:
        zapis.poziv_status = "napaka"
        zapis.poziv_napaka = str(exc)[:1000]
        db.commit()
        request.session["flash_warning"] = (
            f"Poziv ni bil poslan: {exc}"
        )
        akcija = "placilni_poziv_napaka"
    else:
        zapis.poziv_status = "poslano"
        zapis.poziv_poslan_ob = datetime.now()
        zapis.poziv_napaka = None
        if not zapis.znesek:
            zapis.znesek = znesek
        db.commit()
        request.session["flash_success"] = (
            f"Poziv je bil poslan na {email}."
        )
        akcija = "placilni_poziv_poslan"

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        user.get("uporabnisko_ime") if user else None,
        akcija,
        f"clan_id={clan_id}, leto={leto}, email={email}",
        ip=ip,
    )

    return RedirectResponse(_nazaj(clan_id), status_code=302)


@router.post("/{clan_id}/{leto}/placano-danes")
async def placano_danes(
    request: Request,
    clan_id: int,
    leto: int,
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> RedirectResponse:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    if not is_editor(user):
        return RedirectResponse(_nazaj(clan_id), status_code=302)

    clan = db.query(Clan).filter(Clan.id == clan_id).first()
    if not clan:
        return RedirectResponse("/clani", status_code=302)

    zapis = _zapis(db, clan_id, leto)
    placilo = pripravi_placilo(db, clan, leto)

    zapis.datum_placila = date.today()

    if not zapis.znesek:
        zapis.znesek = (
            f"{float(placilo.skupaj):.2f}"
            .replace(".", ",")
        )

    if not zapis.opombe:
        zapis.opombe = "Plačana članarina"

    db.commit()

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        user.get("uporabnisko_ime") if user else None,
        "clanarina_placana",
        f"clan_id={clan_id}, leto={leto}",
        ip=ip,
    )

    request.session["flash_success"] = (
        f"Članarina za leto {leto} je označena kot plačana."
    )

    return RedirectResponse(_nazaj(clan_id), status_code=302)


@router.post("/{clan_id}/{leto}/placano-danes")
async def placano_danes(
    request: Request,
    clan_id: int,
    leto: int,
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> RedirectResponse:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    if not is_editor(user):
        return RedirectResponse(_nazaj(clan_id), status_code=302)

    clan = db.query(Clan).filter(Clan.id == clan_id).first()
    if not clan:
        return RedirectResponse("/clani", status_code=302)

    zapis = _zapis(db, clan_id, leto)
    placilo = pripravi_placilo(db, clan, leto)

    zapis.datum_placila = date.today()

    if not zapis.znesek:
        zapis.znesek = (
            f"{float(placilo.skupaj):.2f}"
            .replace(".", ",")
        )

    if not zapis.opombe:
        zapis.opombe = "Plačana članarina"

    db.commit()

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        user.get("uporabnisko_ime") if user else None,
        "clanarina_placana",
        f"clan_id={clan_id}, leto={leto}",
        ip=ip,
    )

    request.session["flash_success"] = (
        f"Članarina za leto {leto} je označena kot plačana."
    )

    return RedirectResponse(_nazaj(clan_id), status_code=302)
