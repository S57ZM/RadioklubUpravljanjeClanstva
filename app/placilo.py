from __future__ import annotations

import io
import re
from dataclasses import dataclass

import segno
from sqlalchemy.orm import Session

from .config import get_clanarina_zneski, get_nastavitev
from .models import Clan, ZrsClanarina


@dataclass(frozen=True)
class PlaciloPodatki:
    clan_id: int
    leto: int
    placnik: str
    klicni_znak: str
    ulica_placnika: str
    kraj_placnika: str
    prejemnik: str
    ulica_prejemnika: str
    kraj_prejemnika: str
    iban: str
    bic: str
    referenca: str
    namen: str
    opis: str
    klub_znesek: float
    zrs_znesek: float
    zrs_vrsta: str
    skupaj: float


def _normaliziraj_iban(vrednost: str) -> str:
    return re.sub(r"\s+", "", vrednost or "").upper()


def _normaliziraj_bic(vrednost: str) -> str:
    return re.sub(r"\s+", "", vrednost or "").upper()


def _veljaven_iban(iban: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{13,32}", iban):
        return False
    preurejen = iban[4:] + iban[:4]
    stevilski = "".join(
        znak if znak.isdigit() else str(ord(znak) - 55)
        for znak in preurejen
    )
    ostanek = 0
    for znak in stevilski:
        ostanek = (ostanek * 10 + int(znak)) % 97
    return ostanek == 1


def pripravi_placilo(
    db: Session,
    clan: Clan,
    leto: int,
) -> PlaciloPodatki:
    zneski = get_clanarina_zneski(db)
    osnovni = (
        float(zneski.get(clan.tip_clanstva) or 0.0)
        if clan.tip_clanstva
        else 0.0
    )

    zrs = (
        db.query(ZrsClanarina)
        .filter(
            ZrsClanarina.clan_id == clan.id,
            ZrsClanarina.leto == leto,
        )
        .first()
    )

    klub_znesek = (
        float(zrs.klub_znesek)
        if zrs and zrs.klub_znesek is not None
        else osnovni
    )
    zrs_znesek = (
        float(zrs.zrs_znesek or 0.0)
        if zrs
        else 0.0
    )
    zrs_vrsta = zrs.zrs_vrsta if zrs else "Brez ZRS"

    ref_predloga = get_nastavitev(
        db,
        "upn_referenca_predloga",
        "SI00 {id}-{leto}",
    )
    referenca = (
        ref_predloga
        .replace("{leto}", str(leto))
        .replace("{id}", str(clan.id))
        .replace(
            "{es}",
            str(clan.es_stevilka) if clan.es_stevilka else "",
        )
    )

    opis_predloga = get_nastavitev(
        db,
        "upn_opis_predloga",
        "Članarina {leto}",
    )
    opis = opis_predloga.replace("{leto}", str(leto))

    if zrs_znesek > 0:
        dopis = get_nastavitev(
            db,
            "zrs_opis_dopis",
            " + ZRS članarina ({zrs_vrsta})",
        )
        opis += dopis.replace("{zrs_vrsta}", zrs_vrsta or "")

    return PlaciloPodatki(
        clan_id=clan.id,
        leto=leto,
        placnik=f"{clan.priimek} {clan.ime}".strip(),
        klicni_znak=(clan.klicni_znak or "").strip(),
        ulica_placnika=(clan.naslov_ulica or "").strip(),
        kraj_placnika=(clan.naslov_posta or "").strip(),
        prejemnik=get_nastavitev(db, "klub_ime", "").strip(),
        ulica_prejemnika=get_nastavitev(
            db,
            "klub_naslov",
            "",
        ).strip(),
        kraj_prejemnika=get_nastavitev(
            db,
            "klub_posta",
            "",
        ).strip(),
        iban=_normaliziraj_iban(
            get_nastavitev(db, "klub_iban", "")
        ),
        bic=_normaliziraj_bic(
            get_nastavitev(db, "klub_bic", "")
        ),
        referenca=referenca.strip(),
        namen=get_nastavitev(
            db,
            "upn_namen",
            "MEMB",
        ).strip().upper()[:4],
        opis=opis.strip(),
        klub_znesek=round(klub_znesek, 2),
        zrs_znesek=round(zrs_znesek, 2),
        zrs_vrsta=zrs_vrsta or "Brez ZRS",
        skupaj=round(klub_znesek + zrs_znesek, 2),
    )


def napake_placila(podatki: PlaciloPodatki) -> list[str]:
    napake: list[str] = []

    if not podatki.prejemnik:
        napake.append("V nastavitvah manjka ime kluba.")
    if not podatki.iban:
        napake.append("V nastavitvah manjka IBAN kluba.")
    elif not _veljaven_iban(podatki.iban):
        napake.append("IBAN kluba ni veljaven.")
    if podatki.bic and not re.fullmatch(
        r"[A-Z0-9]{8}([A-Z0-9]{3})?",
        podatki.bic,
    ):
        napake.append("BIC/SWIFT ni v veljavnem formatu.")
    if podatki.skupaj < 0.01:
        napake.append(
            "Skupni znesek mora biti najmanj 0,01 EUR."
        )

    return napake


def epc_vsebina(podatki: PlaciloPodatki) -> str:
    napake = napake_placila(podatki)
    if napake:
        raise ValueError(" ".join(napake))

    namen = re.sub(
        r"[^A-Z0-9]",
        "",
        podatki.namen.upper(),
    )[:4]

    podrobnosti = [podatki.opis]
    if podatki.referenca:
        podrobnosti.append(podatki.referenca)
    if podatki.klicni_znak:
        podrobnosti.append(podatki.klicni_znak)

    neoblikovan_namen = " | ".join(
        delcek for delcek in podrobnosti if delcek
    )[:140]

    vrstice = [
        "BCD",
        "002",
        "1",
        "SCT",
        podatki.bic,
        podatki.prejemnik[:70],
        podatki.iban[:34],
        f"EUR{podatki.skupaj:.2f}",
        namen,
        "",
        neoblikovan_namen,
    ]

    vsebina = "\n".join(vrstice)
    if len(vsebina.encode("utf-8")) > 331:
        raise ValueError(
            "SEPA QR vsebuje preveč podatkov (največ 331 bajtov)."
        )
    return vsebina


def _epc_qr(podatki: PlaciloPodatki) -> segno.QRCode:
    vsebina = epc_vsebina(podatki)
    qr = segno.make(
        vsebina.encode("utf-8"),
        error="m",
        mode="byte",
    )
    if isinstance(qr.version, int) and qr.version > 13:
        raise ValueError(
            "SEPA QR presega največjo dovoljeno različico 13."
        )
    return qr


def generiraj_epc_svg(podatki: PlaciloPodatki) -> str:
    qr = _epc_qr(podatki)
    buffer = io.BytesIO()
    qr.save(
        buffer,
        kind="svg",
        scale=6,
        border=4,
    )
    return buffer.getvalue().decode("utf-8")


def generiraj_epc_png(podatki: PlaciloPodatki) -> bytes:
    qr = _epc_qr(podatki)
    buffer = io.BytesIO()
    qr.save(
        buffer,
        kind="png",
        scale=8,
        border=4,
    )
    return buffer.getvalue()
