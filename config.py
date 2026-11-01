"""La configuración de la app: el archivo `.env` junto a este archivo.

Nadie tiene que editarlo a mano: lo escribe el formulario de Configuración de
la app (`guardar`). El `.env` está en `.gitignore` y nunca se sube al repo.

La clave del PJN NO se guarda: la app la pide al abrir y vive sólo en memoria.
"""

import os
from pathlib import Path

from dotenv import dotenv_values

DIR_APP = Path(__file__).resolve().parent
ENV_PATH = DIR_APP / ".env"

# nombre, etiqueta, ¿obligatorio?, ¿secreto?, valor por defecto
CAMPOS = [
    ("GMAIL_USUARIO", "Dirección de Gmail", True, False, ""),
    ("GMAIL_CLAVE_APP", "Contraseña de aplicación de Gmail", True, True, ""),
    ("PJN_USUARIO", "Usuario del PJN (CUIT/CUIL)", True, False, ""),
    ("CARPETA_DATOS", "Carpeta donde se guardan el Excel y los PDFs", False, False, "datos"),
    ("DIAS_ATRAS", "Días hacia atrás para leer mails", False, False, "7"),
    ("FILTRO_CARATULA", "Sólo causas cuya carátula contenga (opcional)", False, False, ""),
    ("DESCARGAR_PDFS", "Descargar los PDFs de las actuaciones", False, False, "1"),
]
SECRETOS = {n for n, _, _, s, _ in CAMPOS if s}
DEFECTOS = {n: d for n, _, _, _, d in CAMPOS}

ENCABEZADO = """\
# Configuración de PJN Novedades — la genera la app (Configuración). NO compartir.
# Este archivo tiene la contraseña de aplicación de Gmail: no lo subas a ningún lado.
# La clave del PJN no se guarda: la app la pide al abrir.
"""


def leer() -> dict:
    """La configuración actual, con los valores por defecto donde falten."""
    valores = dict(DEFECTOS)
    if ENV_PATH.exists():
        valores.update({k: (v or "") for k, v in dotenv_values(ENV_PATH).items() if k in DEFECTOS})
    return valores


def faltantes(cfg: dict) -> list[str]:
    """Las etiquetas de los campos obligatorios vacíos."""
    return [etiqueta for n, etiqueta, oblig, _, _ in CAMPOS if oblig and not (cfg.get(n) or "").strip()]


def _comillas(valor: str) -> str:
    """Valor entre comillas simples para dotenv: literal, salvo \\\\ y \\'."""
    return "'" + valor.replace("\\", "\\\\").replace("'", "\\'") + "'"


def validar(nuevos: dict) -> list[str]:
    errores = []
    gmail = (nuevos.get("GMAIL_USUARIO") or "").strip()
    if gmail and "@" not in gmail:
        errores.append("La dirección de Gmail no parece válida")
    clave = (nuevos.get("GMAIL_CLAVE_APP") or "").replace(" ", "")
    if clave and len(clave) != 16:
        errores.append("La contraseña de aplicación de Gmail tiene 16 letras (sin contar espacios)")
    cuit = "".join(c for c in (nuevos.get("PJN_USUARIO") or "") if c.isdigit())
    if nuevos.get("PJN_USUARIO") and len(cuit) != 11:
        errores.append("El usuario del PJN es el CUIT/CUIL: 11 números")
    dias = (nuevos.get("DIAS_ATRAS") or "").strip()
    if dias and (not dias.isdigit() or not 1 <= int(dias) <= 90):
        errores.append("Los días hacia atrás van de 1 a 90")
    return errores


def guardar(nuevos: dict) -> dict:
    """Escribe el `.env` con los valores del formulario y retorna la config.

    Un secreto que llega vacío conserva el valor guardado (el formulario no
    lo muestra, sólo dice si ya hay uno). Lanza ValueError si algo no valida.
    """
    errores = validar(nuevos)
    if errores:
        raise ValueError(". ".join(errores))
    actual = leer()
    final = {}
    for n, _, _, secreto, _ in CAMPOS:
        valor = nuevos.get(n)
        if valor is None or (secreto and not str(valor).strip()):
            valor = actual.get(n, "")
        valor = str(valor).strip()
        if n == "GMAIL_CLAVE_APP":
            valor = valor.replace(" ", "")
        if n == "PJN_USUARIO":
            valor = "".join(c for c in valor if c.isdigit())
        final[n] = valor

    lineas = [ENCABEZADO]
    for n, etiqueta, _, _, _ in CAMPOS:
        lineas.append(f"# {etiqueta}")
        lineas.append(f"{n}={_comillas(final[n])}")
    tmp = ENV_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_PATH)
    return leer()


def vista_publica(cfg: dict) -> dict:
    """La config para mostrar en el formulario: los secretos no salen de la PC."""
    out = {n: ("" if n in SECRETOS else cfg.get(n, "")) for n in DEFECTOS}
    out["_guardados"] = {n: bool(cfg.get(n)) for n in SECRETOS}
    out["_carpeta_absoluta"] = str(carpeta_datos(cfg))
    return out


# ---------------------------------------------------------------------------
# Derivados
# ---------------------------------------------------------------------------

def carpeta_datos(cfg: dict) -> Path:
    """La carpeta de datos, absoluta (una relativa cuelga de la carpeta de la app)."""
    p = Path(os.path.expandvars(os.path.expanduser(cfg.get("CARPETA_DATOS") or "datos")))
    return p if p.is_absolute() else DIR_APP / p


def ruta_db(cfg: dict) -> Path:
    return carpeta_datos(cfg) / "pjn_novedades.db"


def ruta_excel(cfg: dict) -> Path:
    return carpeta_datos(cfg) / "Novedades PJN.xlsx"


def carpeta_pdfs(cfg: dict) -> Path:
    return carpeta_datos(cfg) / "PDFs"


def dias_atras(cfg: dict) -> int:
    try:
        return max(1, min(90, int(cfg.get("DIAS_ATRAS") or 7)))
    except ValueError:
        return 7


def filtros_caratula(cfg: dict) -> list[str]:
    """Las palabras del filtro, separadas por coma. Vacío = todas las causas."""
    return [p.strip() for p in (cfg.get("FILTRO_CARATULA") or "").split(",") if p.strip()]


def descargar_pdfs(cfg: dict) -> bool:
    return (cfg.get("DESCARGAR_PDFS") or "1").strip() not in ("0", "no", "false", "")
