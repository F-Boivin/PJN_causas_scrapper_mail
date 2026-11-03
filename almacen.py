"""Los datos de la app: una base SQLite en la carpeta de datos.

Es la fuente de verdad de la app. Al final de cada corrida `excel.py` pone al
día el Excel desde acá, sin pisar lo que escribió la persona: si el Excel está
abierto, lo pendiente entra la próxima vez, y si se borra, se vuelve a armar
(sin las notas de la persona, que están sólo en el Excel y sus respaldos).

Tablas:
    novedades    un registro por mail del PJN leído (auditoría y cola)
    causas       los datos generales de cada causa, tal como los da el SCW
    actuaciones  una fila por actuación registrada, con su alerta
    corridas     el historial de barridos
"""

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS novedades (
    msg_id        TEXT PRIMARY KEY,
    recibido      TEXT,
    asunto        TEXT,
    expediente    TEXT,
    caratula      TEXT,
    fecha_novedad TEXT,
    link          TEXT,
    relevante     INTEGER NOT NULL DEFAULT 1,
    procesada_en  TEXT,
    corrida_id    INTEGER
);
CREATE TABLE IF NOT EXISTS causas (
    expediente    TEXT PRIMARY KEY,
    caratula      TEXT,
    jurisdiccion  TEXT,
    dependencia   TEXT,
    situacion     TEXT,
    link          TEXT,
    revisada_en   TEXT
);
CREATE TABLE IF NOT EXISTS actuaciones (
    clave         TEXT PRIMARY KEY,
    expediente    TEXT NOT NULL,
    fecha         TEXT,
    fecha_iso     TEXT,
    tipo          TEXT,
    detalle       TEXT,
    a_fs          TEXT,
    link_archivo  TEXT,
    pdf           TEXT,
    alerta        TEXT NOT NULL DEFAULT '',
    historica     INTEGER NOT NULL DEFAULT 0,
    alzada        INTEGER NOT NULL DEFAULT 0,
    corrida_id    INTEGER,
    registrada_en TEXT
);
CREATE INDEX IF NOT EXISTS act_expediente ON actuaciones(expediente);
CREATE INDEX IF NOT EXISTS act_alerta ON actuaciones(alerta, fecha_iso);
CREATE TABLE IF NOT EXISTS corridas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio        TEXT,
    fin           TEXT,
    origen        TEXT,
    estado        TEXT,
    mails         INTEGER DEFAULT 0,
    causas        INTEGER DEFAULT 0,
    nuevas        INTEGER DEFAULT 0,
    primeros_autos INTEGER DEFAULT 0,
    sentencias    INTEGER DEFAULT 0,
    errores       INTEGER DEFAULT 0,
    advertencias  INTEGER DEFAULT 0,
    duracion      TEXT,
    detalle       TEXT
);
"""
VERSION = 1  # PRAGMA user_version una vez creado ESQUEMA: subirla si cambia


def ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def abrir(ruta: Path):
    """Conexión con el esquema creado. WAL: la app lee mientras el scraper escribe."""
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ruta), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        _pasar_a_wal(con)
        if con.execute("PRAGMA user_version").fetchone()[0] < VERSION:
            # BEGIN IMMEDIATE toma la escritura antes de leer el esquema: dos
            # aperturas a la vez de una base nueva (el panel pide alertas e
            # historial juntos) esperan su turno en vez de dar "database is locked".
            con.executescript(f"BEGIN IMMEDIATE; {ESQUEMA} PRAGMA user_version = {VERSION}; COMMIT;")
        yield con
        con.commit()
    finally:
        con.close()


def _pasar_a_wal(con, intentos: int = 50):
    """En una base nueva, si otra conexión la está pasando a WAL a la vez,
    SQLite no espera el timeout: da "database is locked" en el acto."""
    for i in range(intentos):
        try:
            con.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or i == intentos - 1:
                raise
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# Mails
# ---------------------------------------------------------------------------

def mails_vistos(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT msg_id FROM novedades")}


def guardar_novedades(con, novedades: list[dict], filtros: list[str]) -> int:
    """Registra los mails nuevos. Retorna cuántos entraron.

    `filtros`: si hay, sólo es relevante (se scrapea) la novedad cuya carátula
    contiene alguna de esas palabras. Las demás quedan registradas igual.
    """
    from clasificador import norm
    palabras = [norm(f) for f in filtros]
    n = 0
    for nov in novedades:
        relevante = 1 if not palabras else int(any(p in norm(nov["caratula"]) for p in palabras))
        cur = con.execute(
            "INSERT OR IGNORE INTO novedades (msg_id, recibido, asunto, expediente, caratula,"
            " fecha_novedad, link, relevante) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (nov["msg_id"], nov["recibido"], nov["asunto"], nov["expediente"], nov["caratula"],
             nov["fecha_novedad"], nov["link"], relevante))
        n += cur.rowcount
    return n


def cola(con) -> dict:
    """Las causas a revisar: novedades relevantes sin procesar, agrupadas por causa.

    Retorna {"causas": [{expediente, caratula, link, msg_ids, desde}], "sin_link": [...]}.
    `desde` es la fecha (ISO) de la novedad más vieja pendiente de la causa: lo
    anterior a eso, si aparece en el primer scrapeo, es historia de la causa.
    """
    filas = con.execute(
        "SELECT * FROM novedades WHERE relevante = 1 AND procesada_en IS NULL"
        " ORDER BY recibido").fetchall()
    causas, sin_link = {}, []
    for f in filas:
        if not f["link"] or not f["expediente"]:
            sin_link.append(f["expediente"] or f["asunto"] or f["msg_id"])
            continue
        c = causas.setdefault(f["expediente"], {
            "expediente": f["expediente"], "caratula": f["caratula"], "link": f["link"],
            "msg_ids": [], "desde": ""})
        c["msg_ids"].append(f["msg_id"])
        c["link"] = f["link"]  # el más reciente
        fecha = f["fecha_novedad"] or (f["recibido"] or "")[:10]
        if fecha and (not c["desde"] or fecha < c["desde"]):
            c["desde"] = fecha
    return {"causas": list(causas.values()), "sin_link": sin_link}


def marcar_procesadas(con, msg_ids: list[str], corrida_id: int):
    con.executemany("UPDATE novedades SET procesada_en = ?, corrida_id = ? WHERE msg_id = ?",
                    [(ahora(), corrida_id, m) for m in msg_ids])


def ignorar_sin_link(con):
    """Los mails sin link no se pueden scrapear: se marcan para no avisar siempre."""
    con.execute("UPDATE novedades SET procesada_en = ? WHERE procesada_en IS NULL"
                " AND (link IS NULL OR link = '' OR expediente IS NULL OR expediente = '')",
                (ahora(),))


# ---------------------------------------------------------------------------
# Causas y actuaciones
# ---------------------------------------------------------------------------

def indice(con) -> tuple[set[str], dict[str, str]]:
    """(claves registradas, expediente → fecha ISO de su actuación más vieja)."""
    claves = {r[0] for r in con.execute("SELECT clave FROM actuaciones")}
    desde = {r[0]: r[1] for r in con.execute(
        "SELECT expediente, MIN(fecha_iso) FROM actuaciones WHERE fecha_iso <> ''"
        " GROUP BY expediente")}
    return claves, desde


def guardar_causa(con, expediente: str, datos: dict, link: str):
    con.execute(
        "INSERT INTO causas (expediente, caratula, jurisdiccion, dependencia, situacion, link,"
        " revisada_en) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(expediente) DO UPDATE SET"
        " caratula = excluded.caratula, jurisdiccion = excluded.jurisdiccion,"
        " dependencia = excluded.dependencia, situacion = excluded.situacion,"
        " link = excluded.link, revisada_en = excluded.revisada_en",
        (expediente, datos.get("caratula", ""), datos.get("jurisdiccion", ""),
         datos.get("dependencia", ""), datos.get("sit_actual", ""), link, ahora()))


def guardar_actuaciones(con, filas: list[dict], corrida_id: int):
    con.executemany(
        "INSERT OR IGNORE INTO actuaciones (clave, expediente, fecha, fecha_iso, tipo, detalle,"
        " a_fs, link_archivo, pdf, alerta, historica, alzada, corrida_id, registrada_en)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(f["clave"], f["expediente"], f["fecha"], f["fecha_iso"], f["tipo"], f["detalle"],
          f["a_fs"], f["link_archivo"], f.get("pdf") or "", f["alerta"], int(f["historica"]),
          int(f["alzada"]), corrida_id, ahora()) for f in filas])


def ruta_pdf(con, clave: str) -> str:
    r = con.execute("SELECT pdf FROM actuaciones WHERE clave = ?", (clave,)).fetchone()
    return r[0] if r else ""


# ---------------------------------------------------------------------------
# Corridas
# ---------------------------------------------------------------------------

def crear_corrida(con, origen: str) -> int:
    cur = con.execute("INSERT INTO corridas (inicio, origen, estado) VALUES (?, ?, 'CORRIENDO')",
                      (ahora(), origen))
    con.commit()
    return cur.lastrowid


def cerrar_corrida(con, corrida_id: int, estado: str, r: dict):
    con.execute(
        "UPDATE corridas SET fin = ?, estado = ?, mails = ?, causas = ?, nuevas = ?,"
        " primeros_autos = ?, sentencias = ?, errores = ?, advertencias = ?, duracion = ?,"
        " detalle = ? WHERE id = ?",
        (ahora(), estado, r.get("mails", 0), r.get("causas", 0), r.get("nuevas", 0),
         r.get("primeros_autos", 0), r.get("sentencias", 0), r.get("errores", 0),
         r.get("warnings", 0), r.get("duracion", ""), r.get("detalle", ""), corrida_id))


def cerrar_colgadas(con):
    """Una corrida que quedó CORRIENDO (se cerró la ventana a la mitad)."""
    con.execute("UPDATE corridas SET estado = 'INTERRUMPIDA', fin = COALESCE(fin, ?)"
                " WHERE estado = 'CORRIENDO'", (ahora(),))


def historial(con, n: int = 10) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM corridas ORDER BY id DESC LIMIT ?", (n,))]


def corrida(con, corrida_id: int) -> dict | None:
    r = con.execute("SELECT * FROM corridas WHERE id = ?", (corrida_id,)).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------

_SELECT_ALERTAS = (
    "SELECT a.clave, a.expediente, a.fecha, a.fecha_iso, a.tipo, a.detalle, a.alerta,"
    " a.link_archivo, a.pdf, a.historica, a.corrida_id, a.registrada_en,"
    " c.caratula, c.dependencia, c.link AS link_causa"
    " FROM actuaciones a LEFT JOIN causas c ON c.expediente = a.expediente")


def _agrupar(filas) -> dict:
    out = {"primer_auto": [], "sentencia": []}
    for f in filas:
        out.setdefault(f["alerta"], []).append(dict(f))
    return out


def ultima_corrida_con_resultado(con) -> dict | None:
    r = con.execute("SELECT * FROM corridas WHERE estado <> 'CORRIENDO' ORDER BY id DESC"
                    " LIMIT 1").fetchone()
    return dict(r) if r else None


def alertas_de_corrida(con, corrida_id: int) -> dict:
    """Lo que apareció en un barrido (sin la historia del primer scrapeo)."""
    filas = con.execute(
        _SELECT_ALERTAS + " WHERE a.corrida_id = ? AND a.alerta <> '' AND a.historica = 0"
        " ORDER BY a.fecha_iso DESC, a.expediente", (corrida_id,)).fetchall()
    return _agrupar(filas)


def alertas_del_mes(con, mes: str) -> dict:
    """Las del mes `mes` (aaaa-mm) por fecha de la actuación, se hayan visto cuando se hayan visto."""
    filas = con.execute(
        _SELECT_ALERTAS + " WHERE a.alerta <> '' AND substr(a.fecha_iso, 1, 7) = ?"
        " ORDER BY a.fecha_iso DESC, a.expediente", (mes,)).fetchall()
    return _agrupar(filas)


def meses_con_alertas(con) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT substr(fecha_iso, 1, 7) m FROM actuaciones"
        " WHERE alerta <> '' AND fecha_iso <> '' ORDER BY m DESC")]


def resumen_por_mes(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT substr(fecha_iso, 1, 7) AS mes,"
        " SUM(alerta = 'primer_auto') AS primeros_autos,"
        " SUM(alerta = 'sentencia') AS sentencias,"
        " COUNT(*) AS actuaciones"
        " FROM actuaciones WHERE fecha_iso <> '' GROUP BY mes ORDER BY mes DESC")]


def contadores(con) -> dict:
    return {
        "causas": con.execute("SELECT COUNT(*) FROM causas").fetchone()[0],
        "actuaciones": con.execute("SELECT COUNT(*) FROM actuaciones").fetchone()[0],
        "pendientes": len(cola(con)["causas"]),
    }
