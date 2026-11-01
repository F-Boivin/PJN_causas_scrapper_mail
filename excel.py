"""El Excel de salida: la app agrega lo nuevo y no pisa lo que escribe la persona.

Hojas de la persona (Alertas, Actuaciones, Causas): la app agrega al final las
filas nuevas y mantiene al día sus propias columnas, que ubica por el título.
Cada fila lleva su ID en una columna oculta, así se puede ordenar, filtrar e
insertar columnas. Lo demás (sus columnas, colores, filtros, tablas, hojas
nuevas, filas agregadas a mano) no se toca. Una fila o columna de la app que
falte vuelve a aparecer.

Hojas de la app (Por mes, Mails del PJN, Corridas): se reescriben enteras.

Antes de reemplazar el archivo se guarda una copia en `respaldos/` (las últimas
10). openpyxl no conserva gráficos ni imágenes de un archivo que abre, ni los
comentarios de Excel 365: esos se pasan a notas, que sí conserva.
"""

import os
import shutil
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from posixpath import dirname, join, normpath

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.table import TableColumn

from clasificador import NOMBRES

AZUL = PatternFill("solid", fgColor="1F3A68")
COLOR_ALERTA = {"primer_auto": PatternFill("solid", fgColor="E3EEFF"),
                "sentencia": PatternFill("solid", fgColor="E6F4EA")}
FONT_LINK = Font(color="1155CC", underline="single")

ID = "ID"
OBSERVACIONES = "Observaciones"
RESPALDOS = 10


class ErrorExcel(Exception):
    pass


class ExcelAbierto(ErrorExcel):
    pass


class ExcelIlegible(ErrorExcel):
    pass


def _fecha(iso: str):
    if not iso:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(iso, fmt)
        except ValueError:
            continue
    return iso


def _link(texto: str, url: str):
    return {"texto": texto, "url": url} if url else ""


def _pdf(ruta: str, base: Path):
    """Link relativo al PDF local: anda abriendo el Excel desde su carpeta."""
    if not ruta:
        return ""
    try:
        rel = os.path.relpath(ruta, base)
    except ValueError:  # otra unidad de disco
        rel = ruta
    return {"texto": "abrir PDF", "url": rel.replace("\\", "/")}


# ---------------------------------------------------------------------------
# Escritura de celdas y encabezados
# ---------------------------------------------------------------------------

def _texto(v):
    return v["texto"] if isinstance(v, dict) else v


def _igual(actual, nuevo) -> bool:
    nuevo = _texto(nuevo)
    if actual in (None, "") and nuevo in (None, ""):
        return True
    return actual == nuevo or (actual is not None and nuevo is not None
                               and not isinstance(nuevo, datetime) and str(actual) == str(nuevo))


def _escribir(celda, v, nueva: bool):
    """Pone un valor de la app. En una celda que ya existía respeta el formato."""
    celda.value = _texto(v)
    if isinstance(v, dict):
        celda.hyperlink = v["url"]
        if nueva or celda.font != FONT_LINK:
            celda.font = FONT_LINK
    elif celda.hyperlink is not None:
        celda.hyperlink = None
    if isinstance(v, datetime):
        celda.number_format = "dd/mm/yyyy hh:mm" if (v.hour or v.minute) else "dd/mm/yyyy"


def _encabezado(ws, col: int, titulo: str, ancho: int):
    celda = ws.cell(row=1, column=col, value=titulo)
    celda.font = Font(bold=True, color="FFFFFF")
    celda.fill = AZUL
    celda.alignment = Alignment(vertical="center")
    ws.column_dimensions[get_column_letter(col)].width = ancho


def _crear(wb, titulo: str, posicion: int):
    return wb.create_sheet(titulo, min(posicion, len(wb.sheetnames)))


def _titulo_libre(wb, base: str) -> str:
    for n in range(1, 100):
        t = f"{base} (anterior{'' if n == 1 else f' {n}'})"[:31]
        if t not in wb.sheetnames:
            return t
    raise ErrorExcel(f"Demasiadas copias de la hoja {base}")


# ---------------------------------------------------------------------------
# Hojas de la persona: se agregan filas, nunca se borra nada
# ---------------------------------------------------------------------------

def _sincronizar(wb, titulo, posicion, columnas, filas, id_de, relleno=None) -> bool:
    """Agrega las filas que faltan y actualiza las columnas de la app.

    `columnas`: [(título, ancho, valor(fila))]. Retorna si cambió algo.
    """
    cambio = False
    ws = wb[titulo] if titulo in wb.sheetnames else None
    cols = {}
    if ws is not None:
        for celda in ws[1]:
            if celda.value is not None and str(celda.value).strip() not in cols:
                cols[str(celda.value).strip()] = celda.column
        if ID not in cols:
            # Sin la columna ID no hay cómo reconocer las filas: la hoja queda
            # como estaba, con otro nombre, y se arma una nueva.
            ws.title = _titulo_libre(wb, titulo)
            ws, cols = None, {}
    if ws is None:
        ws = _crear(wb, titulo, posicion)
        for i, (t, ancho) in enumerate([(t, a) for t, a, _ in columnas]
                                       + [(OBSERVACIONES, 40), (ID, 10)], 1):
            _encabezado(ws, i, t, ancho)
            cols[t] = i
        ws.column_dimensions[get_column_letter(cols[ID])].hidden = True
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"
        cambio = True

    ultima_col = max([c.column for c in ws[1] if c.value is not None] or [0])
    for t, ancho, _ in columnas + [(ID, 10, None)]:
        if t not in cols:
            ultima_col += 1
            _encabezado(ws, ultima_col, t, ancho)
            cols[t] = ultima_col
            if t == ID:
                ws.column_dimensions[get_column_letter(ultima_col)].hidden = True
            cambio = True

    filas_de, ultima = {}, 1
    for r, valores in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if any(v not in (None, "") for v in valores):
            ultima = r
        if len(valores) >= cols[ID] and valores[cols[ID] - 1] not in (None, ""):
            filas_de.setdefault(str(valores[cols[ID] - 1]), []).append(r)

    for fila in filas:
        ident = id_de(fila)
        valores = [(cols[t], valor(fila)) for t, _, valor in columnas]
        existentes = filas_de.get(ident)
        if existentes:
            for r in existentes:
                for c, v in valores:
                    celda = ws.cell(row=r, column=c)
                    if not _igual(celda.value, v):
                        _escribir(celda, v, nueva=False)
                        cambio = True
            continue
        ultima += 1
        for c, v in valores + [(cols[ID], ident)]:
            _escribir(ws.cell(row=ultima, column=c), v, nueva=True)
        if relleno:
            color = relleno(fila)
            for c, _ in valores:
                ws.cell(row=ultima, column=c).fill = color
        cambio = True

    if cambio:
        _extender_rangos(ws, ultima, max(cols.values()))
    return cambio


def _extender_rangos(ws, ultima: int, ultima_col: int):
    """Que el filtro y las tablas de Excel abarquen las filas y columnas nuevas."""
    tablas = [t for t in ws.tables.values() if range_boundaries(t.ref)[1] == 1]
    for t in tablas:
        min_col, _, max_col, max_row = range_boundaries(t.ref)
        for c in range(max_col + 1, ultima_col + 1):
            nombre = ws.cell(row=1, column=c).value
            if nombre is None:
                break
            t.tableColumns.append(TableColumn(id=len(t.tableColumns) + 1, name=str(nombre)))
            max_col = c
        t.ref = f"{get_column_letter(min_col)}1:{get_column_letter(max_col)}{max(max_row, ultima, 2)}"
        if t.autoFilter is not None:
            t.autoFilter.ref = t.ref
    if not tablas and ws.auto_filter.ref:
        min_col, _, max_col, _ = range_boundaries(ws.auto_filter.ref)
        ws.auto_filter.ref = (f"{get_column_letter(min_col)}1:"
                              f"{get_column_letter(max(max_col, ultima_col))}{ultima}")


# ---------------------------------------------------------------------------
# Hojas de la app: se reescriben enteras
# ---------------------------------------------------------------------------

def _reescribir(wb, titulo, posicion, columnas, filas) -> bool:
    """Rehace la hoja si su contenido cambió. Retorna si cambió algo."""
    tabla = [[t for t, _ in columnas]] + [[_texto(v) for v in f] for f in filas]
    if titulo in wb.sheetnames:
        ws = wb[titulo]
        actual = [list(r) for r in ws.iter_rows(values_only=True)]
        if len(actual) == len(tabla) and all(
                len(a) >= len(n) and all(_igual(x, y) for x, y in zip(a, n))
                for a, n in zip(actual, tabla)):
            return False
        posicion = wb.sheetnames.index(titulo)
        wb.remove(ws)
    ws = _crear(wb, titulo, posicion)
    for i, (t, ancho) in enumerate(columnas, 1):
        _encabezado(ws, i, t, ancho)
    for r, fila in enumerate(filas, 2):
        for c, v in enumerate(fila, 1):
            _escribir(ws.cell(row=r, column=c), v, nueva=True)
    ws.freeze_panes = "A2"
    if filas:
        ws.auto_filter.ref = ws.dimensions
    return True


# ---------------------------------------------------------------------------
# El archivo
# ---------------------------------------------------------------------------

def _abrir(ruta: Path):
    if not ruta.exists():
        wb = Workbook()
        wb.remove(wb.active)
        return wb
    try:
        return load_workbook(ruta, rich_text=True)
    except PermissionError as e:
        raise ExcelAbierto(f"No se pudo leer «{ruta.name}»: está abierto en otro programa. "
                           "Cerralo: lo nuevo se agrega en el próximo barrido.") from e
    except (zipfile.BadZipFile, InvalidFileException, KeyError, ValueError, OSError) as e:
        raise ExcelIlegible(
            f"No se pudo abrir «{ruta.name}» para actualizarlo, así que quedó como estaba. "
            "Si se dañó o lo reemplazaste por otro archivo, recuperá una copia de la carpeta "
            "«respaldos», o movelo a otro lado para que la app arme uno nuevo.") from e


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "tc": "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"}
_RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _parte(carpeta: str, destino: str) -> str:
    return destino.lstrip("/") if destino.startswith("/") else normpath(join(carpeta, destino))


def _comentarios_modernos(ruta: Path) -> dict:
    """{hoja: {celda: [(autor, texto)]}} con los comentarios de Excel 365 del archivo."""
    with zipfile.ZipFile(ruta) as z:
        partes = set(z.namelist())
        if not any(p.startswith("xl/threadedComments/") for p in partes):
            return {}

        def leer(p):
            return ET.fromstring(z.read(p))

        personas = {}
        for p in partes:
            if p.startswith("xl/persons/") and p.endswith(".xml"):
                personas.update({x.get("id"): x.get("displayName") or "" for x in leer(p)})
        destinos = {r.get("Id"): r.get("Target") for r in leer("xl/_rels/workbook.xml.rels")}
        salida = {}
        for hoja in leer("xl/workbook.xml").find("m:sheets", _NS):
            parte = _parte("xl", destinos.get(hoja.get(_RID), ""))
            rels = f"{dirname(parte)}/_rels/{parte.rsplit('/', 1)[-1]}.rels"
            if rels not in partes:
                continue
            for rel in leer(rels):
                if not rel.get("Type", "").endswith("/threadedComment"):
                    continue
                for c in leer(_parte(dirname(parte), rel.get("Target"))):
                    texto = c.find("tc:text", _NS)
                    salida.setdefault(hoja.get("name"), {}).setdefault(c.get("ref"), []).append(
                        (personas.get(c.get("personId"), ""),
                         "".join(texto.itertext()) if texto is not None else ""))
    return salida


def _pasar_comentarios_a_notas(wb, ruta: Path) -> bool:
    """openpyxl no guarda los comentarios de Excel 365: su texto pasa a una nota."""
    try:
        hilos = _comentarios_modernos(ruta) if ruta.exists() else {}
    except (KeyError, ET.ParseError, zipfile.BadZipFile):
        return False
    for hoja, celdas in hilos.items():
        if hoja not in wb.sheetnames:
            continue
        for ref, mensajes in celdas.items():
            texto = "\n".join(f"{autor}: {t}" if autor else t for autor, t in mensajes)
            wb[hoja][ref].comment = Comment(texto, mensajes[0][0] or "Excel", width=300, height=120)
    return bool(hilos)


def _respaldar(ruta: Path) -> Path | None:
    """Copia el Excel actual a respaldos/ y deja sólo las últimas RESPALDOS copias."""
    if not ruta.exists():
        return None
    carpeta = ruta.parent / "respaldos"
    carpeta.mkdir(exist_ok=True)
    destino = carpeta / f"{ruta.stem} {datetime.now():%Y-%m-%d %H%M%S}.xlsx"
    shutil.copy2(ruta, destino)
    for viejo in sorted(carpeta.glob(f"{ruta.stem} ????-??-?? ??????.xlsx"))[:-RESPALDOS]:
        viejo.unlink(missing_ok=True)
    return destino


def exportar(con, ruta: Path) -> Path:
    """Pone al día el Excel con la base, sin pisar lo que escribió la persona.

    Lanza ExcelAbierto si Windows no deja reemplazarlo (está abierto) y
    ExcelIlegible si el archivo existe pero no se puede leer: en los dos casos
    el archivo queda como estaba.
    """
    ruta = Path(ruta)
    base = ruta.parent
    wb = _abrir(ruta)
    cambio = _pasar_comentarios_a_notas(wb, ruta)

    acts = con.execute(
        "SELECT a.*, c.caratula, c.dependencia FROM actuaciones a"
        " LEFT JOIN causas c ON c.expediente = a.expediente"
        " ORDER BY a.fecha_iso, a.expediente, a.registrada_en").fetchall()

    cambio |= _sincronizar(wb, "Alertas", 0, [
        ("Fecha", 12, lambda a: _fecha(a["fecha_iso"]) or a["fecha"]),
        ("Alerta", 13, lambda a: NOMBRES.get(a["alerta"], a["alerta"])),
        ("Expediente", 18, lambda a: a["expediente"]),
        ("Carátula", 55, lambda a: a["caratula"] or ""),
        ("Detalle", 50, lambda a: a["detalle"]),
        ("Dependencia", 40, lambda a: a["dependencia"] or ""),
        ("Documento", 14, lambda a: _link("ver en el PJN", a["link_archivo"])),
        ("PDF", 11, lambda a: _pdf(a["pdf"], base)),
        ("Detectada", 17, lambda a: _fecha(a["registrada_en"]))],
        [a for a in acts if a["alerta"]], lambda a: a["clave"],
        relleno=lambda a: COLOR_ALERTA.get(a["alerta"], PatternFill()))

    meses = con.execute(
        "SELECT substr(fecha_iso, 1, 7) AS mes, SUM(alerta = 'primer_auto') AS pa,"
        " SUM(alerta = 'sentencia') AS se, COUNT(*) AS n FROM actuaciones"
        " WHERE fecha_iso <> '' GROUP BY mes ORDER BY mes DESC").fetchall()
    cambio |= _reescribir(wb, "Por mes", 1, [("Mes", 10), ("Primeros autos", 16),
                                             ("Sentencias", 12), ("Actuaciones", 13)],
                          [[m["mes"], m["pa"], m["se"], m["n"]] for m in meses])

    cambio |= _sincronizar(wb, "Actuaciones", 2, [
        ("Fecha", 12, lambda a: _fecha(a["fecha_iso"]) or a["fecha"]),
        ("Expediente", 18, lambda a: a["expediente"]),
        ("Carátula", 50, lambda a: a["caratula"] or ""),
        ("Tipo", 22, lambda a: a["tipo"]),
        ("Detalle", 55, lambda a: a["detalle"]),
        ("Fojas", 8, lambda a: a["a_fs"]),
        ("Alerta", 13, lambda a: NOMBRES.get(a["alerta"], "")),
        ("Instancia", 10, lambda a: "Alzada" if a["alzada"] else ""),
        ("Documento", 14, lambda a: _link("ver en el PJN", a["link_archivo"])),
        ("PDF", 11, lambda a: _pdf(a["pdf"], base)),
        ("Registrada", 17, lambda a: _fecha(a["registrada_en"]))],
        acts, lambda a: a["clave"])

    causas = con.execute(
        "SELECT c.*, COUNT(a.clave) AS n, MAX(a.fecha_iso) AS ultima,"
        " MIN(CASE WHEN a.alerta = 'primer_auto' THEN a.fecha_iso END) AS primer_auto,"
        " MIN(CASE WHEN a.alerta = 'sentencia' THEN a.fecha_iso END) AS sentencia"
        " FROM causas c LEFT JOIN actuaciones a ON a.expediente = c.expediente"
        " GROUP BY c.expediente ORDER BY ultima, c.expediente").fetchall()
    cambio |= _sincronizar(wb, "Causas", 3, [
        ("Expediente", 18, lambda c: c["expediente"]),
        ("Carátula", 55, lambda c: c["caratula"]),
        ("Dependencia", 45, lambda c: c["dependencia"]),
        ("Situación", 16, lambda c: c["situacion"]),
        ("Última actuación", 16, lambda c: _fecha(c["ultima"])),
        ("Actuaciones", 12, lambda c: c["n"]),
        ("Primer auto", 13, lambda c: _fecha(c["primer_auto"])),
        ("Sentencia", 12, lambda c: _fecha(c["sentencia"])),
        ("Causa", 12, lambda c: _link("abrir", c["link"])),
        ("Revisada", 17, lambda c: _fecha(c["revisada_en"]))],
        causas, lambda c: c["expediente"])

    mails = con.execute("SELECT * FROM novedades ORDER BY recibido DESC").fetchall()
    cambio |= _reescribir(wb, "Mails del PJN", 4, [
        ("Recibido", 17), ("Expediente", 18), ("Carátula", 55), ("Fecha novedad", 14),
        ("Se revisa", 10), ("Procesado", 17)],
        [[_fecha(m["recibido"]), m["expediente"], m["caratula"], _fecha(m["fecha_novedad"]),
          "sí" if m["relevante"] else "no (filtro)", _fecha(m["procesada_en"])] for m in mails])

    corridas = con.execute("SELECT * FROM corridas ORDER BY id DESC").fetchall()
    cambio |= _reescribir(wb, "Corridas", 5, [
        ("Inicio", 17), ("Estado", 13), ("Mails nuevos", 12), ("Causas", 9), ("Actuaciones", 12),
        ("Primeros autos", 15), ("Sentencias", 11), ("Errores", 9), ("Duración", 10)],
        [[_fecha(c["inicio"]), c["estado"], c["mails"], c["causas"], c["nuevas"],
          c["primeros_autos"], c["sentencias"], c["errores"], c["duracion"]] for c in corridas])

    if not cambio and ruta.exists():
        return ruta

    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.stem + ".tmp.xlsx")
    wb.save(tmp)
    respaldo = _respaldar(ruta)
    try:
        os.replace(tmp, ruta)
    except PermissionError as e:
        tmp.unlink(missing_ok=True)
        if respaldo:
            respaldo.unlink(missing_ok=True)
        raise ExcelAbierto(f"No se pudo actualizar «{ruta.name}» porque está abierto en Excel. "
                           "Cerralo: lo nuevo se agrega en el próximo barrido, o al tocar "
                           "«📊 Abrir Excel».") from e
    return ruta
