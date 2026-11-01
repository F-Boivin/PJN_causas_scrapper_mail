"""Qué actuaciones merecen una alerta: el primer auto y la sentencia.

Las reglas vienen del proyecto de relevamiento de sentencias de ejecuciones
fiscales (CAF y CSS, 2023-2026), calibradas contra cientos de miles de
actuaciones reales. Se copian tal cual: si cambian allá, se traen a mano y se
corre `python -m unittest` (los casos de `tests/` salen del mismo corpus).

Dos alertas:

- **Sentencia**: `clasificar_sentencia()` devuelve "sentencia" (el despacho del
  juzgado que la dicta), "indicio" (la nombra sin serla: un cambio de estado,
  una notificación, el llamamiento de autos) o "" (no tiene que ver, o es un
  escrito de parte que la pide). Sólo "sentencia" dispara la alerta.
- **Primer auto**: `es_primer_auto()` reconoce el despacho que tiene por
  presentada la demanda y ordena el mandamiento de intimación de pago. Es un
  concepto del juicio ejecutivo, así que sólo se busca en ejecuciones
  (fiscales o tributarias) y en FIRMA DESPACHO.

Ojo: en ejecución fiscal la sentencia de trance y remate casi nunca dice
SENTENCIA. Se dicta declarando EXPEDITA la vía (art. 92 ley 11.683), y el tipo
de la actuación es siempre FIRMA DESPACHO: toda la señal está en el detalle.
"""

import re
import unicodedata
from functools import lru_cache


def norm(texto: str) -> str:
    """Mayúsculas, sin acentos, espacios colapsados."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().upper()


# --- Carátula ---------------------------------------------------------------
# "S/ EJECUCION FISCAL", "S/ EJEC.FISCAL", "EJECUCION FISCAL - AFIP - DGI"...
# Exige FISCAL pegado a EJEC..., así "EJECUCION DE HONORARIOS" no entra. Se
# suma "EJECUCION TRIBUTARIA", que es como CAF rotula desde 2025 las de otros
# organismos: también tienen primer auto.
RE_EJECUCION = re.compile(r"EJEC\w*\.?\s*FISCAL(?:ES)?|EJECUCION\s+TRIBUTARIA")


def es_ejecucion(caratula: str) -> bool:
    return bool(RE_EJECUCION.search(norm(caratula)))


# --- Lo que cambia de un fuero a otro ----------------------------------------
# CAF es el fuero sobre el que se calibró lo común. En CSS (Seguridad Social)
# la sentencia de trance y remate se rotula STR, y el despacho digital SEI; el
# cambio de estado se carga como EVENTO. Un fuero sin entrada usa lo común.
FUEROS = {
    "CSS": {
        "SENTENCIA": (r"\bSTR\b", r"\bSEI\b"),
        "NO_ES_SENTENCIA": (r"RECIBID|DEVUEL|DEVOLUCION", r"\bDESISTE\b"),
        "TRAMO_SENTENCIA": r"^(?:[^A-Z]|SEC\b|S\d+\b|F\b)*SEI\b",
        "TIPOS_INDICIO": frozenset({"EVENTO"}),
    },
}
_FUERO_COMUN = {"SENTENCIA": (), "NO_ES_SENTENCIA": (), "TRAMO_SENTENCIA": "",
                "TIPOS_INDICIO": frozenset()}


def sigla_fuero(expediente: str) -> str:
    """"CSS 12345/2025" → "CSS"."""
    m = re.match(r"\s*([A-Za-z]{2,4})\b", expediente or "")
    return m.group(1).upper() if m else ""


# --- Sentencia ---------------------------------------------------------------

# Movimientos internos del sistema: sin documento, prueban que hubo sentencia.
TIPOS_INDICIO = {"MOVIMIENTO", "CAMBIO DE ESTADO DE EXPEDIENTE"}
# Repite una sentencia ya cargada como FIRMA DESPACHO.
TIPOS_DUPLICADOS = {"PUBLICACION SENTENCIA"}
# Nunca son un despacho del juzgado: "SE DICTE SENTENCIA" es la parte pidiéndola.
RE_TIPO_AJENO = re.compile(r"^ESCRITO\b|CEDULA|OFICIO|MANDAMIENTO|^EVENTO\b|^DEO\b")

PATRONES_SENTENCIA = [
    r"\bEXPEDITA\b",
    # Sin \b a la izquierda: "MJFSENTENCIA DE REMATE", "*SENTENCIA ARCA".
    r"SENTENCIA",
    r"TRANCE\s+Y\s+REMATE",
    r"LLEVAR\s+ADELANTE\s+LA\s+EJECUCION",
]

# Llamamiento de autos: la causa quedó a resolver, la sentencia no está dictada.
PATRONES_LLAMAMIENTO = [
    r"AUTOS\s+(?:PARA|A)\s+(?:DICTAR\s+)?SENTENCIA",
    r"\bA\s+SENTENCIA\b",
]

# Pedidos y pasos de trámite: "ACT SOLICITA EXPEDITA, SALE PREVIO",
# "CONVENIO- PLAN DE PAGOS- PASA SENTENCIA".
PATRONES_PEDIDO_O_PASO = [
    r"\bSOLICITA",
    r"NO\s+CORRESPONDE",
    r"\bPASA\s+(?:A\s+)?(?:DICTAR\s+)?(?:LA\s+)?(?:SENTENCIA|EXPEDITA)",
]

# El rótulo EMPIEZA con SENTENCIA o EXPEDITA tras el prefijo de secretaría
# ("SEC 18 - ", "5 SEC - ", "F ", "S13", "***"): ahí la sentencia es el acto.
RE_EMPIEZA_CON_SENTENCIA = re.compile(
    r"^(?:[^A-Z]|SEC\b|S\d+\b|F\b)*(?:[A-Z]{0,4}SENTENCIA|EXPEDITA)\b")

# La mencionan sin serla: apuntan al verbo del despacho (agregar, archivar...).
PATRONES_NO_ES_SENTENCIA = [
    r"NOTIFICAC",
    r"CONSTANCIA",
    r"ACREDITA",
    r"AGREGUESE",
    r"\bAGREGA\b",
    r"ACOMPA",
    r"TRANSFERENCIA",
    r"ANTERIOR\s+A\s+LA",
    r"FUERO\s+DE\s+ATRACCION",
    r"\bCEDULA\b",
    r"ARCHIV",
    r"EJECUCION\s+DE\s+SENTENCIA",
]

# El despacho que pide algo antes de dictarla, o la pasa a dictar: "PREVIO A
# PROVEER LA EXPEDITA DEBERÁ ACLARAR...", "PASEN LOS AUTOS A EXPEDITA".
# "PROCEDIMIENTO PREVIO" no: es la sentencia que resuelve una excepción.
RE_PREVIO_A_LA_SENTENCIA = re.compile(
    r"(?<!PROCEDIMIENTO )\bPR?EVIO\b|\bDEBE\w*\s+ACLARAR"
    r"|\bPASEN?\b|\bPASA\s+A\b|\bA\s+DICTAR\b|\bAL\s+DICTADO\b|\bPARA\s+DICTAR\b"
    r"|VISTA\s+AL\s+FISCAL|MAL\s+NOMBRE\s+DEL?\s+JUEZ|\bPIDE\s+SENT")

# Rótulo que junta dos actos con la sentencia en el segundo: "ARCHIVO -
# SENTENCIA SEXTO PARRAFO". Se parte por " - " o " / ".
RE_TRAMO = re.compile(r"\s+-\s+|\s+/\s+")

# El movimiento que dice sólo "SENTENCIA INTERLOCUTORIA", sin documento.
RE_SOLO_INTERLOCUTORIA = re.compile(r"^\W*SENTENCIA\s+INTERLOCUTORIA\W*$")

# El despacho que dice que no hubo sentencia o que no corresponde.
RE_NO_TUVO_SENTENCIA = re.compile(
    r"SIN SENTENCIA|NO TUVO SENTENCIA|NO TIENE SENTENCIA"
    r"|MEJOR\s+PROVEER"
    r"|NO\s+HA\s+LUGAR\s+(?:EL\s+|AL\s+)?PEDIDO\s+DE\s+SENTENCIA"
    r"|PIDE\s+SENTENCIA\s*-\s*NO\s+HA\s+LUGAR"
    r"|NO\s+CORRESP\w*\s+SENTENCIA|\bNI\s+SENTENCIA\b|NO\s+PIDE\s+EXPEDITA"
    r"|AL\s+PEDIDO\s+DE\s+EXPEDITA\s+ESTESE"
    r"|\bREDU(?:CE|CCION)\b.*\bANTES\s+DE\s+(?:LA\s+)?SENTENCIA"
    r"|^\W*SENTENCIA\s*-\s*PREVIO\b")


@lru_cache(maxsize=None)
def _patrones(sigla: str) -> dict:
    """Los patrones comunes más los del fuero, compilados."""
    f = FUEROS.get(sigla, _FUERO_COMUN)
    return {
        "sentencia": [re.compile(p) for p in (*PATRONES_SENTENCIA, *f["SENTENCIA"])],
        "llamamiento": [re.compile(p) for p in PATRONES_LLAMAMIENTO],
        "pedido": [re.compile(p) for p in PATRONES_PEDIDO_O_PASO],
        "no_sentencia": [re.compile(p) for p in (*PATRONES_NO_ES_SENTENCIA,
                                                 *f["NO_ES_SENTENCIA"])],
        "tramo": re.compile(f["TRAMO_SENTENCIA"]) if f["TRAMO_SENTENCIA"] else None,
        "tipos_indicio": f["TIPOS_INDICIO"],
    }


def _sentencia_en_otro_tramo(d: str, p: dict) -> bool:
    """Si un tramo del rótulo que no es el primero es la sentencia."""
    tramos = [t for t in RE_TRAMO.split(d) if t.strip()]
    for i, t in enumerate(tramos[1:], 1):
        if not (RE_EMPIEZA_CON_SENTENCIA.search(t)
                or (p["tramo"] is not None and p["tramo"].search(t))):
            continue
        resto = " - ".join(tramos[i:])
        if (any(r.search(resto) for r in (*p["llamamiento"], *p["pedido"], *p["no_sentencia"]))
                or RE_PREVIO_A_LA_SENTENCIA.search(resto)):
            continue
        return True
    return False


def _clasificar(t: str, d: str, p: dict) -> str:
    if not any(r.search(d) for r in p["sentencia"]):
        return ""
    if (t in TIPOS_INDICIO or t in p["tipos_indicio"]) and RE_SOLO_INTERLOCUTORIA.search(d):
        return ""
    # Antes del filtro por tipo: lo que en un fuero es marca de estado puede
    # ser un tipo ajeno en el otro (EVENTO).
    if t in p["tipos_indicio"]:
        return "indicio"
    if RE_TIPO_AJENO.search(t):
        return ""
    if t in TIPOS_INDICIO or t in TIPOS_DUPLICADOS:
        return "indicio"
    if any(r.search(d) for r in p["llamamiento"]):
        return "indicio"
    if RE_EMPIEZA_CON_SENTENCIA.search(d):
        return "sentencia"
    if any(r.search(d) for r in p["pedido"]):
        return "indicio"
    if any(r.search(d) for r in p["no_sentencia"]):
        if t == "FIRMA DESPACHO" and _sentencia_en_otro_tramo(d, p):
            return "sentencia"
        return "indicio"
    if RE_PREVIO_A_LA_SENTENCIA.search(d):
        return ""
    return "sentencia"


def clasificar_sentencia(tipo: str, detalle: str, fuero: str = "") -> str:
    """"sentencia", "indicio" o "" para una actuación del fuero `fuero` (sigla)."""
    d = norm(detalle)
    clase = _clasificar(norm(tipo), d, _patrones(fuero.upper()))
    if clase == "sentencia" and RE_NO_TUVO_SENTENCIA.search(d):
        return ""
    return clase


# --- Primer auto -------------------------------------------------------------
# Se rotula de mil maneras ("PRIMER AUTO", "1ER AUTO ANTICIPATORIO FISCO",
# "INICIO AÑO 2025 SEC 6", "*AUTO* - DIGITALES - ARCA - INICIAL EJECUCION"), y
# dos clases de rótulo lo nombran sin serlo:
# - el previo: "SEC 18 - PRIMER AUTO - PREVIO ACLARE TIPO SOCIETARIO",
#   "REQUIERE JUSTIFIQUE INICIO EJECUCION". "Aclare" no es previo cuando el
#   mismo despacho provee la demanda ("PRIMER DESPACHO -SE PROVEE DEMANDA-
#   ACLARE TIPO SOCIETARIO");
# - el posterior, que lo cita, lo anula o resuelve un recurso contra él:
#   "ESTESE AL AUTO ANTICIPATORIO", "DEJESE SIN EFECTO EL PRIMER AUTO".
#   "DÉJESE SIN EFECTO. 1° AUTO (DISP. 120/2025)" sí lo es: lo vuelve a dictar.
# En la sucesión, el primer auto es el que provee ("SUCESION VUELVE DEL FISCAL
# SOMOS COMPETENTES"). "DESPACHO - LETRA PRIMER AUTO" es un despacho de trámite.
RE_PRIMER_AUTO = re.compile(r"PRIMER\s+(?:AUTO|DESPACHO|PROVEIDO)|\b1\s*(?:ER|°|O)?\s*AUTO\b"
                            r"|INICIO|INICIAL|AUTO\s+ANTICIPATORIO")
_RE_PREVIO_ROTULADO = re.compile(
    r"\bPREVIO\b|REQUIERE|JUSTIFI|SUSPEN|DEBE (?:ACLARAR|DENUNCIAR)|\bACLARE\b|INTIMACION TIPO"
    r"|ACOMPANE|RATIFI|ACREDITE|CARECE DE MATRICULA|OTRO LETRADO|DIFIERE|PREMATURO|RECHAZAR"
    r"|PASE AL FISCAL|DENUNCIE|DENUNCE|FALTA DECIR|SE PROVEERA")
_RE_PROVEE_DEMANDA = re.compile(r"SE PROVEE DEMANDA|ACLARE SECTOR")
_RE_CITA_PRIMER_AUTO = re.compile(
    r"ESTESE|NULIDAD|SIN EFECTO\W+(?:EL\s+)?PRIMER|HONORARIOS|AD EFFECTUM|REVOCA|REPOSICION"
    r"|POSTERIOR(?:IDAD)? (?:A|AL) INICIO|ANTERIOR (?:AL INICIO|1)|INICIO DE LAS PRESENTES"
    r"|TRANSFER|LEVANTAMIENTO|SENTENCIA|EXPEDITA|\bLETRA\b")
_RE_SUCESION = re.compile(r"SUCESION")
_RE_SUCESION_PROVEE = re.compile(r"COMPETENTE|POSTERIOR|EMBARGO|SE PROVEE")
_RE_SUCESION_COMPETENTE = re.compile(r"SUCESION\b.*\bSOMOS COMPETENTES|DESDOBLAR DEMANDA")

TIPO_DESPACHO = "FIRMA DESPACHO"


def es_primer_auto(detalle: str) -> bool:
    """Si el rótulo de un despacho es el del primer auto."""
    d = norm(detalle)
    if not (RE_PRIMER_AUTO.search(d) or _RE_SUCESION_COMPETENTE.search(d)):
        return False
    if _RE_CITA_PRIMER_AUTO.search(d):
        return False
    if _RE_SUCESION.search(d) and not _RE_SUCESION_PROVEE.search(d):
        return False
    return not _RE_PREVIO_ROTULADO.search(d) or bool(_RE_PROVEE_DEMANDA.search(d))


# --- La alerta de una actuación ----------------------------------------------

PRIMER_AUTO = "primer_auto"
SENTENCIA = "sentencia"
NOMBRES = {PRIMER_AUTO: "Primer auto", SENTENCIA: "Sentencia"}


def alerta(act: dict, expediente: str, caratula: str) -> str:
    """PRIMER_AUTO, SENTENCIA o "" para una actuación recién registrada.

    Sólo alertan las ejecuciones fiscales o tributarias: las reglas están
    calibradas sobre ellas. Una carátula vacía (no se pudo leer) no descarta
    la alerta. Las filas en negrita son de la alzada (Cámara, Corte): lo que
    resuelve la alzada no es la sentencia de la causa, y no alertan.
    """
    if act.get("negrita") or (caratula and not es_ejecucion(caratula)):
        return ""
    tipo, detalle = act.get("tipo", ""), act.get("detalle", "")
    if clasificar_sentencia(tipo, detalle, sigla_fuero(expediente)) == "sentencia":
        return SENTENCIA
    if norm(tipo) == TIPO_DESPACHO and es_primer_auto(detalle):
        return PRIMER_AUTO
    return ""
