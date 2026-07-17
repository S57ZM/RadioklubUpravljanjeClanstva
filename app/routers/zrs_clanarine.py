from datetime import date

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Clan, Clanarina, ZrsClanarina
from ..auth import require_login, is_editor, is_admin
from ..config import get_clanarina_zneski, get_nastavitev
from ..csrf import get_csrf_token, csrf_protect
from ..audit_log import log_akcija


router = APIRouter(prefix="/zrs-clanarine")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["csrf_token"] = get_csrf_token


ZRS_PRIVZETO = (
    "Brez ZRS=0.00\n"
    "Redni ÄŤlan=40.00\n"
    "DruĹľinski ÄŤlan=20.00\n"
    "Operater invalid=20.00\n"
    "Mladi do 18 let=20.00"
)


def _zrs_zneski(db: Session) -> dict[str, float]:
    vrednost = get_nastavitev(db, "zrs_clanarina_zneski", ZRS_PRIVZETO)
    rezultat: dict[str, float] = {}

    for vrstica in vrednost.splitlines():
        if "=" not in vrstica:
            continue
        naziv, znesek = vrstica.split("=", 1)
        try:
            rezultat[naziv.strip()] = float(znesek.strip().replace(",", "."))
        except ValueError:
            continue

    if "Brez ZRS" not in rezultat:
        rezultat = {"Brez ZRS": 0.0, **rezultat}

    return rezultat


def _datum(vrednost: str) -> date | None:
    vrednost = (vrednost or "").strip()
    if not vrednost:
        return None
    return date.fromisoformat(vrednost)


@router.get("", response_class=HTMLResponse)
async def seznam(
    request: Request,
    leto: int | None = None,
    db: Session = Depends(get_db),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect

    izbrano_leto = leto or date.today().year
    clani = (
        db.query(Clan)
        .filter(Clan.aktiven == True)
        .order_by(Clan.priimek, Clan.ime)
        .all()
    )
    evidence = {
        evidenca.clan_id: evidenca
        for evidenca in (
            db.query(ZrsClanarina)
            .filter(ZrsClanarina.leto == izbrano_leto)
            .all()
        )
    }

    klub_zneski = get_clanarina_zneski(db)
    zrs_zneski = _zrs_zneski(db)

    vrstice = []
    skupaj_klub = 0.0
    skupaj_zrs = 0.0
    skupaj_zrs_nakazano = 0.0
    stevilo_placanih = 0

    for clan in clani:
        evidenca = evidence.get(clan.id)
        klub_znesek = (
            float(evidenca.klub_znesek or 0.0)
            if evidenca
            else float(klub_zneski.get(clan.tip_clanstva, 0.0) or 0.0)
        )
        zrs_vrsta = evidenca.zrs_vrsta if evidenca else "Brez ZRS"
        zrs_znesek = (
            float(evidenca.zrs_znesek or 0.0)
            if evidenca
            else float(zrs_zneski.get(zrs_vrsta, 0.0) or 0.0)
        )

        if evidenca and evidenca.datum_placila:
            stevilo_placanih += 1
            skupaj_klub += klub_znesek
            skupaj_zrs += zrs_znesek
            if evidenca.zrs_nakazano:
                skupaj_zrs_nakazano += zrs_znesek

        vrstice.append(
            {
                "clan": clan,
                "evidenca": evidenca,
                "klub_znesek": klub_znesek,
                "zrs_vrsta": zrs_vrsta,
                "zrs_znesek": zrs_znesek,
                "skupaj": klub_znesek + zrs_znesek,
            }
        )

    return templates.TemplateResponse(
        request,
        "zrs_clanarine/index.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin(user),
            "is_editor": is_editor(user),
            "leto": izbrano_leto,
            "vrstice": vrstice,
            "zrs_vrste": list(zrs_zneski.keys()),
            "zrs_zneski": zrs_zneski,
            "stevilo_placanih": stevilo_placanih,
            "skupaj_klub": skupaj_klub,
            "skupaj_zrs": skupaj_zrs,
            "skupaj_zrs_nakazano": skupaj_zrs_nakazano,
            "skupaj_zrs_caka": skupaj_zrs - skupaj_zrs_nakazano,
            "shranjen": request.query_params.get("shranjen") == "1",
        },
    )


@router.post("/shrani/{clan_id}")
async def shrani(
    request: Request,
    clan_id: int,
    leto: int = Form(...),
    zrs_vrsta: str = Form("Brez ZRS"),
    datum_placila: str = Form(""),
    zrs_nakazano: str = Form(""),
    datum_zrs_nakazila: str = Form(""),
    opombe: str = Form(""),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> RedirectResponse:
    user, redirect = require_login(request)
    if redirect:
        return redirect
    if not is_editor(user):
        return RedirectResponse(url=f"/zrs-clanarine?leto={leto}", status_code=302)

    clan = db.query(Clan).filter(Clan.id == clan_id).first()
    if not clan:
        return RedirectResponse(url=f"/zrs-clanarine?leto={leto}", status_code=302)

    zrs_zneski = _zrs_zneski(db)
    if zrs_vrsta not in zrs_zneski:
        zrs_vrsta = "Brez ZRS"

    try:
        placano_dne = _datum(datum_placila)
        nakazano_dne = _datum(datum_zrs_nakazila)
    except ValueError:
        return RedirectResponse(url=f"/zrs-clanarine?leto={leto}", status_code=302)

    klub_znesek = float(
        get_clanarina_zneski(db).get(clan.tip_clanstva, 0.0) or 0.0
    )
    zrs_znesek = float(zrs_zneski.get(zrs_vrsta, 0.0) or 0.0)

    je_nakazano = zrs_nakazano == "1" and zrs_znesek > 0
    if je_nakazano and not nakazano_dne:
        nakazano_dne = date.today()
    if not je_nakazano:
        nakazano_dne = None

    evidenca = (
        db.query(ZrsClanarina)
        .filter(
            ZrsClanarina.clan_id == clan_id,
            ZrsClanarina.leto == leto,
        )
        .first()
    )
    if not evidenca:
        evidenca = ZrsClanarina(clan_id=clan_id, leto=leto)
        db.add(evidenca)

    evidenca.zrs_vrsta = zrs_vrsta
    evidenca.klub_znesek = klub_znesek
    evidenca.zrs_znesek = zrs_znesek
    evidenca.datum_placila = placano_dne
    evidenca.zrs_nakazano = je_nakazano
    evidenca.datum_zrs_nakazila = nakazano_dne
    evidenca.opombe = opombe.strip() or None

    skupni_znesek = klub_znesek + zrs_znesek
    clanarina = (
        db.query(Clanarina)
        .filter(Clanarina.clan_id == clan_id, Clanarina.leto == leto)
        .first()
    )
    if not clanarina:
        clanarina = Clanarina(clan_id=clan_id, leto=leto)
        db.add(clanarina)

    clanarina.datum_placila = placano_dne
    clanarina.znesek = f"{skupni_znesek:.2f}"
    clanarina.opombe = (
        f"Klub {klub_znesek:.2f} EUR + "
        f"ZRS {zrs_znesek:.2f} EUR ({zrs_vrsta})"
    )

    db.commit()

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        user.get("uporabnisko_ime") if user else None,
        "zrs_clanarina_shranjena",
        (
            f"clan_id={clan_id}, leto={leto}, ZRS={zrs_vrsta}, "
            f"klub={klub_znesek:.2f}, zrs={zrs_znesek:.2f}, "
            f"nakazano={je_nakazano}"
        ),
        ip=ip,
    )

    return RedirectResponse(
        url=f"/zrs-clanarine?leto={leto}&shranjen=1",
        status_code=302,
    )


@router.post("/oznaci-vse-nakazano")
async def oznaci_vse_nakazano(
    request: Request,
    leto: int = Form(...),
    datum_nakazila: str = Form(""),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> RedirectResponse:
    user, redirect = require_login(request)
    if redirect:
        return redirect
    if not is_editor(user):
        return RedirectResponse(url=f"/zrs-clanarine?leto={leto}", status_code=302)

    try:
        datum = _datum(datum_nakazila) or date.today()
    except ValueError:
        return RedirectResponse(url=f"/zrs-clanarine?leto={leto}", status_code=302)

    evidence = (
        db.query(ZrsClanarina)
        .filter(
            ZrsClanarina.leto == leto,
            ZrsClanarina.datum_placila.isnot(None),
            ZrsClanarina.zrs_znesek > 0,
        )
        .all()
    )

    for evidenca in evidence:
        evidenca.zrs_nakazano = True
        evidenca.datum_zrs_nakazila = datum

    db.commit()

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        user.get("uporabnisko_ime") if user else None,
        "zrs_vse_nakazano",
        f"Leto {leto}: oznaÄŤenih {len(evidence)} ZRS ÄŤlanarin",
        ip=ip,
    )

    return RedirectResponse(
        url=f"/zrs-clanarine?leto={leto}&shranjen=1",
        status_code=302,
    )