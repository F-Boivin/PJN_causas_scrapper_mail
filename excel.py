"""El Excel de salida: se regenera entero desde la base al final de cada corrida.

Hojas: Alertas (primeros autos y sentencias), Por mes, Actuaciones, Causas,
Mails del PJN y Corridas. Las fechas son fechas reales (se ordenan y filtran
por mes) y los links se pueden clickear.

No conviene escribir a mano en este Excel: la próxima corrida lo reemplaza.
Para anotar, copiá la hoja a otro archivo. Se abre igual en Google Sheets
(Archivo → Importar, o subiéndolo a Drive).
"""

import os
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from clasificador import NOMBRES

AZUL = PatternFill("solid", fgColor="1F3A68")
COLOR_ALERTA = {"primer_auto": PatternFill("solid", fgColor="E3EEFF"),
                "sentencia": PatternFill("solid", fgColor="E6F4EA")}
FONT_LINK = Font(color="1155CC", underline="single")


class ExcelAbierto(Exception):
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


def _hoja(wb, titulo: str, columnas: list[tuple[str, int]], filas: list[list], primera=False):
    ws = wb.active if primera else wb.create_sheet()
    ws.title = titulo
    ws.append([c for c, _ in columnas])
    for celda in ws[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = AZUL
        celda.alignment = Alignment(vertical="center")
    for i, (_, ancho) in enumerate(columnas, 1):
        ws.column_dimensions[get_column_letter(i)].width = ancho
    for fila in filas:
        ws.append([v["texto"] if isinstance(v, dict) else v for v in fila])
        r = ws.max_row
        for i, v in enumerate(fila, 1):
            celda = ws.cell(row=r, column=i)
            if isinstance(v, dict) and v.get("url"):
                celda.hyperlink = v["url"]
                celda.font = FONT_LINK
            elif isinstance(v, datetime):
                celda.number_format = "dd/mm/yyyy hh:mm" if (v.hour or v.minute) else "dd/mm/yyyy"
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions
    return ws


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


def exportar(con, ruta: Path) -> Path:
    """Escribe el Excel. Lanza ExcelAbierto si Windows no deja reemplazarlo."""
    ruta = Path(ruta)
    base = ruta.parent
    wb = Workbook()

    acts = con.execute(
        "SELECT a.*, c.caratula, c.dependencia FROM actuaciones a"
        " LEFT JOIN causas c ON c.expediente = a.expediente"
        " ORDER BY a.fecha_iso DESC, a.expediente, a.registrada_en DESC").fetchall()

    alertas = [a for a in acts if a["alerta"]]
    ws = _hoja(wb, "Alertas", [
        ("Fecha", 12), ("Alerta", 13), ("Expediente", 18), ("Carátula", 55), ("Detalle", 50),
        ("Dependencia", 40), ("Documento", 14), ("PDF", 11), ("Detectada", 17)],
        [[_fecha(a["fecha_iso"]) or a["fecha"], NOMBRES.get(a["alerta"], a["alerta"]),
          a["expediente"], a["caratula"] or "", a["detalle"], a["dependencia"] or "",
          _link("ver en el PJN", a["link_archivo"]), _pdf(a["pdf"], base),
          _fecha(a["registrada_en"])] for a in alertas], primera=True)
    for r, a in enumerate(alertas, 2):
        for celda in ws[r][:6]:
            celda.fill = COLOR_ALERTA.get(a["alerta"], PatternFill())

    meses = con.execute(
        "SELECT substr(fecha_iso, 1, 7) AS mes, SUM(alerta = 'primer_auto') AS pa,"
        " SUM(alerta = 'sentencia') AS se, COUNT(*) AS n FROM actuaciones"
        " WHERE fecha_iso <> '' GROUP BY mes ORDER BY mes DESC").fetchall()
    _hoja(wb, "Por mes", [("Mes", 10), ("Primeros autos", 16), ("Sentencias", 12),
                          ("Actuaciones", 13)],
          [[m["mes"], m["pa"], m["se"], m["n"]] for m in meses])

    _hoja(wb, "Actuaciones", [
        ("Fecha", 12), ("Expediente", 18), ("Carátula", 50), ("Tipo", 22), ("Detalle", 55),
        ("Fojas", 8), ("Alerta", 13), ("Instancia", 10), ("Documento", 14), ("PDF", 11),
        ("Registrada", 17)],
        [[_fecha(a["fecha_iso"]) or a["fecha"], a["expediente"], a["caratula"] or "", a["tipo"],
          a["detalle"], a["a_fs"], NOMBRES.get(a["alerta"], ""),
          "Alzada" if a["alzada"] else "", _link("ver en el PJN", a["link_archivo"]),
          _pdf(a["pdf"], base), _fecha(a["registrada_en"])] for a in acts])

    causas = con.execute(
        "SELECT c.*, COUNT(a.clave) AS n, MAX(a.fecha_iso) AS ultima,"
        " MIN(CASE WHEN a.alerta = 'primer_auto' THEN a.fecha_iso END) AS primer_auto,"
        " MIN(CASE WHEN a.alerta = 'sentencia' THEN a.fecha_iso END) AS sentencia"
        " FROM causas c LEFT JOIN actuaciones a ON a.expediente = c.expediente"
        " GROUP BY c.expediente ORDER BY ultima DESC").fetchall()
    _hoja(wb, "Causas", [
        ("Expediente", 18), ("Carátula", 55), ("Dependencia", 45), ("Situación", 16),
        ("Última actuación", 16), ("Actuaciones", 12), ("Primer auto", 13), ("Sentencia", 12),
        ("Causa", 12), ("Revisada", 17)],
        [[c["expediente"], c["caratula"], c["dependencia"], c["situacion"], _fecha(c["ultima"]),
          c["n"], _fecha(c["primer_auto"]), _fecha(c["sentencia"]), _link("abrir", c["link"]),
          _fecha(c["revisada_en"])] for c in causas])

    mails = con.execute("SELECT * FROM novedades ORDER BY recibido DESC").fetchall()
    _hoja(wb, "Mails del PJN", [
        ("Recibido", 17), ("Expediente", 18), ("Carátula", 55), ("Fecha novedad", 14),
        ("Se revisa", 10), ("Procesado", 17)],
        [[_fecha(m["recibido"]), m["expediente"], m["caratula"], _fecha(m["fecha_novedad"]),
          "sí" if m["relevante"] else "no (filtro)", _fecha(m["procesada_en"])] for m in mails])

    corridas = con.execute("SELECT * FROM corridas ORDER BY id DESC").fetchall()
    _hoja(wb, "Corridas", [
        ("Inicio", 17), ("Estado", 13), ("Mails nuevos", 12), ("Causas", 9), ("Actuaciones", 12),
        ("Primeros autos", 15), ("Sentencias", 11), ("Errores", 9), ("Duración", 10)],
        [[_fecha(c["inicio"]), c["estado"], c["mails"], c["causas"], c["nuevas"],
          c["primeros_autos"], c["sentencias"], c["errores"], c["duracion"]] for c in corridas])

    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.stem + ".tmp.xlsx")
    wb.save(tmp)
    try:
        os.replace(tmp, ruta)
    except PermissionError as e:
        tmp.unlink(missing_ok=True)
        raise ExcelAbierto(f"No se pudo actualizar {ruta.name}: está abierto en Excel. "
                           "Cerralo y apretá «Actualizar Excel».") from e
    return ruta
