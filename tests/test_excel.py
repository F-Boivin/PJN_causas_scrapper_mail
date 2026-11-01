"""El Excel no pisa lo que escribe la persona.

Se simula lo que haría alguien en Excel (anotar, pintar, ordenar, insertar
columnas, borrar filas, dar formato de tabla) y se vuelve a exportar.
Datos inventados.
"""

import contextlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook  # noqa: E402
from openpyxl.cell.rich_text import CellRichText, TextBlock  # noqa: E402
from openpyxl.cell.text import InlineFont  # noqa: E402
from openpyxl.styles import PatternFill  # noqa: E402
from openpyxl.worksheet.table import Table  # noqa: E402

import almacen  # noqa: E402
import excel  # noqa: E402

CAUSA = "CAF 1/2026"


def act(clave, fecha_iso, detalle, alerta=""):
    a, m, d = fecha_iso.split("-")
    return {"clave": clave, "expediente": CAUSA, "fecha": f"{int(d)}/{int(m)}/{a}",
            "fecha_iso": fecha_iso, "tipo": "FIRMA DESPACHO", "detalle": detalle, "a_fs": "1",
            "link_archivo": f"https://scw.pjn.gov.ar/scw/viewer.seam?id={clave}", "pdf": "",
            "alerta": alerta, "historica": 0, "alzada": 0}


def columnas(ws) -> dict:
    return {c.value: c.column for c in ws[1] if c.value is not None}


def fila_de(ws, ident) -> int | None:
    col = columnas(ws)["ID"]
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=col).value == ident:
            return r
    return None


def celda(ws, ident, titulo):
    return ws.cell(row=fila_de(ws, ident), column=columnas(ws)[titulo])


TC = "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"


def comentario_moderno(ruta, parte_hoja, ref, texto, autor):
    """Agrega un comentario de Excel 365 como lo guarda Excel (openpyxl no sabe escribirlos)."""
    rels = parte_hoja.replace("worksheets/", "worksheets/_rels/") + ".rels"
    with zipfile.ZipFile(ruta) as z:
        partes = {n: z.read(n) for n in z.namelist()}
    rel = ('<Relationship Id="rIdTC" Type="http://schemas.microsoft.com/office/2017/10/'
           'relationships/threadedComment" Target="../threadedComments/threadedComment1.xml"/>')
    partes[rels] = partes.get(rels, b'<Relationships xmlns="http://schemas.openxmlformats.org/'
                                     b'package/2006/relationships"></Relationships>'
                              ).replace(b"</Relationships>", rel.encode() + b"</Relationships>")
    partes["xl/threadedComments/threadedComment1.xml"] = (
        f'<ThreadedComments xmlns="{TC}"><threadedComment ref="{ref}" personId="{{P1}}" '
        f'id="{{C1}}"><text>{texto}</text></threadedComment></ThreadedComments>').encode()
    partes["xl/persons/person.xml"] = (
        f'<personList xmlns="{TC}"><person displayName="{autor}" id="{{P1}}"/></personList>').encode()
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, contenido in partes.items():
            z.writestr(nombre, contenido)


def ids(ws) -> list:
    col = columnas(ws)["ID"]
    return [ws.cell(row=r, column=col).value for r in range(2, ws.max_row + 1)
            if ws.cell(row=r, column=col).value]


class TestExcel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = Path(self.tmp.name) / "Novedades PJN.xlsx"
        pila = contextlib.ExitStack()
        self.addCleanup(pila.close)
        self.con = pila.enter_context(almacen.abrir(Path(self.tmp.name) / "t.db"))
        self.causa("EN DESPACHO")
        self.agregar(act("a1", "2026-10-01", "PRIMER AUTO ARCA", "primer_auto"),
                     act("a2", "2026-10-15", "INTIMACION"))

    def causa(self, situacion, caratula="FISCO NACIONAL c/ EMPRESA DE PRUEBA s/EJECUCION FISCAL"):
        almacen.guardar_causa(self.con, CAUSA, {"caratula": caratula, "dependencia": "JUZGADO 1",
                                                "sit_actual": situacion},
                              "https://scw.pjn.gov.ar/scw/x?eid=1")

    def agregar(self, *acts):
        almacen.guardar_actuaciones(self.con, list(acts), 1)

    def exportar(self):
        return load_workbook(excel.exportar(self.con, self.ruta), rich_text=True)

    def editar(self):
        """Abre el Excel como lo haría la persona; guardar con self.guardar(wb)."""
        return load_workbook(self.ruta, rich_text=True)

    def guardar(self, wb):
        wb.save(self.ruta)

    def test_hojas_y_orden(self):
        wb = self.exportar()
        self.assertEqual(wb.sheetnames, ["Alertas", "Por mes", "Actuaciones", "Causas",
                                         "Mails del PJN", "Corridas"])
        ws = wb["Actuaciones"]
        self.assertEqual(ids(ws), ["a1", "a2"])  # lo más viejo arriba, lo nuevo se agrega abajo
        self.assertIn("Observaciones", columnas(ws))
        self.assertTrue(ws.column_dimensions[ws.cell(row=1, column=columnas(ws)["ID"])
                                             .column_letter].hidden)
        self.assertEqual(ids(wb["Alertas"]), ["a1"])

    def test_notas_colores_columnas_y_hojas_propias_quedan(self):
        self.exportar()
        wb = self.editar()
        ws = wb["Actuaciones"]
        celda(ws, "a1", "Observaciones").value = CellRichText(
            ["pedir liquidación ", TextBlock(InlineFont(b=True), "hoy")])
        celda(ws, "a2", "Detalle").fill = PatternFill("solid", fgColor="FFFF00")
        nueva = ws.max_column + 1
        ws.cell(row=1, column=nueva, value="Responsable")
        ws.cell(row=fila_de(ws, "a1"), column=nueva, value="Dra. Prueba")
        wb["Alertas"].cell(row=2, column=columnas(wb["Alertas"])["Observaciones"], value="ok")
        wb.create_sheet("Mis notas")["A1"] = "pendientes"
        self.guardar(wb)

        self.agregar(act("a3", "2026-10-20", "SENTENCIA EXPEDITA", "sentencia"))
        wb = self.exportar()
        ws = wb["Actuaciones"]
        self.assertEqual(ids(ws), ["a1", "a2", "a3"])
        nota = celda(ws, "a1", "Observaciones").value
        self.assertIsInstance(nota, CellRichText)
        self.assertEqual(str(nota), "pedir liquidación hoy")
        self.assertTrue(celda(ws, "a2", "Detalle").fill.fgColor.rgb.endswith("FFFF00"))
        self.assertEqual(celda(ws, "a1", "Responsable").value, "Dra. Prueba")
        self.assertEqual(wb["Mis notas"]["A1"].value, "pendientes")
        self.assertEqual(ids(wb["Alertas"]), ["a1", "a3"])
        self.assertEqual(celda(wb["Alertas"], "a1", "Observaciones").value, "ok")
        self.assertEqual(celda(ws, "a3", "Documento").hyperlink.target,
                         "https://scw.pjn.gov.ar/scw/viewer.seam?id=a3")

    def test_ordenar_e_insertar_columnas(self):
        self.exportar()
        wb = self.editar()
        ws = wb["Actuaciones"]
        celda(ws, "a1", "Observaciones").value = "nota de a1"
        # ordenar de más nuevo a más viejo: se intercambian las filas enteras
        f2 = [c.value for c in ws[2]]
        f3 = [c.value for c in ws[3]]
        for c, (v2, v3) in enumerate(zip(f2, f3), 1):
            ws.cell(row=2, column=c, value=v3)
            ws.cell(row=3, column=c, value=v2)
        ws.insert_cols(2)
        ws.cell(row=1, column=2, value="Mi columna")
        ws.cell(row=2, column=2, value="x2")
        ws.cell(row=3, column=2, value="x3")
        self.guardar(wb)

        self.causa("EN DESPACHO", caratula="FISCO NACIONAL c/ OTRA EMPRESA s/EJECUCION FISCAL")
        wb = self.exportar()
        ws = wb["Actuaciones"]
        self.assertEqual(ids(ws), ["a2", "a1"])  # respeta el orden de la persona, sin duplicar
        self.assertEqual(celda(ws, "a1", "Observaciones").value, "nota de a1")
        self.assertEqual(celda(ws, "a2", "Mi columna").value, "x2")
        self.assertEqual(celda(ws, "a1", "Carátula").value,
                         "FISCO NACIONAL c/ OTRA EMPRESA s/EJECUCION FISCAL")
        self.assertEqual(celda(ws, "a1", "Detalle").value, "PRIMER AUTO ARCA")

    def test_fila_borrada_vuelve_y_las_filas_a_mano_quedan(self):
        self.exportar()
        wb = self.editar()
        ws = wb["Actuaciones"]
        ws.delete_rows(fila_de(ws, "a1"))
        ws.cell(row=3, column=columnas(ws)["Detalle"], value="llamar al cliente")
        self.guardar(wb)

        ws = self.exportar()["Actuaciones"]
        self.assertEqual(ids(ws), ["a2", "a1"])
        self.assertEqual(ws.cell(row=3, column=columnas(ws)["Detalle"]).value, "llamar al cliente")
        self.assertEqual(fila_de(ws, "a1"), 4)

    def test_causas_se_actualizan_sin_tocar_las_notas(self):
        self.exportar()
        wb = self.editar()
        celda(wb["Causas"], CAUSA, "Observaciones").value = "embargo pendiente"
        self.guardar(wb)

        self.causa("A SENTENCIA")
        ws = self.exportar()["Causas"]
        self.assertEqual(ids(ws), [CAUSA])
        self.assertEqual(celda(ws, CAUSA, "Situación").value, "A SENTENCIA")
        self.assertEqual(celda(ws, CAUSA, "Observaciones").value, "embargo pendiente")

    def test_tabla_de_excel_se_extiende(self):
        self.exportar()
        wb = self.editar()
        ws = wb["Actuaciones"]
        ws.auto_filter.ref = None  # Excel saca el filtro al dar formato de tabla
        ultima = ws.cell(row=1, column=ws.max_column).column_letter
        ws.add_table(Table(displayName="MisActuaciones", ref=f"A1:{ultima}{ws.max_row}"))
        self.guardar(wb)

        self.agregar(act("a3", "2026-10-20", "TRASLADO"), act("a4", "2026-10-21", "VISTA"))
        ws = self.exportar()["Actuaciones"]
        self.assertEqual(ws.tables["MisActuaciones"].ref, f"A1:{ultima}5")
        self.assertEqual(ids(ws), ["a1", "a2", "a3", "a4"])

    def test_hoja_sin_id_queda_como_anterior(self):
        self.exportar()
        wb = self.editar()
        ws = wb["Actuaciones"]
        celda(ws, "a1", "Observaciones").value = "no perder"
        ws.delete_cols(columnas(ws)["ID"])
        self.guardar(wb)

        wb = self.exportar()
        vieja = wb["Actuaciones (anterior)"]
        self.assertEqual(vieja.cell(row=2, column=columnas(vieja)["Observaciones"]).value,
                         "no perder")
        self.assertEqual(ids(wb["Actuaciones"]), ["a1", "a2"])

    def test_comentarios_de_excel_365_pasan_a_notas(self):
        self.exportar()
        comentario_moderno(self.ruta, "xl/worksheets/sheet1.xml", "E2", "ver plazo", "Dra. Prueba")

        ws = self.exportar()["Alertas"]  # sheet1 es Alertas, la primera hoja
        self.assertEqual(ws["E2"].comment.text, "Dra. Prueba: ver plazo")
        antes = self.ruta.stat().st_mtime_ns
        self.exportar()  # ya es una nota: no hay nada más que cambiar
        self.assertEqual(self.ruta.stat().st_mtime_ns, antes)

    def test_las_hojas_de_la_app_se_reescriben(self):
        self.exportar()
        wb = self.editar()
        wb["Por mes"]["B2"] = "lo toqué"
        self.guardar(wb)

        self.agregar(act("a3", "2026-10-20", "SENTENCIA EXPEDITA", "sentencia"))
        ws = self.exportar()["Por mes"]
        self.assertEqual([c.value for c in ws[2]], ["2026-10", 1, 1, 3])

    def test_respaldo_antes_de_cada_cambio_y_solo_los_ultimos(self):
        self.exportar()
        carpeta = self.ruta.parent / "respaldos"
        carpeta.mkdir()
        for i in range(12):
            (carpeta / f"Novedades PJN 2026-01-01 0000{i:02d}.xlsx").write_bytes(b"viejo")

        self.agregar(act("a3", "2026-10-20", "TRASLADO"))
        self.exportar()
        respaldos = sorted(p.name for p in carpeta.iterdir())
        self.assertEqual(len(respaldos), excel.RESPALDOS)
        self.assertNotIn("Novedades PJN 2026-01-01 000000.xlsx", respaldos)
        self.assertEqual(ids(load_workbook(carpeta / respaldos[-1])["Actuaciones"]), ["a1", "a2"])

    def test_sin_cambios_no_reescribe(self):
        self.exportar()
        antes = self.ruta.stat().st_mtime_ns
        self.exportar()
        self.assertEqual(self.ruta.stat().st_mtime_ns, antes)
        self.assertFalse((self.ruta.parent / "respaldos").exists())

    def test_excel_abierto_no_toca_nada(self):
        self.exportar()
        wb = self.editar()
        celda(wb["Actuaciones"], "a1", "Observaciones").value = "mía"
        self.guardar(wb)
        contenido = self.ruta.read_bytes()

        def bloqueado(*_):
            raise PermissionError("en uso")

        original, excel.os.replace = excel.os.replace, bloqueado
        try:
            self.agregar(act("a3", "2026-10-20", "TRASLADO"))
            with self.assertRaises(excel.ExcelAbierto):
                excel.exportar(self.con, self.ruta)
        finally:
            excel.os.replace = original
        self.assertEqual(self.ruta.read_bytes(), contenido)
        self.assertEqual(list((self.ruta.parent / "respaldos").iterdir()), [])
        self.assertFalse(self.ruta.with_name("Novedades PJN.tmp.xlsx").exists())

        ws = self.exportar()["Actuaciones"]  # cerrado: entra lo que quedó pendiente
        self.assertEqual(ids(ws), ["a1", "a2", "a3"])
        self.assertEqual(celda(ws, "a1", "Observaciones").value, "mía")

    def test_archivo_ilegible_queda_como_estaba(self):
        self.ruta.write_bytes(b"esto no es un Excel")
        with self.assertRaises(excel.ExcelIlegible):
            excel.exportar(self.con, self.ruta)
        self.assertEqual(self.ruta.read_bytes(), b"esto no es un Excel")


if __name__ == "__main__":
    unittest.main()
