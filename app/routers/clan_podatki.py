import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..audit_log import log_akcija
from ..auth import is_editor, require_login
from ..csrf import csrf_protect, get_csrf_token
from ..database import get_db
from ..models import Clan, ClanPodatkiToken, ClanSprememba


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["csrf_token"] = get_csrf_token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _neveljavna_povezava(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="moji_podatki_neveljavno.html",
        context={},
        status_code=410,
    )


def _napaka_obrazca(
    request: Request, clan: Clan, token: str, expires_at,
    napaka: str, vneseno: dict
):
    return templates.TemplateResponse(
        request=request,
        name="moji_podatki.html",
        context={
            "clan": clan,
            "token": token,
            "velja_do": expires_at,
            "napaka": napaka,
            "vneseno": vneseno,
        },
        status_code=400,
    )


@router.post("/clani/{clan_id}/podatki-link")
async def ustvari_podatki_link(
    request: Request,
    clan_id: int,
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
):
    user, redirect = require_login(request)
    if redirect:
        return redirect

    if not is_editor(user):
        return RedirectResponse("/clani", status_code=302)

    clan = db.query(Clan).filter(Clan.id == clan_id).first()
    if not clan:
        return RedirectResponse("/clani", status_code=302)

    token = secrets.token_urlsafe(32)
    zdaj = datetime.now(timezone.utc)

    # Veljavna naj bo samo zadnja ustvarjena povezava.
    db.query(ClanPodatkiToken).filter(
        ClanPodatkiToken.clan_id == clan.id,
        ClanPodatkiToken.uporabljen_at.is_(None),
    ).update(
        {ClanPodatkiToken.uporabljen_at: zdaj},
        synchronize_session=False,
    )

    zapis = ClanPodatkiToken(
        clan_id=clan.id,
        token_hash=_token_hash(token),
        expires_at=zdaj + timedelta(days=14),
    )

    db.add(zapis)
    db.commit()

    povezava = str(
        request.url_for(
            "prikazi_moje_podatke",
            token=token,
        )
    )

    request.session["flash_podatki_link"] = povezava

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        user.get("uporabnisko_ime") if user else None,
        "clan_podatki_link",
        f"clan_id={clan.id}",
        ip=ip,
    )

    return RedirectResponse(
        f"/clani/{clan.id}",
        status_code=302,
    )


@router.get("/moji-podatki/{token}", name="prikazi_moje_podatke")
async def prikazi_moje_podatke(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    zapis = (
        db.query(ClanPodatkiToken)
        .filter(
            ClanPodatkiToken.token_hash == _token_hash(token)
        )
        .first()
    )

    if not zapis:
        return _neveljavna_povezava(request)

    zdaj = datetime.now(timezone.utc)

    expires_at = zapis.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if zapis.uporabljen_at is not None or expires_at < zdaj:
        return _neveljavna_povezava(request)

    clan = db.query(Clan).filter(Clan.id == zapis.clan_id).first()
    if not clan:
        return _neveljavna_povezava(request)

    return templates.TemplateResponse(
        request=request,
        name="moji_podatki.html",
        context={
            "clan": clan,
            "token": token,
            "velja_do": expires_at,
        },
    )


@router.post("/moji-podatki/{token}/oddaj")
async def oddaj_moje_podatke(
    request: Request,
    token: str,
    ime: str = Form(""),
    priimek: str = Form(""),
    klicni_znak: str = Form(""),
    naslov_ulica: str = Form(""),
    naslov_posta: str = Form(""),
    elektronska_posta: str = Form(""),
    mobilni_telefon: str = Form(""),
    telefon_doma: str = Form(""),
    opomba_clana: str = Form(""),
    dejanje: str = Form(...),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
):
    zapis = (
        db.query(ClanPodatkiToken)
        .filter(ClanPodatkiToken.token_hash == _token_hash(token))
        .first()
    )

    if not zapis:
        return _neveljavna_povezava(request)

    zdaj = datetime.now(timezone.utc)

    expires_at = zapis.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if zapis.uporabljen_at is not None or expires_at < zdaj:
        return _neveljavna_povezava(request)

    clan = db.query(Clan).filter(Clan.id == zapis.clan_id).first()
    if not clan:
        return _neveljavna_povezava(request)

    if dejanje not in ("spremembe", "pravilni"):
        return RedirectResponse(
            f"/moji-podatki/{token}",
            status_code=303,
        )

    podatki_pravilni = dejanje == "pravilni"

    if podatki_pravilni:
        ime = clan.ime or ""
        priimek = clan.priimek or ""
        klicni_znak = clan.klicni_znak or ""
        naslov_ulica = clan.naslov_ulica or ""
        naslov_posta = clan.naslov_posta or ""
        elektronska_posta = clan.elektronska_posta or ""
        mobilni_telefon = clan.mobilni_telefon or ""
        telefon_doma = clan.telefon_doma or ""
        opomba_clana = ""

    # Validacija podatkov, ki jih član lahko spremeni.
    if not podatki_pravilni:
        vneseno = {
            "ime": ime,
            "priimek": priimek,
            "klicni_znak": klicni_znak,
            "naslov_ulica": naslov_ulica,
            "naslov_posta": naslov_posta,
            "elektronska_posta": elektronska_posta,
            "mobilni_telefon": mobilni_telefon,
            "telefon_doma": telefon_doma,
            "opomba_clana": opomba_clana,
        }

        polja = {
            "Ime": (ime, 100),
            "Priimek": (priimek, 100),
            "Klicni znak": (klicni_znak, 30),
            "Naslov": (naslov_ulica, 200),
            "Pošta / kraj": (naslov_posta, 100),
            "E-pošta": (elektronska_posta, 254),
            "Mobilni telefon": (mobilni_telefon, 50),
            "Domači telefon": (telefon_doma, 50),
            "Opomba": (opomba_clana, 2000),
        }

        for naziv, (vrednost, meja) in polja.items():
            if len(vrednost.strip()) > meja:
                return _napaka_obrazca(
                    request, clan, token, expires_at,
                    f"{naziv} je predolg. Največ {meja} znakov.", vneseno
                )

        if not ime.strip() or not priimek.strip():
            return _napaka_obrazca(
                request, clan, token, expires_at,
                "Ime in priimek sta obvezna.", vneseno
            )

        email = elektronska_posta.strip()
        if email and (
            "@" not in email
            or email.startswith("@")
            or email.endswith("@")
            or "." not in email.split("@")[-1]
        ):
            return _napaka_obrazca(
                request, clan, token, expires_at,
                "Vnesite veljaven e-poštni naslov.", vneseno
            )

    sprememba = ClanSprememba(
        clan_id=clan.id,
        token_id=zapis.id,
        ime=ime.strip(),
        priimek=priimek.strip(),
        klicni_znak=klicni_znak.strip().upper(),
        naslov_ulica=(naslov_ulica.strip() or None),
        naslov_posta=(naslov_posta.strip() or None),
        elektronska_posta=(elektronska_posta.strip().lower() or None),
        mobilni_telefon=(mobilni_telefon.strip() or None),
        telefon_doma=(telefon_doma.strip() or None),
        opomba_clana=(opomba_clana.strip() or None),
        podatki_pravilni=podatki_pravilni,
        status="potrjeno" if podatki_pravilni else "caka",
        obravnavano_at=zdaj if podatki_pravilni else None,
        obravnaval="samopotrditev" if podatki_pravilni else None,
    )

    db.add(sprememba)

    # Token je enkraten. Po uspešni oddaji ga ni mogoče ponovno uporabiti.
    zapis.uporabljen_at = zdaj

    db.commit()

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        None,
        "clan_podatki_oddani",
        (
            f"clan_id={clan.id}, "
            f"sprememba_id={sprememba.id}, "
            f"podatki_pravilni={podatki_pravilni}"
        ),
        ip=ip,
    )

    return templates.TemplateResponse(
        request=request,
        name="moji_podatki_hvala.html",
        context={
            "clan": clan,
            "podatki_pravilni": podatki_pravilni,
        },
    )


@router.get("/admin/spremembe-podatkov")
async def seznam_sprememb_podatkov(
    request: Request,
    db: Session = Depends(get_db),
):
    user, redirect = require_login(request)
    if redirect:
        return redirect

    if not is_editor(user):
        return templates.TemplateResponse(
            request=request,
            name="403.html",
            context={"user": user},
            status_code=403,
        )

    spremembe = (
        db.query(ClanSprememba)
        .filter(ClanSprememba.status == "caka")
        .order_by(ClanSprememba.created_at.asc())
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="clan_spremembe/index.html",
        context={
            "user": user,
            "spremembe": spremembe,
        },
    )


@router.post("/admin/spremembe-podatkov/{sprememba_id}/potrdi")
async def potrdi_spremembo_podatkov(
    request: Request,
    sprememba_id: int,
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
):
    user, redirect = require_login(request)
    if redirect:
        return redirect

    if not is_editor(user):
        return templates.TemplateResponse(
            request=request,
            name="403.html",
            context={"user": user},
            status_code=403,
        )

    sprememba = (
        db.query(ClanSprememba)
        .filter(ClanSprememba.id == sprememba_id)
        .first()
    )

    if not sprememba or sprememba.status != "caka":
        return RedirectResponse(
            "/admin/spremembe-podatkov",
            status_code=303,
        )

    clan = db.query(Clan).filter(Clan.id == sprememba.clan_id).first()
    if not clan:
        return RedirectResponse(
            "/admin/spremembe-podatkov",
            status_code=303,
        )

    # Starejsi zahtevki nimajo novih osebnih polj.
    if sprememba.ime is not None:
        clan.ime = sprememba.ime
        clan.priimek = sprememba.priimek
        clan.klicni_znak = sprememba.klicni_znak or None

    # Ostala dovoljena polja.
    clan.naslov_ulica = sprememba.naslov_ulica
    clan.naslov_posta = sprememba.naslov_posta
    clan.elektronska_posta = sprememba.elektronska_posta
    clan.mobilni_telefon = sprememba.mobilni_telefon
    clan.telefon_doma = sprememba.telefon_doma

    zdaj = datetime.now(timezone.utc)

    sprememba.status = "potrjeno"
    sprememba.obravnavano_at = zdaj
    sprememba.obravnaval = user.get("uporabnisko_ime")

    db.commit()

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        user.get("uporabnisko_ime"),
        "clan_podatki_potrjeni",
        f"clan_id={clan.id}, sprememba_id={sprememba.id}",
        ip=ip,
    )

    return RedirectResponse(
        "/admin/spremembe-podatkov",
        status_code=303,
    )


@router.post("/admin/spremembe-podatkov/{sprememba_id}/zavrni")
async def zavrni_spremembo_podatkov(
    request: Request,
    sprememba_id: int,
    opomba_admin: str = Form(""),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
):
    user, redirect = require_login(request)
    if redirect:
        return redirect

    if not is_editor(user):
        return templates.TemplateResponse(
            request=request,
            name="403.html",
            context={"user": user},
            status_code=403,
        )

    sprememba = (
        db.query(ClanSprememba)
        .filter(ClanSprememba.id == sprememba_id)
        .first()
    )

    if not sprememba or sprememba.status != "caka":
        return RedirectResponse(
            "/admin/spremembe-podatkov",
            status_code=303,
        )

    zdaj = datetime.now(timezone.utc)

    sprememba.status = "zavrnjeno"
    sprememba.obravnavano_at = zdaj
    sprememba.obravnaval = user.get("uporabnisko_ime")
    sprememba.opomba_admin = opomba_admin.strip() or None

    db.commit()

    ip = request.client.host if request.client else None
    log_akcija(
        db,
        user.get("uporabnisko_ime"),
        "clan_podatki_zavrnjeni",
        f"clan_id={sprememba.clan_id}, sprememba_id={sprememba.id}",
        ip=ip,
    )

    return RedirectResponse(
        "/admin/spremembe-podatkov",
        status_code=303,
    )
