import html
import logging
import re
import smtplib
from datetime import datetime, timezone
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
from ..models import Clan, DostopnaProsnja, Uporabnik


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dostop")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["csrf_token"] = get_csrf_token


def najdi_uporabnika_za_prijavo(
    db: Session,
    identifikator: str,
) -> Uporabnik | None:
    """Najde uporabnika po uporabniškem imenu, e-pošti ali klicnem znaku."""

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


def _najdi_clana(
    db: Session,
    identifikator: str,
) -> Clan | None:
    ident = identifikator.strip()
    if not ident:
        return None

    return (
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


def _render_prosnja(
    request: Request,
    napaka: str | None = None,
    uspeh: str | None = None,
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
            "vrednosti": vrednosti or {},
        },
        status_code=status_code,
    )


@router.get("/prosnja", response_class=HTMLResponse)
async def prosnja_form(request: Request) -> Response:
    if request.session.get("uporabnik"):
        return RedirectResponse(url="/clani", status_code=302)
    return _render_prosnja(request)


@router.post("/prosnja", response_class=HTMLResponse)
async def prosnja_shrani(
    request: Request,
    identifikator: str = Form(...),
    uporabnisko_ime: str = Form(...),
    geslo: str = Form(...),
    geslo_ponovi: str = Form(...),
    soglasje: str = Form(""),
    db: Session = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
) -> Response:
    if request.session.get("uporabnik"):
        return RedirectResponse(url="/clani", status_code=302)

    identifikator = identifikator.strip()
    uporabnisko_ime = uporabnisko_ime.strip()
    vrednosti = {
        "identifikator": identifikator,
        "uporabnisko_ime": uporabnisko_ime,
    }

    if soglasje != "da":
        return _render_prosnja(
            request,
            napaka="Potrdite, da prosite za dostop do svojega članskega zapisa.",
            vrednosti=vrednosti,
            status_code=400,
        )

    if not re.fullmatch(r"[A-Za-z0-9._-]{3,50}", uporabnisko_ime):
        return _render_prosnja(
            request,
            napaka=(
                "Uporabniško ime mora imeti 3–50 znakov. "
                "Dovoljene so črke, številke, pika, podčrtaj in vezaj."
            ),
            vrednosti=vrednosti,
            status_code=400,
        )

    if geslo != geslo_ponovi:
        return _render_prosnja(
            request,
            napaka="Vneseni gesli se ne ujemata.",
            vrednosti=vrednosti,
            status_code=400,
        )

    napaka_gesla = preveri_zahteve_gesla(geslo)
    if napaka_gesla:
        return _render_prosnja(
            request,
            napaka=napaka_gesla,
            vrednosti=vrednosti,
            status_code=400,
        )

    obstojeci_uporabnik = (
        db.query(Uporabnik)
        .filter(
            func.lower(Uporabnik.uporabnisko_ime)
            == uporabnisko_ime.lower()
        )
        .first()
    )
    if obstojeci_uporabnik:
        return _render_prosnja(
            request,
            napaka="Izbrano uporabniško ime je že zasedeno.",
            vrednosti=vrednosti,
            status_code=400,
        )

    clan = _najdi_clana(db, identifikator)
    if not clan:
        return _render_prosnja(
            request,
            napaka=(
                "Vnesena e-pošta ali klicni znak se ne ujema "
                "z aktivnim članom v evidenci."
            ),
            vrednosti=vrednosti,
            status_code=404,
        )

    if not clan.elektronska_posta:
        return _render_prosnja(
            request,
            napaka=(
                "Pri članu ni vpisanega e-poštnega naslova. "
                "Najprej naj ga administrator dopolni."
            ),
            vrednosti=vrednosti,
            status_code=400,
        )

    if db.query(Uporabnik).filter(Uporabnik.clan_id == clan.id).first():
        return _render_prosnja(
            request,
            napaka=(
                "Za tega člana uporabniški dostop že obstaja. "
                "Uporabite prijavo ali kasneje možnost pozabljenega gesla."
            ),
            vrednosti=vrednosti,
            status_code=409,
        )

    obstojeca_prosnja = (
        db.query(DostopnaProsnja)
        .filter(
            DostopnaProsnja.clan_id == clan.id,
            DostopnaProsnja.status == "caka",
        )
        .first()
    )
    if obstojeca_prosnja:
        return _render_prosnja(
            request,
            napaka="Za tega člana prošnja že čaka na odločitev administratorja.",
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

    admin_email = (
        get_nastavitev(db, "klub_email", "").strip()
        or get_nastavitev(db, "smtp_od", "").strip()
    )

    if admin_email:
        try:
            osnovni_url = str(request.base_url).rstrip("/")
            _poslji_html(
                db,
                admin_email,
                f"Nova prošnja za dostop – {clan.klicni_znak or clan.ime}",
                f"""
                <h2>Nova prošnja za dostop</h2>
                <p>Član je oddal prošnjo za dostop do aplikacije.</p>
                <table cellpadding="6" cellspacing="0" border="1">
                  <tr><th align="left">Član</th><td>{html.escape(clan.ime)} {html.escape(clan.priimek)}</td></tr>
                  <tr><th align="left">Klicni znak</th><td>{html.escape(clan.klicni_znak or "")}</td></tr>
                  <tr><th align="left">E-pošta</th><td>{html.escape(clan.elektronska_posta or "")}</td></tr>
                  <tr><th align="left">Uporabniško ime</th><td>{html.escape(uporabnisko_ime)}</td></tr>
                </table>
                <p><strong>Geslo ni prikazano in ga administrator ne more prebrati.</strong></p>
                <p><a href="{osnovni_url}/dostop/prosnje">Odpri seznam prošenj</a></p>
                """,
            )
        except Exception:
            logger.exception("Pošiljanje obvestila administratorju ni uspelo.")

    log_akcija(
        db,
        None,
        "dostop_prosnja",
        (
            f"Prošnja za dostop: clan_id={clan.id}, "
            f"uporabnisko_ime={uporabnisko_ime}"
        ),
        ip=request.client.host if request.client else None,
    )

    return _render_prosnja(
        request,
        uspeh=(
            "Prošnja je bila poslana administratorju. "
            "Po odobritvi boste prejeli obvestilo po e-pošti."
        ),
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
        return RedirectResponse(url="/clani", status_code=302)

    prosnje = (
        db.query(DostopnaProsnja)
        .order_by(
            (DostopnaProsnja.status == "caka").desc(),
            DostopnaProsnja.created_at.desc(),
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
            "prosnje": prosnje,
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
        return RedirectResponse(url="/clani", status_code=302)

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

    if (
        db.query(Uporabnik)
        .filter(
            func.lower(Uporabnik.uporabnisko_ime)
            == prosnja.uporabnisko_ime.lower()
        )
        .first()
    ):
        return RedirectResponse(
            url="/dostop/prosnje?sporocilo=Uporabniško ime je medtem postalo zasedeno.",
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
                <p>administrator je odobril vaš dostop do članske aplikacije.</p>
                <p>Prijavite se lahko z:</p>
                <ul>
                  <li>uporabniškim imenom <strong>{html.escape(prosnja.uporabnisko_ime)}</strong>,</li>
                  <li>e-poštnim naslovom ali</li>
                  <li>klicnim znakom.</li>
                </ul>
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
        (
            f"Odobrena prošnja {prosnja.id}: clan_id={clan.id}, "
            f"uporabnisko_ime={uporabnik.uporabnisko_ime}"
        ),
        ip=request.client.host if request.client else None,
    )

    return RedirectResponse(
        url="/dostop/prosnje?sporocilo=Prošnja je odobrena.",
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
        return RedirectResponse(url="/clani", status_code=302)

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
                <p>Za dodatne informacije se obrnite na administratorja kluba.</p>
                """,
            )
        except Exception:
            logger.exception("Pošiljanje obvestila o zavrnitvi ni uspelo.")

    log_akcija(
        db,
        user.get("uporabnisko_ime"),
        "dostop_zavrnjen",
        f"Zavrnjena prošnja {prosnja.id}",
        ip=request.client.host if request.client else None,
    )

    return RedirectResponse(
        url="/dostop/prosnje?sporocilo=Prošnja je zavrnjena.",
        status_code=302,
    )