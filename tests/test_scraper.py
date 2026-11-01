"""Una causa de punta a punta contra una página local con la forma del SCW.

Abre un Chromium real (sin login ni red): lectura de datos generales y de la
tabla (incluida una fila en negrita de la alzada), filtro de lo que no trae
documento, dedup, alertas, historia de la causa y el Excel.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

import almacen  # noqa: E402
import excel  # noqa: E402
import scraper  # noqa: E402


def fila(fecha, tipo, detalle, link=True, negrita=False):
    cls = "font-negrita" if negrita else "font-color-black"
    a = '<a href="/scw/viewer.seam?id=1&tipoDoc=despacho">ver</a>' if link else ""
    celdas = "".join(f'<td><span class="{cls}">{t}</span></td>' for t in (fecha, tipo, detalle, "10"))
    return f"<tr><td>{a}</td><td>OFICINA</td>{celdas}</tr>"


PAGINA = f"""<html><body>
<fieldset>
  <div class="form-group"><label>Expediente:</label><div class="col-xs-10"><span>CAF 099001/2026 (PRUEBA)</span></div></div>
  <div class="form-group"><label>Jurisdicción:</label><div class="col-xs-10"><span>CAF - Contencioso Administrativo Federal</span></div></div>
  <div class="form-group"><label>Dependencia:</label><div class="col-xs-10"><span>JUZGADO FEDERAL DE EJECUCIONES FISCALES TRIBUTARIAS 5 - SECRETARIA 17</span></div></div>
  <div class="form-group"><label>Sit. Actual:</label><div class="col-xs-10"><span>EN DESPACHO</span></div></div>
  <div class="form-group"><label>Carátula:</label><div class="col-xs-10"><span>FISCO NACIONAL c/ EMPRESA DE PRUEBA S.A. s/EJECUCION FISCAL</span></div></div>
</fieldset>
<table id="expediente:action-table"><tbody>
  {fila("28/10/2026", "FIRMA DESPACHO", "SENTENCIA EXPEDITA - S17")}
  {fila("27/10/2026", "MOVIMIENTO", "A DESPACHO", link=False)}
  {fila("25/10/2026", "FIRMA DESPACHO", "SENTENCIA", negrita=True)}
  {fila("20/10/2026", "FIRMA DESPACHO", "PRIMER AUTO ARCA")}
  {fila("3/03/2026", "FIRMA DESPACHO", "1ER AUTO ANTICIPATORIO FISCO")}
</tbody></table></body></html>"""

ITEM = {"expediente": "CAF 99001/2026", "caratula": "", "link": "https://scw.pjn.gov.ar/scw/x?eid=1",
        "msg_ids": ["m1"], "desde": "2026-10-21"}


class TestCausa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)
        cls._abrir = scraper.abrir_causa
        scraper.abrir_causa = lambda page, url, *cred: None  # la página ya está cargada

    @classmethod
    def tearDownClass(cls):
        scraper.abrir_causa = cls._abrir
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"CARPETA_DATOS": self.tmp.name, "DESCARGAR_PDFS": "0"}
        self.db = Path(self.tmp.name) / "t.db"
        self.page = self.browser.new_page()
        self.page.set_content(PAGINA)

    def tearDown(self):
        self.page.close()
        self.tmp.cleanup()

    def correr(self, con, cid):
        claves, desde = almacen.indice(con)
        return scraper.procesar_causa(self.page, dict(ITEM), claves, desde, self.cfg,
                                      ("20000000001", "x"), con, cid)

    def test_causa_nueva_y_repetida(self):
        with almacen.abrir(self.db) as con:
            almacen.guardar_novedades(con, [{"msg_id": "m1", "recibido": "", "asunto": "",
                                             "expediente": ITEM["expediente"], "caratula": "",
                                             "fecha_novedad": "2026-10-21", "link": ITEM["link"]}], [])
            cid = almacen.crear_corrida(con, "test")
            r = self.correr(con, cid)
            self.assertEqual((r["nuevas"], r["sin_archivo"], r["historicas"]), (4, 1, 1))
            self.assertEqual((r["primeros_autos"], r["sentencias"]), (1, 1))

            barrido = almacen.alertas_de_corrida(con, cid)
            self.assertEqual([a["detalle"] for a in barrido["primer_auto"]], ["PRIMER AUTO ARCA"])
            self.assertEqual([a["detalle"] for a in barrido["sentencia"]], ["SENTENCIA EXPEDITA - S17"])
            self.assertEqual(barrido["sentencia"][0]["expediente"], "CAF 99001/2026")
            self.assertIn("EJECUCION FISCAL", barrido["sentencia"][0]["caratula"])

            # la historia no alerta en el barrido, pero cuenta en su mes
            self.assertEqual(len(almacen.alertas_del_mes(con, "2026-03")["primer_auto"]), 1)
            self.assertEqual(len(almacen.alertas_del_mes(con, "2026-10")["primer_auto"]), 1)

            alzada = con.execute("SELECT alerta, alzada FROM actuaciones WHERE fecha_iso = '2026-10-25'").fetchone()
            self.assertEqual(tuple(alzada), ("", 1))
            self.assertEqual(almacen.cola(con)["causas"], [])

            # otra vez la misma página: nada nuevo
            r2 = self.correr(con, almacen.crear_corrida(con, "test"))
            self.assertEqual((r2["nuevas"], r2["duplicadas"], r2["paginas"]), (0, 4, 1))

            ruta = excel.exportar(con, Path(self.tmp.name) / "Novedades PJN.xlsx")

        wb = load_workbook(ruta)
        self.assertEqual(wb.sheetnames, ["Alertas", "Por mes", "Actuaciones", "Causas",
                                         "Mails del PJN", "Corridas"])
        self.assertEqual(wb["Alertas"].max_row, 1 + 3)
        self.assertEqual(wb["Actuaciones"].max_row, 1 + 4)
        self.assertEqual(wb["Alertas"]["G2"].hyperlink.target,
                         "https://scw.pjn.gov.ar/scw/viewer.seam?id=1&tipoDoc=despacho")
        por_mes = {r[0]: r[1:] for r in wb["Por mes"].iter_rows(min_row=2, values_only=True)}
        self.assertEqual(por_mes["2026-10"], (1, 1, 3))


if __name__ == "__main__":
    unittest.main()
