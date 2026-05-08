from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from .models import LoginPoizkus

_MAX_ATTEMPTS = 10
_LOCKOUT_SECONDS = 900


def check_rate_limit(ip: str, db: Session, uporabnisko_ime: str | None = None) -> bool:
    """Vrne True če je dostop dovoljen, False če je zaklenjen. Čisti stare vnose.

    Preveri oba: IP in (opcijsko) uporabniško ime, da zaščiti pred porazdeljenimi napadi
    in hkrati prepreči zaklepanje deljivih NAT-ov po imenu računa namesto po IP.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_LOCKOUT_SECONDS)
    db.query(LoginPoizkus).filter(LoginPoizkus.cas < cutoff).delete(synchronize_session=False)
    db.commit()

    count_ip = db.query(LoginPoizkus).filter(
        LoginPoizkus.ip == ip,
        LoginPoizkus.cas >= cutoff,
    ).count()
    if count_ip >= _MAX_ATTEMPTS:
        return False

    if uporabnisko_ime:
        count_user = db.query(LoginPoizkus).filter(
            LoginPoizkus.uporabnisko_ime == uporabnisko_ime,
            LoginPoizkus.cas >= cutoff,
        ).count()
        if count_user >= _MAX_ATTEMPTS:
            return False

    return True


def record_failed_attempt(ip: str, db: Session, uporabnisko_ime: str | None = None) -> None:
    db.add(LoginPoizkus(ip=ip, uporabnisko_ime=uporabnisko_ime, cas=datetime.now(timezone.utc)))
    db.commit()
