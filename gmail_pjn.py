"""Lectura de los mails de novedades del PJN desde Gmail (IMAP).

El PJN manda un mail por cada novedad en una causa que el abogado sigue
("Novedad en expediente CAF 12345/2025"), desde scw-noreply@pjn.gov.ar. El
mail trae la carátula, la fecha y el link a la causa en el SCW: ese link es lo
que después abre el scraper.

Se entra por IMAP con una **contraseña de aplicación** de Google (no la clave
normal de la cuenta). No hace falta crear nada en Google Cloud, y no vence
cada 7 días como un token OAuth de una app en modo prueba. La carpeta se abre
en sólo lectura: los mails no se marcan como leídos ni se mueven.
"""

import email
import html
import imaplib
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from pjn import codigo_expediente

IMAP_HOST = "imap.gmail.com"
REMITENTE = "scw-noreply@pjn.gov.ar"
LOTE = 50  # mails por FETCH


class ErrorGmail(Exception):
    pass


# ---------------------------------------------------------------------------
# Parseo de un mail (sin red: se testea con mails armados a mano)
# ---------------------------------------------------------------------------

_RE_EXP = re.compile(r"expediente\s+([A-Za-z]{2,4})\s+(\d+)\s*/\s*(\d{4})", re.I)
_RE_CARATULA = re.compile(r"/\s*\d{4}\s*-\s*(.*?)\s*Fecha\s*:", re.I | re.S)
_RE_FECHA = re.compile(r"Fecha\s*:\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})", re.I)
_RE_LINK = re.compile(r"https?://scw\.pjn\.gov\.ar/scw/[^\s\"'<>]+", re.I)


def _texto_header(valor) -> str:
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(valor)))
    except Exception:
        return str(valor)


def _partes(msg) -> tuple[str, str]:
    """(texto plano, html) del mail, decodificados."""
    texto, htm = [], []
    for parte in msg.walk() if msg.is_multipart() else [msg]:
        tipo = parte.get_content_type()
        if tipo not in ("text/plain", "text/html") or parte.get_filename():
            continue
        payload = parte.get_payload(decode=True) or b""
        charset = parte.get_content_charset() or "utf-8"
        try:
            contenido = payload.decode(charset, errors="replace")
        except LookupError:
            contenido = payload.decode("utf-8", errors="replace")
        (texto if tipo == "text/plain" else htm).append(contenido)
    return "\n".join(texto), "\n".join(htm)


def parsear_mail(raw: bytes, msg_id: str = "") -> dict:
    """Los datos de la novedad de un mail del PJN.

    Retorna {msg_id, recibido (ISO), asunto, expediente, caratula,
    fecha_novedad (ISO o ""), link}. Los campos que no aparecen quedan vacíos.
    """
    msg = email.message_from_bytes(raw)
    asunto = _texto_header(msg.get("Subject")).strip()
    try:
        recibido = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc)
        recibido = recibido.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        recibido = ""

    texto, htm = _partes(msg)
    sin_tags = html.unescape(re.sub(r"<[^>]+>", " ", htm))
    cuerpo = re.sub(r"\s+", " ", f"{texto} {sin_tags}").strip()

    m = _RE_EXP.search(asunto) or _RE_EXP.search(cuerpo)
    expediente = codigo_expediente(f"{m.group(1)} {m.group(2)}/{m.group(3)}") if m else ""

    m = _RE_CARATULA.search(cuerpo)
    caratula = re.sub(r"[\s*]+$", "", re.sub(r"\s+", " ", m.group(1))).strip() if m else ""

    m = _RE_FECHA.search(cuerpo)
    fecha_novedad = ""
    if m:
        try:
            fecha_novedad = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # El link primero en el texto visible; si no, en el href del html.
    m = _RE_LINK.search(cuerpo) or _RE_LINK.search(html.unescape(htm))
    link = re.sub(r"[)\]>\"'.,;]+$", "", m.group(0)) if m else ""

    return {
        "msg_id": msg_id or (msg.get("Message-ID") or "").strip(),
        "recibido": recibido,
        "asunto": asunto,
        "expediente": expediente,
        "caratula": caratula,
        "fecha_novedad": fecha_novedad,
        "link": link,
    }


# ---------------------------------------------------------------------------
# IMAP
# ---------------------------------------------------------------------------

def conectar(usuario: str, clave_app: str) -> imaplib.IMAP4_SSL:
    """Abre la sesión IMAP. Lanza ErrorGmail con un mensaje para el usuario."""
    if not usuario or not clave_app:
        raise ErrorGmail("Faltan la dirección de Gmail o la contraseña de aplicación")
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=30)
    except OSError as e:
        raise ErrorGmail(f"No se pudo conectar con Gmail ({e}). ¿Hay internet?") from e
    try:
        imap.login(usuario.strip(), clave_app.replace(" ", ""))
    except imaplib.IMAP4.error as e:
        raise ErrorGmail(
            "Gmail rechazó el acceso. Revisá la dirección y que la contraseña sea una "
            "«contraseña de aplicación» de 16 letras (no la clave normal de Gmail)."
        ) from e
    return imap


_RE_LIST = re.compile(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<nombre>.+)$')


def _carpeta_todos(imap) -> str:
    """La carpeta "Todos" de Gmail (su nombre cambia con el idioma), o INBOX.

    Hay que buscar ahí y no en el recibido: muchos tienen un filtro que
    archiva o etiqueta los mails del PJN.
    """
    typ, data = imap.list()
    if typ == "OK":
        for raw in data or []:
            linea = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            m = _RE_LIST.match(linea)
            if m and "\\All" in m.group("flags"):
                return m.group("nombre").strip()
    return "INBOX"


def buscar_novedades(usuario: str, clave_app: str, dias: int,
                     ya_vistos: set[str] | None = None) -> list[dict]:
    """Los mails del PJN de los últimos `dias` días, parseados.

    `ya_vistos`: ids de mails ya registrados; no se vuelven a bajar. El id es
    el X-GM-MSGID de Gmail (estable aunque el mail cambie de etiqueta).
    """
    ya_vistos = ya_vistos or set()
    imap = conectar(usuario, clave_app)
    try:
        carpeta = _carpeta_todos(imap)
        typ, _ = imap.select(carpeta, readonly=True)
        if typ != "OK":
            raise ErrorGmail(f"No se pudo abrir la carpeta {carpeta} de Gmail")
        consulta = f'"from:{REMITENTE} newer_than:{max(1, int(dias))}d"'
        typ, data = imap.uid("SEARCH", "X-GM-RAW", consulta)
        if typ != "OK":
            raise ErrorGmail("Gmail no aceptó la búsqueda de mails del PJN")
        uids = (data[0] or b"").split()

        # Primero sólo los ids (barato), para no bajar lo que ya está registrado.
        pendientes = []
        for i in range(0, len(uids), LOTE):
            lote = b",".join(uids[i:i + LOTE]).decode()
            typ, resp = imap.uid("FETCH", lote, "(X-GM-MSGID)")
            for item in resp or []:
                linea = item.decode() if isinstance(item, bytes) else ""
                mu, mg = re.search(r"UID (\d+)", linea), re.search(r"X-GM-MSGID (\d+)", linea)
                if mu and mg and mg.group(1) not in ya_vistos:
                    pendientes.append((mu.group(1), mg.group(1)))

        novedades = []
        for i in range(0, len(pendientes), LOTE):
            lote = pendientes[i:i + LOTE]
            ids = dict(lote)
            typ, resp = imap.uid("FETCH", ",".join(u for u, _ in lote), "(UID BODY.PEEK[])")
            resp = resp or []
            for j, item in enumerate(resp):
                if not isinstance(item, tuple):
                    continue
                # El UID puede venir antes del cuerpo o en el cierre de la respuesta.
                mu = re.search(rb"UID (\d+)", item[0])
                if not mu and j + 1 < len(resp) and isinstance(resp[j + 1], bytes):
                    mu = re.search(rb"UID (\d+)", resp[j + 1])
                uid = mu.group(1).decode() if mu else ""
                if uid in ids:
                    novedades.append(parsear_mail(item[1], ids[uid]))
        return novedades
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def probar(usuario: str, clave_app: str, dias: int = 30) -> dict:
    """Para el botón "Probar conexión": cuántos mails del PJN hay y un ejemplo."""
    novedades = buscar_novedades(usuario, clave_app, dias)
    novedades.sort(key=lambda n: n["recibido"], reverse=True)
    con_link = [n for n in novedades if n["link"]]
    ejemplo = con_link[0] if con_link else (novedades[0] if novedades else None)
    return {
        "mails": len(novedades),
        "con_link": len(con_link),
        "dias": dias,
        "ejemplo": ({"expediente": ejemplo["expediente"], "caratula": ejemplo["caratula"],
                     "fecha": ejemplo["fecha_novedad"]} if ejemplo else None),
    }
