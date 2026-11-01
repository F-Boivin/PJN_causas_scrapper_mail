"""Motor de scraping del SCW (consulta de causas del PJN) con Playwright.

Login por Keycloak, lectura de los datos generales y de la tabla de
actuaciones (con paginación), descarga de documentos, normalización de fechas
y la clave de deduplicación de una actuación.
"""

import hashlib
import os
import re
import sys
import time
from datetime import datetime

# La consola de Windows (cp1252) no imprime cualquier carácter de un rótulo del
# PJN: mejor un "?" que un UnicodeEncodeError a mitad de un barrido.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

LOGIN_URL = (
    "https://sso.pjn.gov.ar/auth/realms/pjn/protocol/openid-connect/auth"
    "?client_id=pjn-portal"
    "&redirect_uri=https%3A%2F%2Fportalpjn.pjn.gov.ar%2F"
    "&state=test123"
    "&response_mode=fragment"
    "&response_type=code"
    "&scope=openid"
    "&nonce=test456"
)


def log(msg: str, level: str = "info"):
    """Log simple a consola con hora y nivel (la app lo muestra en vivo)."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level.upper():5}] {msg}", flush=True)


def normalizar_fecha(fecha_raw: str) -> str:
    """Fecha a d/m/aaaa sin ceros a la izquierda (como la usa la clave de dedup).

    Acepta d/m/aaaa, dd/mm/aaaa, d/m/aa, dd-mm-aaaa y aaaa-mm-dd.
    """
    fecha = (fecha_raw or "").strip()
    if not fecha:
        return ""
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(fecha, fmt)
            return f"{dt.day}/{dt.month}/{dt.year}"
        except ValueError:
            continue
    return fecha


def fecha_iso(fecha_raw: str) -> str:
    """Cualquier formato de `normalizar_fecha` a aaaa-mm-dd, o "" si no parsea."""
    try:
        return datetime.strptime(normalizar_fecha(fecha_raw), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


_RE_EXPEDIENTE = re.compile(r"([A-Za-z]{2,4})\s+0*(\d+)\s*/\s*(\d{4})")


def codigo_expediente(texto: str) -> str:
    """"CAF 099001/2026 (algo)" o "CAF 99001/2026" → "CAF 99001/2026".

    Es la forma única con que se guarda cada causa, venga del mail o del SCW.
    """
    m = _RE_EXPEDIENTE.search(texto or "")
    if not m:
        return (texto or "").strip()
    return f"{m.group(1).upper()} {int(m.group(2))}/{m.group(3)}"


def compute_clave(expediente: str, fecha_norm: str, tipo: str, detalle: str, a_fs: str) -> str:
    """Clave de deduplicación de una actuación (md5 hex).

    `fecha_norm` debe ser SIEMPRE el output de normalizar_fecha() para que el
    hash coincida entre corridas.
    """
    base = "|".join((s or "").strip() for s in (expediente, fecha_norm, tipo, detalle, a_fs))
    return hashlib.md5(base.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Login / sesión PJN
# ---------------------------------------------------------------------------

class CredencialesInvalidas(Exception):
    pass


def login_pjn(page, cuit: str, password: str):
    """Login en el PJN vía Keycloak."""
    log("Ingresando al PJN...")
    page.goto(LOGIN_URL)
    page.wait_for_load_state("networkidle")
    page.fill("#username", cuit)
    page.fill("#password", password)
    page.click("#kc-login")

    # Keycloak no redirige si la clave es mala: sin esto se esperaba 30 s a un timeout mudo.
    limite = time.monotonic() + 30
    while time.monotonic() < limite:
        if "portalpjn.pjn.gov.ar" in page.url:
            log("Login exitoso", "ok")
            return
        if page.query_selector("#input-error, .alert-error, .kc-feedback-text, .pf-m-danger"):
            raise CredencialesInvalidas("Usuario o contraseña del PJN incorrectos")
        time.sleep(0.5)
    raise Exception("El login del PJN no respondió en 30 s")


def abrir_causa(page, url: str, cuit: str, password: str):
    """Navega a la URL de una causa; re-loguea si la sesión expiró."""
    page.goto(url, wait_until="networkidle", timeout=30000)
    try:
        page.wait_for_selector("#expediente\\:action-table", timeout=15000)
    except Exception:
        if "sso.pjn.gov.ar" in page.url:
            log("Sesión expirada, volviendo a ingresar...", "warn")
            page.fill("#username", cuit)
            page.fill("#password", password)
            page.click("#kc-login")
            page.wait_for_selector("#expediente\\:action-table", timeout=15000)
        else:
            raise Exception(f"No se pudo abrir la causa: {page.url}")


# ---------------------------------------------------------------------------
# Extracción (todo con page.evaluate: datos planos, sin handles que pierdan memoria)
# ---------------------------------------------------------------------------

def extraer_datos_generales(page) -> dict:
    """Expediente, jurisdicción, dependencia, situación y carátula del fieldset."""
    campos = page.evaluate(r"""() => {
        const fs = document.querySelector('fieldset');
        if (!fs) return [];
        const out = [];
        for (const fg of fs.querySelectorAll('.form-group')) {
            const l = fg.querySelector('label');
            const v = fg.querySelector('.col-xs-10 span, .col-xs-10 .form_control');
            if (l && v) out.push([l.innerText.trim(), v.innerText.trim()]);
        }
        return out;
    }""")
    datos = {}
    for label, value in campos:
        label = label.rstrip(":")
        if label == "Expediente":
            datos["expediente"] = value.split("(")[0].strip()
        elif "Jurisdicci" in label:
            datos["jurisdiccion"] = value
        elif "Dependencia" in label:
            datos["dependencia"] = value
        elif "Sit" in label and "Actual" in label:
            datos["sit_actual"] = value
        elif "Car" in label and "tula" in label:
            datos["caratula"] = value
    return datos


def extraer_actuaciones(page) -> list[dict]:
    """Las actuaciones de la página actual de la tabla.

    El texto de cada celda viene en `span.font-color-black`, salvo en las filas
    que el portal muestra en negrita (`span.font-negrita`): son las de la
    alzada (Cámara, Corte Suprema). Se marcan con `negrita`.
    """
    filas = page.evaluate(r"""() => [...document.querySelectorAll('#expediente\\:action-table tbody tr')]
        .map(tr => {
            const tds = tr.querySelectorAll('td');
            if (tds.length < 6) return null;
            const texto = i => {
                const el = tds[i].querySelector('span.font-color-black, span.font-negrita');
                return el ? el.innerText.trim() : '';
            };
            const a = tds[0].querySelector("a[href*='viewer.seam']")
                   || tds[0].querySelector("a[id*='j_idt162']");
            return [texto(2), texto(3), texto(4), texto(5), a ? (a.getAttribute('href') || '') : '',
                    tr.querySelector('span.font-negrita') ? 1 : 0];
        }).filter(Boolean)""")

    actuaciones = []
    for fecha, tipo, detalle, a_fs, href, negrita in filas:
        link = ""
        if href:
            link = ("https://scw.pjn.gov.ar" + href) if href.startswith("/") else href
        actuaciones.append({"fecha": fecha, "tipo": tipo, "detalle": detalle, "a_fs": a_fs,
                            "link_archivo": link, "negrita": negrita})
    return actuaciones


# Paginador de la tabla de actuaciones (15 filas por página, la más nueva primero).
# Los ids j_idt* cambian entre versiones del SCW: se ubica como el primer
# ul.pagination que sigue a la tabla en el DOM (hay otro más abajo, de otra grilla).
_XPATH_PAGINADOR = ("//table[@id='expediente:action-table']"
                    "/following::ul[contains(@class,'pagination')][1]")

_JS_PAGINA_ACTIVA = """(esperada) => {
    const li = document.evaluate(
        "%s//li[contains(@class,'active')]", document, null,
        XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    return !!li && li.textContent.trim() === String(esperada);
}""" % _XPATH_PAGINADOR


def ir_a_pagina_siguiente(page) -> bool:
    """Avanza la tabla de actuaciones a la página siguiente (ajax RichFaces).

    Retorna False si no hay página siguiente. Espera a que el paginador marque
    la página nueva como activa: la tabla se re-renderiza en la misma respuesta.
    """
    paginador = page.query_selector("xpath=" + _XPATH_PAGINADOR)
    if not paginador:
        return False
    siguiente = paginador.query_selector("a:has(span[title='Siguiente'])")
    if not siguiente:
        return False

    activa = paginador.query_selector("li.active")
    try:
        actual = int(activa.inner_text().strip()) if activa else 1
    except ValueError:
        actual = 1

    siguiente.click()
    page.wait_for_function(_JS_PAGINA_ACTIVA, arg=actual + 1, timeout=20000)
    page.wait_for_selector("#expediente\\:action-table tbody tr", timeout=15000)
    return True


# ---------------------------------------------------------------------------
# Descarga de documentos
# ---------------------------------------------------------------------------

def descargar_archivo(page, download_url: str, destino: str) -> str | None:
    """Descarga un documento del SCW a `destino` (ruta completa). Retorna la ruta o None."""
    if not download_url:
        return None
    try:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with page.expect_download(timeout=30000) as download_info:
            page.evaluate("url => window.open(url)", download_url)
        download_info.value.save_as(destino)
        return destino
    except Exception as e:
        log(f"No se pudo descargar el documento: {e}", "warn")
        return None
