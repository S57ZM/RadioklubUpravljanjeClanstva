import html
import logging
import re
import smtplib
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..audit_log import log_akcija
from ..auth import (
    hash_geslo,
    is_admin,
    preveri_zahteve_gesla,
    require_login,
)
from ..config import get_nastavitev
from ..csrf import csrf_protect, get_csrf_token
from ..database import get_db
from ..email import get_smtp_nastavitve
from ..models import (
    Clan,
    DostopnaProsnja,
    RegistracijskaProsnja,
    Uporabnik,
    ZrsClanarina,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dostop")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["csrf_token"] = get_csrf_token

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,50}$")


def najdi_uporabnika_za_prijavo(
    db: Session,
    identifikator: str,
) -> Uporabnik | None:
    """Najde aktivnega uporabnika po imenu, e-pošti ali klicnem znaku."""

    ident = identifikator.strip()
    if not ident:
        return None

    uporabnik = (
        db.query(Uporabnik)
        .filter(
            func.lower(Uporabnik.uporabnisko_ime) == ident.lower(),
            Uporabnik.aktiven == True,
        )
        .first()
    )
    if uporabnik:
        return uporabnik

    clan = (
        db.query(Clan)
        .filter(
            Clan.aktiven == True,
            or_(
                func.lower(Clan.elektronska_posta) == ident.lower(),
                func.upper(Clan.klicni_znak) == ident.upper(),
            ),
        )
        .first()
    )
    if not clan:
        return None

    return (
        db.query(Uporabnik)
        .filter(
            Uporabnik.clan_id == clan.id,
            Uporabnik.aktiven == True,
        )
        .first()
    )


def _normaliziraj_email(value: str) -> str:
    return value.strip().lower()


def _normaliziraj_klicni_znak(value: str) -> str:
    return value.strip().upper()


def _uporabnisko_ime_zasedeno(
    db: Session,
    uporabnisko_ime: str,
) -> bool:
    ident = uporabnisko_ime.strip().lower()

    if (
        db.query(Uporabnik)
        .filter(func.lower(Uporabnik.uporabnisko_ime) == ident)
        .first()
    ):
        return True

    if (
        db.query(DostopnaProsnja)
        .filter(
            func.lower(DostopnaProsnja.uporabnisko_ime) == ident,
            DostopnaProsnja.status == "caka",
        )
        .first()
    ):
        return True

    return (
        db.query(RegistracijskaProsnja)
        .filter(
            func.lower(RegistracijskaProsnja.uporabnisko_ime) == ident,
            RegistracijskaProsnja.status == "caka",
        )
        .first()
        is not None
    )


def _najdi_obstojecega_clana(
    db: Session,
    email: str,
    klicni_znak: str,
) -> Clan | None:
    """E-pošta je obvezna; klicni znak, če je vpisan, se mora ujemati."""

    email_norm = _normaliziraj_email(email)
    kz_norm = _normaliziraj_klicni_znak(klicni_znak)

    query = db.query(Clan).filter(
        Clan.aktiven == True,
        func.lower(Clan.elektronska_posta) == email_norm,
    )
    if kz_norm:
        query = query.filter(func.upper(Clan.klicni_znak) == kz_norm)

    return query.first()


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


def _zrs_vrste(db: Session) -> list[str]:
    raw = get_nastavitev(
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
    vrste = list(_parse_zneski(raw).keys())
    return vrste or ["Brez ZRS"]


def _tipi_clanstva(db: Session) -> list[str]:
    raw = get_nastavitev(
        db,
        "tipi_clanstva",
        "Osebni\nDružinski\nSimpatizerji\nMladi\nInvalid",
    )
    tipi = [v.strip() for v in raw.splitlines() if v.strip()]
    return tipi or ["Osebni"]


def _operaterski_razredi(db: Session) -> list[str]:
    raw = get_nastavitev(
        db,
        "operaterski_razredi",
        "A\nN\nA - CW\nN - CW",
    )
    return [v.strip() for v in raw.splitlines() if v.strip()]


def _poslji_html(
    db: Session,
    prejemnik: str,
    zadeva: str,
    vsebina_html: str,
) -> None:
    nastavitve = get_smtp_nastavitve(db)

    prejemnik = prejemnik.replace("\r", "").replace("\n", "").strip()
    zadeva = zadeva.replace("\r", "").replace("\n", " ").strip()
    posiljatelj = nastavitve["od"].replace("\r", "").replace("\n", "").strip()

    if not prejemnik:
        raise ValueError("E-poštni naslov prejemnika ni nastavljen.")
    if not posiljatelj:
        raise ValueError("SMTP naslov pošiljatelja ni nastavljen.")

    sporocilo = MIMEMultipart("alternative")
    sporocilo["Subject"] = zadeva
    sporocilo["From"] = posiljatelj
    sporocilo["To"] = prejemnik
    sporocilo.attach(MIMEText(vsebina_html, "html", "utf-8"))

    host = nastavitve["host"]
    port = nastavitve["port"]
    nacin = nastavitve["nacin"]
    uporabnik = nastavitve["uporabnik"]
    geslo = nastavitve["geslo"]

    if nacin == "ssl":
        with smtplib.SMTP_SSL(host, port) as server:
            if uporabnik:
                server.login(uporabnik, geslo)
            server.send_message(sporocilo)
    elif nacin == "plain":
        with smtplib.SMTP(host, port) as server:
            if uporabnik:
                server.login(uporabnik, geslo)
            server.send_message(sporocilo)
    else:
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if uporabnik:
                server.login(uporabnik, geslo)
            server.send_message(sporocilo)


def _admin_email(db: Session) -> str:
    return (
        get_nastavitev(db, "klub_email", "").strip()
        or get_nastavitev(db, "smtp_od", "").strip()
    )


def _render_javna_stran(
    request: Request,
    db: Session,
    napaka: str | None = None,
    uspeh: str | None = None,
    aktivni_zavihek: str = "dostop",
    vrednosti: dict | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "dostop/prosnja.html",
        {
            "request": request,
            "napaka": napaka,
            "uspeh": uspeh,
            "aktivni_zavihek": aktivni_zavihek,
            "vrednosti": vrednosti or {},
            "tipi_clanstva": _tipi_clanstva(db),
            "operaterski_razredi": _operaterski_razredi(db),
            "zrs_vrste": _zrs_vrste(db),
        },
        status_code=status_code,
    )


def _preveri_prijavne_podatke(
    db: Session,
    uporabnisko_ime: str,
    geslo: str,
    geslo_ponovi: str,
) -> str | None:
    if not _USERNAME_RE.fullmatch(uporabnisko_ime):
        return (
            "Uporabniško ime mora imeti 3–50 znakov. "
            "Dovoljene so črke, številke, pika, podčrtaj in vezaj."
        )
    if geslo != geslo_ponovi:
        return "Vneseni gesli se ne ujemata."

    napaka = preveri_zahteve_gesla(geslo)
    if napaka:
        return napaka

    if _uporabnisko_ime_zasedeno(db, uporabnisko_ime):
        return "Izbrano uporabniško ime je že zasedeno ali čaka na odobritev."

    return None


@router.get("/prosnja", response_class=HTMLResponse)
async def prosnja_form(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    if request.session.get("uporabnik"):
        return RedirectResponse(url="/", status_code=302)

    aktivni_zavihek = request.query_params.get("zavihek", "dostop")
    if aktivni_zavihek not in ("dostop", "registracija"):
        aktivni_zavihek = "dostop"

    return _render_javna_stran(
        request,
        db,
        aktivni_zavihek=aktivni_zavihek,
    )


@router.post("/prosnja", response_class=HTMLResponse)
async def prosnja_shrani(
    request: Request,
    elektronska_posta: str = Form(...),
    klicni_znak: str = Form(""),
    uporabnisko_ime: str = Form(...),
    geslo: str = Form(...),
    geslo_ponovi: str = Form(...),
    soglasje: str = Form(""),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> Response:
    if request.session.get("uporabnik"):
        return RedirectResponse(url="/", status_code=302)

    email = _normaliziraj_email(elektronska_posta)
    kz = _normaliziraj_klicni_znak(klicni_znak)
    uporabnisko_ime = uporabnisko_ime.strip()

    vrednosti = {
        "dostop_email": email,
        "dostop_klicni_znak": kz,
        "dostop_uporabnisko_ime": uporabnisko_ime,
    }

    if soglasje != "da":
        return _render_javna_stran(
            request,
            db,
            napaka="Potrdite, da prosite za dostop do svojega članskega zapisa.",
            aktivni_zavihek="dostop",
            vrednosti=vrednosti,
            status_code=400,
        )

    if not _EMAIL_RE.fullmatch(email):
        return _render_javna_stran(
            request,
            db,
            napaka="Vnesite veljaven e-poštni naslov.",
            aktivni_zavihek="dostop",
            vrednosti=vrednosti,
            status_code=400,
        )

    napaka = _preveri_prijavne_podatke(
        db,
        uporabnisko_ime,
        geslo,
        geslo_ponovi,
    )
    if napaka:
        return _render_javna_stran(
            request,
            db,
            napaka=napaka,
            aktivni_zavihek="dostop",
            vrednosti=vrednosti,
            status_code=400,
        )

    clan = _najdi_obstojecega_clana(db, email, kz)
    if not clan:
        return _render_javna_stran(
            request,
            db,
            napaka=(
                "E-pošta in klicni znak se ne ujemata z aktivnim članom. "
                "Novi član naj uporabi zavihek »Nova registracija«."
            ),
            aktivni_zavihek="dostop",
            vrednosti=vrednosti,
            status_code=404,
        )

    if db.query(Uporabnik).filter(Uporabnik.clan_id == clan.id).first():
        return _render_javna_stran(
            request,
            db,
            napaka=(
                "Za tega člana uporabniški dostop že obstaja. "
                "Uporabite prijavo ali možnost pozabljenega gesla."
            ),
            aktivni_zavihek="dostop",
            vrednosti=vrednosti,
            status_code=409,
        )

    obstojeca = (
        db.query(DostopnaProsnja)
        .filter(
            DostopnaProsnja.clan_id == clan.id,
            DostopnaProsnja.status == "caka",
        )
        .first()
    )
    if obstojeca:
        return _render_javna_stran(
            request,
            db,
            napaka="Za tega člana prošnja že čaka na odločitev administratorja.",
            aktivni_zavihek="dostop",
            vrednosti=vrednosti,
            status_code=409,
        )

    prosnja = DostopnaProsnja(
        clan_id=clan.id,
        uporabnisko_ime=uporabnisko_ime,
        geslo_hash=hash_geslo(geslo),
        status="caka",
        ip=request.client.host if request.client else None,
    )
    db.add(prosnja)
    db.commit()
    db.refresh(prosnja)

    admin_email = _admin_email(db)
    if admin_email:
        try:
            osnovni_url = str(request.base_url).rstrip("/")
            _poslji_html(
                db,
                admin_email,
                f"Nova prošnja za dostop – {clan.klicni_znak or clan.ime}",
                f"""
                <h2>Nova prošnja obstoječega člana</h2>
                <table cellpadding="6" cellspacing="0" border="1">
                  <tr><th align="left">Član</th><td>{html.escape(clan.ime)} {html.escape(clan.priimek)}</td></tr>
                  <tr><th align="left">Klicni znak</th><td>{html.escape(clan.klicni_znak or "")}</td></tr>
                  <tr><th align="left">E-pošta</th><td>{html.escape(clan.elektronska_posta or "")}</td></tr>
                  <tr><th align="left">Uporabniško ime</th><td>{html.escape(uporabnisko_ime)}</td></tr>
                </table>
                <p><strong>Geslo ni prikazano in ga administrator ne more prebrati.</strong></p>
                <p><a href="{osnovni_url}/dostop/prosnje">Preglej prošnjo</a></p>
                """,
            )
        except Exception:
            logger.exception("Pošiljanje obvestila administratorju ni uspelo.")

    log_akcija(
        db,
        None,
        "dostop_prosnja",
        f"Prošnja za dostop: clan_id={clan.id}",
        ip=request.client.host if request.client else None,
    )

    return _render_javna_stran(
        request,
        db,
        uspeh=(
            "Prošnja za dostop je bila poslana. "
            "Po odobritvi boste prejeli e-poštno obvestilo."
        ),
        aktivni_zavihek="dostop",
    )


@router.post("/registracija", response_class=HTMLResponse)
async def registracija_shrani(
    request: Request,
    priimek: str = Form(...),
    ime: str = Form(...),
    elektronska_posta: str = Form(...),
    klicni_znak: str = Form(""),
    naslov_ulica: str = Form(""),
    naslov_posta: str = Form(""),
    mobilni_telefon: str = Form(""),
    operaterski_razred: str = Form(""),
    tip_clanstva: str = Form("Osebni"),
    zrs_vrsta: str = Form("Brez ZRS"),
    uporabnisko_ime: str = Form(...),
    geslo: str = Form(...),
    geslo_ponovi: str = Form(...),
    soglasje: str = Form(""),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> Response:
    if request.session.get("uporabnik"):
        return RedirectResponse(url="/", status_code=302)

    priimek = priimek.strip().title()
    ime = ime.strip().title()
    email = _normaliziraj_email(elektronska_posta)
    kz = _normaliziraj_klicni_znak(klicni_znak)
    uporabnisko_ime = uporabnisko_ime.strip()

    vrednosti = {
        "reg_priimek": priimek,
        "reg_ime": ime,
        "reg_email": email,
        "reg_klicni_znak": kz,
        "reg_naslov_ulica": naslov_ulica.strip(),
        "reg_naslov_posta": naslov_posta.strip(),
        "reg_mobilni_telefon": mobilni_telefon.strip(),
        "reg_operaterski_razred": operaterski_razred.strip(),
        "reg_tip_clanstva": tip_clanstva,
        "reg_zrs_vrsta": zrs_vrsta,
        "reg_uporabnisko_ime": uporabnisko_ime,
    }

    if soglasje != "da":
        return _render_javna_stran(
            request,
            db,
            napaka="Potrdite pravilnost podatkov in prošnjo za članstvo.",
            aktivni_zavihek="registracija",
            vrednosti=vrednosti,
            status_code=400,
        )

    if not priimek or not ime:
        return _render_javna_stran(
            request,
            db,
            napaka="Ime in priimek sta obvezna.",
            aktivni_zavihek="registracija",
            vrednosti=vrednosti,
            status_code=400,
        )

    if not _EMAIL_RE.fullmatch(email):
        return _render_javna_stran(
            request,
            db,
            napaka="Vnesite veljaven e-poštni naslov.",
            aktivni_zavihek="registracija",
            vrednosti=vrednosti,
            status_code=400,
        )

    if tip_clanstva not in _tipi_clanstva(db):
        return _render_javna_stran(
            request,
            db,
            napaka="Izbrana vrsta članstva ni veljavna.",
            aktivni_zavihek="registracija",
            vrednosti=vrednosti,
            status_code=400,
        )

    if zrs_vrsta not in _zrs_vrste(db):
        return _render_javna_stran(
            request,
            db,
            napaka="Izbrana vrsta ZRS članarine ni veljavna.",
            aktivni_zavihek="registracija",
            vrednosti=vrednosti,
            status_code=400,
        )

    napaka = _preveri_prijavne_podatke(
        db,
        uporabnisko_ime,
        geslo,
        geslo_ponovi,
    )
    if napaka:
        return _render_javna_stran(
            request,
            db,
            napaka=napaka,
            aktivni_zavihek="registracija",
            vrednosti=vrednosti,
            status_code=400,
        )

    obstojeci_pogoji = [
        func.lower(Clan.elektronska_posta) == email,
    ]
    if kz:
        obstojeci_pogoji.append(func.upper(Clan.klicni_znak) == kz)

    if db.query(Clan).filter(or_(*obstojeci_pogoji)).first():
        return _render_javna_stran(
            request,
            db,
            napaka=(
                "Član s to e-pošto ali klicnim znakom že obstaja. "
                "Uporabite zavihek »Obstoječi član«."
            ),
            aktivni_zavihek="registracija",
            vrednosti=vrednosti,
            status_code=409,
        )

    pending_pogoji = [
        func.lower(RegistracijskaProsnja.elektronska_posta) == email,
    ]
    if kz:
        pending_pogoji.append(
            func.upper(RegistracijskaProsnja.klicni_znak) == kz
        )

    if (
        db.query(RegistracijskaProsnja)
        .filter(
            RegistracijskaProsnja.status == "caka",
            or_(*pending_pogoji),
        )
        .first()
    ):
        return _render_javna_stran(
            request,
            db,
            napaka="Registracijska prošnja s temi podatki že čaka na obravnavo.",
            aktivni_zavihek="registracija",
            vrednosti=vrednosti,
            status_code=409,
        )

    prosnja = RegistracijskaProsnja(
        priimek=priimek,
        ime=ime,
        klicni_znak=kz or None,
        elektronska_posta=email,
        naslov_ulica=naslov_ulica.strip() or None,
        naslov_posta=naslov_posta.strip() or None,
        mobilni_telefon=mobilni_telefon.strip() or None,
        operaterski_razred=operaterski_razred.strip() or None,
        tip_clanstva=tip_clanstva,
        zrs_vrsta=zrs_vrsta,
        uporabnisko_ime=uporabnisko_ime,
        geslo_hash=hash_geslo(geslo),
        status="caka",
        ip=request.client.host if request.client else None,
    )
    db.add(prosnja)
    db.commit()
    db.refresh(prosnja)

    admin_email = _admin_email(db)
    if admin_email:
        try:
            osnovni_url = str(request.base_url).rstrip("/")
            _poslji_html(
                db,
                admin_email,
                f"Nova prošnja za članstvo – {kz or f'{ime} {priimek}'}",
                f"""
                <h2>Nova registracijska prošnja</h2>
                <table cellpadding="6" cellspacing="0" border="1">
                  <tr><th align="left">Ime in priimek</th><td>{html.escape(ime)} {html.escape(priimek)}</td></tr>
                  <tr><th align="left">Klicni znak</th><td>{html.escape(kz)}</td></tr>
                  <tr><th align="left">E-pošta</th><td>{html.escape(email)}</td></tr>
                  <tr><th align="left">Vrsta članstva</th><td>{html.escape(tip_clanstva)}</td></tr>
                  <tr><th align="left">ZRS</th><td>{html.escape(zrs_vrsta)}</td></tr>
                  <tr><th align="left">Uporabniško ime</th><td>{html.escape(uporabnisko_ime)}</td></tr>
                </table>
                <p><strong>Geslo ni prikazano in ga administrator ne more prebrati.</strong></p>
                <p><a href="{osnovni_url}/dostop/prosnje">Preglej registracijo</a></p>
                """,
            )
        except Exception:
            logger.exception("Pošiljanje registracijskega obvestila ni uspelo.")

    log_akcija(
        db,
        None,
        "registracijska_prosnja",
        f"Nova registracija: {email}, {kz}",
        ip=request.client.host if request.client else None,
    )

    return _render_javna_stran(
        request,
        db,
        uspeh=(
            "Prošnja za članstvo in registracijo je bila poslana. "
            "Po odobritvi boste prejeli e-poštno obvestilo."
        ),
        aktivni_zavihek="registracija",
    )


@router.get("/prosnje", response_class=HTMLResponse)
async def prosnje_seznam(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect
    if not is_admin(user):
        return RedirectResponse(url="/", status_code=302)

    dostopne = (
        db.query(DostopnaProsnja)
        .order_by(
            (DostopnaProsnja.status == "caka").desc(),
            DostopnaProsnja.created_at.desc(),
        )
        .all()
    )
    registracijske = (
        db.query(RegistracijskaProsnja)
        .order_by(
            (RegistracijskaProsnja.status == "caka").desc(),
            RegistracijskaProsnja.created_at.desc(),
        )
        .all()
    )

    return templates.TemplateResponse(
        request,
        "dostop/admin.html",
        {
            "request": request,
            "user": user,
            "is_admin": True,
            "dostopne_prosnje": dostopne,
            "registracijske_prosnje": registracijske,
            "sporocilo": request.query_params.get("sporocilo"),
        },
    )


@router.post("/prosnje/{prosnja_id}/odobri")
async def prosnja_odobri(
    request: Request,
    prosnja_id: int,
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect
    if not is_admin(user):
        return RedirectResponse(url="/", status_code=302)

    prosnja = (
        db.query(DostopnaProsnja)
        .filter(DostopnaProsnja.id == prosnja_id)
        .first()
    )
    if not prosnja or prosnja.status != "caka":
        return RedirectResponse(
            url="/dostop/prosnje?sporocilo=Prošnja ni več aktivna.",
            status_code=302,
        )

    clan = prosnja.clan
    if not clan:
        return RedirectResponse(
            url="/dostop/prosnje?sporocilo=Članski zapis ne obstaja.",
            status_code=302,
        )

    if db.query(Uporabnik).filter(Uporabnik.clan_id == clan.id).first():
        return RedirectResponse(
            url="/dostop/prosnje?sporocilo=Član že ima uporabniški račun.",
            status_code=302,
        )

    if _uporabnisko_ime_zasedeno(db, prosnja.uporabnisko_ime):
        drugi = (
            db.query(Uporabnik)
            .filter(
                func.lower(Uporabnik.uporabnisko_ime)
                == prosnja.uporabnisko_ime.lower()
            )
            .first()
        )
        if drugi:
            return RedirectResponse(
                url="/dostop/prosnje?sporocilo=Uporabniško ime je zasedeno.",
                status_code=302,
            )

    uporabnik = Uporabnik(
        clan_id=clan.id,
        uporabnisko_ime=prosnja.uporabnisko_ime,
        geslo_hash=prosnja.geslo_hash,
        vloga="bralec",
        ime_priimek=f"{clan.ime} {clan.priimek}".strip(),
        aktiven=True,
    )
    db.add(uporabnik)

    prosnja.status = "odobrena"
    prosnja.geslo_hash = ""
    prosnja.obravnavano_at = datetime.now(timezone.utc)
    prosnja.obravnaval = user.get("uporabnisko_ime")
    db.commit()

    if clan.elektronska_posta:
        try:
            osnovni_url = str(request.base_url).rstrip("/")
            _poslji_html(
                db,
                clan.elektronska_posta,
                "Dostop do aplikacije S50TTT je odobren",
                f"""
                <h2>Dostop je odobren</h2>
                <p>Pozdravljeni {html.escape(clan.ime)},</p>
                <p>Prijavite se lahko z uporabniškim imenom, e-pošto ali klicnim znakom.</p>
                <p><a href="{osnovni_url}/login">Odpri prijavo</a></p>
                <p>Geslo je tisto, ki ste ga določili ob oddaji prošnje.</p>
                """,
            )
        except Exception:
            logger.exception("Pošiljanje potrditve članu ni uspelo.")

    log_akcija(
        db,
        user.get("uporabnisko_ime"),
        "dostop_odobren",
        f"Odobrena prošnja {prosnja.id}, clan_id={clan.id}",
        ip=request.client.host if request.client else None,
    )

    return RedirectResponse(
        url="/dostop/prosnje?sporocilo=Prošnja za dostop je odobrena.",
        status_code=302,
    )


@router.post("/prosnje/{prosnja_id}/zavrni")
async def prosnja_zavrni(
    request: Request,
    prosnja_id: int,
    opomba: str = Form(""),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect
    if not is_admin(user):
        return RedirectResponse(url="/", status_code=302)

    prosnja = (
        db.query(DostopnaProsnja)
        .filter(DostopnaProsnja.id == prosnja_id)
        .first()
    )
    if not prosnja or prosnja.status != "caka":
        return RedirectResponse(
            url="/dostop/prosnje?sporocilo=Prošnja ni več aktivna.",
            status_code=302,
        )

    prosnja.status = "zavrnjena"
    prosnja.geslo_hash = ""
    prosnja.opomba = opomba.strip()[:500] or None
    prosnja.obravnavano_at = datetime.now(timezone.utc)
    prosnja.obravnaval = user.get("uporabnisko_ime")
    db.commit()

    clan = prosnja.clan
    if clan and clan.elektronska_posta:
        try:
            dodatno = (
                f"<p>Opomba administratorja: {html.escape(prosnja.opomba)}</p>"
                if prosnja.opomba
                else ""
            )
            _poslji_html(
                db,
                clan.elektronska_posta,
                "Prošnja za dostop do aplikacije S50TTT",
                f"""
                <h2>Prošnja ni bila odobrena</h2>
                <p>Pozdravljeni {html.escape(clan.ime)},</p>
                <p>vaša prošnja za dostop trenutno ni bila odobrena.</p>
                {dodatno}
                """,
            )
        except Exception:
            logger.exception("Pošiljanje obvestila o zavrnitvi ni uspelo.")

    return RedirectResponse(
        url="/dostop/prosnje?sporocilo=Prošnja za dostop je zavrnjena.",
        status_code=302,
    )


@router.post("/registracije/{prosnja_id}/odobri")
async def registracija_odobri(
    request: Request,
    prosnja_id: int,
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect
    if not is_admin(user):
        return RedirectResponse(url="/", status_code=302)

    prosnja = (
        db.query(RegistracijskaProsnja)
        .filter(RegistracijskaProsnja.id == prosnja_id)
        .first()
    )
    if not prosnja or prosnja.status != "caka":
        return RedirectResponse(
            url="/dostop/prosnje?sporocilo=Registracija ni več aktivna.",
            status_code=302,
        )

    obstojeci_pogoji = [
        func.lower(Clan.elektronska_posta)
        == prosnja.elektronska_posta.lower(),
    ]
    if prosnja.klicni_znak:
        obstojeci_pogoji.append(
            func.upper(Clan.klicni_znak)
            == prosnja.klicni_znak.upper()
        )

    obstojeci = db.query(Clan).filter(or_(*obstojeci_pogoji)).first()
    if obstojeci:
        return RedirectResponse(
            url="/dostop/prosnje?sporocilo=Član s temi podatki že obstaja.",
            status_code=302,
        )

    if (
        db.query(Uporabnik)
        .filter(
            func.lower(Uporabnik.uporabnisko_ime)
            == prosnja.uporabnisko_ime.lower()
        )
        .first()
    ):
        return RedirectResponse(
            url="/dostop/prosnje?sporocilo=Uporabniško ime je zasedeno.",
            status_code=302,
        )

    clan = Clan(
        priimek=prosnja.priimek,
        ime=prosnja.ime,
        klicni_znak=prosnja.klicni_znak,
        naslov_ulica=prosnja.naslov_ulica,
        naslov_posta=prosnja.naslov_posta,
        tip_clanstva=prosnja.tip_clanstva,
        operaterski_razred=prosnja.operaterski_razred,
        mobilni_telefon=prosnja.mobilni_telefon,
        elektronska_posta=prosnja.elektronska_posta,
        aktiven=True,
    )
    db.add(clan)
    db.flush()

    uporabnik = Uporabnik(
        clan_id=clan.id,
        uporabnisko_ime=prosnja.uporabnisko_ime,
        geslo_hash=prosnja.geslo_hash,
        vloga="bralec",
        ime_priimek=f"{clan.ime} {clan.priimek}".strip(),
        aktiven=True,
    )
    db.add(uporabnik)

    klub_zneski = _parse_zneski(
        get_nastavitev(db, "clanarina_zneski", "")
    )
    zrs_zneski = _parse_zneski(
        get_nastavitev(db, "zrs_clanarina_zneski", "")
    )
    db.add(
        ZrsClanarina(
            clan_id=clan.id,
            leto=date.today().year,
            zrs_vrsta=prosnja.zrs_vrsta,
            klub_znesek=klub_zneski.get(prosnja.tip_clanstva, 0.0),
            zrs_znesek=zrs_zneski.get(prosnja.zrs_vrsta, 0.0),
            zrs_nakazano=False,
        )
    )

    prosnja.status = "odobrena"
    prosnja.geslo_hash = ""
    prosnja.created_clan_id = clan.id
    prosnja.obravnavano_at = datetime.now(timezone.utc)
    prosnja.obravnaval = user.get("uporabnisko_ime")
    db.commit()

    try:
        osnovni_url = str(request.base_url).rstrip("/")
        _poslji_html(
            db,
            prosnja.elektronska_posta,
            "Članstvo in dostop do aplikacije S50TTT sta odobrena",
            f"""
            <h2>Registracija je odobrena</h2>
            <p>Pozdravljeni {html.escape(prosnja.ime)},</p>
            <p>vaš članski zapis in uporabniški dostop sta ustvarjena.</p>
            <p><a href="{osnovni_url}/login">Odpri prijavo</a></p>
            <p>Po prijavi boste lahko videli in urejali samo svoje podatke.</p>
            """,
        )
    except Exception:
        logger.exception("Pošiljanje potrditve registracije ni uspelo.")

    log_akcija(
        db,
        user.get("uporabnisko_ime"),
        "registracija_odobrena",
        f"Registracija {prosnja.id}, novi clan_id={clan.id}",
        ip=request.client.host if request.client else None,
    )

    return RedirectResponse(
        url="/dostop/prosnje?sporocilo=Registracija je odobrena.",
        status_code=302,
    )


@router.post("/registracije/{prosnja_id}/zavrni")
async def registracija_zavrni(
    request: Request,
    prosnja_id: int,
    opomba: str = Form(""),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> Response:
    user, redirect = require_login(request)
    if redirect:
        return redirect
    if not is_admin(user):
        return RedirectResponse(url="/", status_code=302)

    prosnja = (
        db.query(RegistracijskaProsnja)
        .filter(RegistracijskaProsnja.id == prosnja_id)
        .first()
    )
    if not prosnja or prosnja.status != "caka":
        return RedirectResponse(
            url="/dostop/prosnje?sporocilo=Registracija ni več aktivna.",
            status_code=302,
        )

    prosnja.status = "zavrnjena"
    prosnja.geslo_hash = ""
    prosnja.opomba = opomba.strip()[:500] or None
    prosnja.obravnavano_at = datetime.now(timezone.utc)
    prosnja.obravnaval = user.get("uporabnisko_ime")
    db.commit()

    try:
        dodatno = (
            f"<p>Opomba administratorja: {html.escape(prosnja.opomba)}</p>"
            if prosnja.opomba
            else ""
        )
        _poslji_html(
            db,
            prosnja.elektronska_posta,
            "Prošnja za članstvo v S50TTT",
            f"""
            <h2>Registracijska prošnja ni bila odobrena</h2>
            <p>Pozdravljeni {html.escape(prosnja.ime)},</p>
            <p>vaša prošnja trenutno ni bila odobrena.</p>
            {dodatno}
            """,
        )
    except Exception:
        logger.exception("Pošiljanje zavrnitve registracije ni uspelo.")

    return RedirectResponse(
        url="/dostop/prosnje?sporocilo=Registracija je zavrnjena.",
        status_code=302,
    )