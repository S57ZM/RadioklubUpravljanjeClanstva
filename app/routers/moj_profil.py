import re
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit_log import log_akcija
from ..auth import require_login
from ..config import get_nastavitev
from ..csrf import csrf_protect, get_csrf_token
from ..database import get_db
from ..models import Clan, Uporabnik, ZrsClanarina


router = APIRouter(prefix="/moj-profil")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["csrf_token"] = get_csrf_token

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_zneski(raw: str) -> dict[str, float]:
    rezultat: dict[str, float] = {}
    for vrstica in (raw or "").splitlines():
        if "=" not in vrstica:
            continue
        naziv, vrednost = vrstica.split("=", 1)
        try:
            rezultat[naziv.strip()] = float(
                vrednost.strip().replace(",", ".")
            )
        except ValueError:
            continue
    return rezultat


def _zrs_zneski(db: Session) -> dict[str, float]:
    return _parse_zneski(
        get_nastavitev(
            db,
            "zrs_clanarina_zneski",
            (
                "Brez ZRS=0.00\n"
                "Redni član=40.00\n"
                "Družinski član=20.00\n"
                "Operater invalid=20.00\n"
                "Mladi do 18 let=20.00"
            ),
        )
    )


def _klub_znesek(db: Session, tip_clanstva: str) -> float:
    zneski = _parse_zneski(
        get_nastavitev(db, "clanarina_zneski", "")
    )
    return zneski.get(tip_clanstva, 0.0)


def _operaterski_razredi(db: Session) -> list[str]:
    raw = get_nastavitev(
        db,
        "operaterski_razredi",
        "A\nN\nA - CW\nN - CW",
    )
    return [v.strip() for v in raw.splitlines() if v.strip()]


def _uporabnik_in_clan(
    request: Request,
    db: Session,
) -> tuple[dict | None, Uporabnik | None, Clan | None, Response | None]:
    user, redirect = require_login(request)
    if redirect:
        return None, None, None, redirect

    uporabnik = (
        db.query(Uporabnik)
        .filter(Uporabnik.id == user["id"])
        .first()
    )
    if not uporabnik:
        request.session.clear()
        return user, None, None, RedirectResponse(
            url="/login",
            status_code=302,
        )

    if not uporabnik.clan_id:
        return user, uporabnik, None, RedirectResponse(
            url="/profil",
            status_code=302,
        )

    clan = (
        db.query(Clan)
        .filter(Clan.id == uporabnik.clan_id)
        .first()
    )
    if not clan:
        return user, uporabnik, None, RedirectResponse(
            url="/profil",
            status_code=302,
        )

    return user, uporabnik, clan, None


@router.get("", response_class=HTMLResponse)
async def moj_profil(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    user, uporabnik, clan, redirect = _uporabnik_in_clan(request, db)
    if redirect:
        return redirect

    leto = date.today().year
    zrs = (
        db.query(ZrsClanarina)
        .filter(
            ZrsClanarina.clan_id == clan.id,
            ZrsClanarina.leto == leto,
        )
        .first()
    )

    return templates.TemplateResponse(
        request,
        "moj_profil/index.html",
        {
            "request": request,
            "user": user,
            "uporabnik": uporabnik,
            "clan": clan,
            "zrs": zrs,
            "leto": leto,
            "zrs_zneski": _zrs_zneski(db),
            "operaterski_razredi": _operaterski_razredi(db),
            "shranjeno": request.query_params.get("shranjeno") == "1",
        },
    )


@router.post("", response_class=HTMLResponse)
async def moj_profil_shrani(
    request: Request,
    ime: str = Form(...),
    priimek: str = Form(...),
    klicni_znak: str = Form(""),
    elektronska_posta: str = Form(...),
    naslov_ulica: str = Form(""),
    naslov_posta: str = Form(""),
    mobilni_telefon: str = Form(""),
    telefon_doma: str = Form(""),
    operaterski_razred: str = Form(""),
    zrs_vrsta: str = Form("Brez ZRS"),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> Response:
    user, uporabnik, clan, redirect = _uporabnik_in_clan(request, db)
    if redirect:
        return redirect

    ime = ime.strip().title()
    priimek = priimek.strip().title()
    email = elektronska_posta.strip().lower()
    kz = klicni_znak.strip().upper()

    napaka = None
    if not ime or not priimek:
        napaka = "Ime in priimek sta obvezna."
    elif not _EMAIL_RE.fullmatch(email):
        napaka = "Vnesite veljaven e-poštni naslov."
    elif zrs_vrsta not in _zrs_zneski(db):
        napaka = "Izbrana vrsta ZRS članarine ni veljavna."

    email_drugi = (
        db.query(Clan)
        .filter(
            Clan.id != clan.id,
            func.lower(Clan.elektronska_posta) == email,
        )
        .first()
    )
    if not napaka and email_drugi:
        napaka = "Ta e-poštni naslov uporablja drug član."

    if kz:
        kz_drugi = (
            db.query(Clan)
            .filter(
                Clan.id != clan.id,
                func.upper(Clan.klicni_znak) == kz,
            )
            .first()
        )
        if not napaka and kz_drugi:
            napaka = "Ta klicni znak uporablja drug član."

    if napaka:
        leto = date.today().year
        zrs = (
            db.query(ZrsClanarina)
            .filter(
                ZrsClanarina.clan_id == clan.id,
                ZrsClanarina.leto == leto,
            )
            .first()
        )
        return templates.TemplateResponse(
            request,
            "moj_profil/index.html",
            {
                "request": request,
                "user": user,
                "uporabnik": uporabnik,
                "clan": clan,
                "zrs": zrs,
                "leto": leto,
                "zrs_zneski": _zrs_zneski(db),
                "operaterski_razredi": _operaterski_razredi(db),
                "napaka": napaka,
            },
            status_code=400,
        )

    clan.ime = ime
    clan.priimek = priimek
    clan.klicni_znak = kz or None
    clan.elektronska_posta = email
    clan.naslov_ulica = naslov_ulica.strip() or None
    clan.naslov_posta = naslov_posta.strip() or None
    clan.mobilni_telefon = mobilni_telefon.strip() or None
    clan.telefon_doma = telefon_doma.strip() or None
    clan.operaterski_razred = operaterski_razred.strip() or None

    leto = date.today().year
    zrs = (
        db.query(ZrsClanarina)
        .filter(
            ZrsClanarina.clan_id == clan.id,
            ZrsClanarina.leto == leto,
        )
        .first()
    )
    zrs_zneski = _zrs_zneski(db)

    if not zrs:
        zrs = ZrsClanarina(
            clan_id=clan.id,
            leto=leto,
            zrs_vrsta=zrs_vrsta,
            klub_znesek=_klub_znesek(db, clan.tip_clanstva),
            zrs_znesek=zrs_zneski.get(zrs_vrsta, 0.0),
            zrs_nakazano=False,
        )
        db.add(zrs)
    else:
        zrs.zrs_vrsta = zrs_vrsta
        zrs.zrs_znesek = zrs_zneski.get(zrs_vrsta, 0.0)
        if zrs.klub_znesek is None:
            zrs.klub_znesek = _klub_znesek(db, clan.tip_clanstva)

    uporabnik.ime_priimek = f"{ime} {priimek}".strip()
    request.session["uporabnik"]["ime"] = uporabnik.ime_priimek

    db.commit()

    log_akcija(
        db,
        user.get("uporabnisko_ime"),
        "moj_profil_urejen",
        f"Član je uredil svoj zapis ID {clan.id}",
        ip=request.client.host if request.client else None,
    )

    return RedirectResponse(
        url="/moj-profil?shranjeno=1",
        status_code=302,
    )