"""La cola de causas, la configuración (.env) y su validación, sin red."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import almacen  # noqa: E402
import config  # noqa: E402


def nov(msg_id, expediente, fecha, link="https://scw.pjn.gov.ar/scw/x?eid=1",
        caratula="ARCA c/ X s/EJECUCION FISCAL"):
    return {"msg_id": msg_id, "recibido": f"{fecha}T23:00:00Z", "asunto": "", "expediente": expediente,
            "caratula": caratula, "fecha_novedad": fecha, "link": link}


class TestCola(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_agrupa_por_causa_y_toma_la_novedad_mas_vieja(self):
        with almacen.abrir(self.db) as con:
            n = almacen.guardar_novedades(con, [
                nov("1", "CAF 1/2026", "2026-10-20"),
                nov("2", "CAF 1/2026", "2026-10-18"),
                nov("3", "CSS 9/2025", "2026-10-21"),
                nov("4", "CAF 7/2026", "2026-10-21", link=""),
            ], [])
            self.assertEqual(n, 4)
            # el mismo mail otra vez no entra
            self.assertEqual(almacen.guardar_novedades(con, [nov("1", "CAF 1/2026", "2026-10-20")], []), 0)
            cola = almacen.cola(con)
        causas = {c["expediente"]: c for c in cola["causas"]}
        self.assertEqual(set(causas), {"CAF 1/2026", "CSS 9/2025"})
        self.assertEqual(sorted(causas["CAF 1/2026"]["msg_ids"]), ["1", "2"])
        self.assertEqual(causas["CAF 1/2026"]["desde"], "2026-10-18")
        self.assertEqual(cola["sin_link"], ["CAF 7/2026"])

    def test_filtro_de_caratula(self):
        with almacen.abrir(self.db) as con:
            almacen.guardar_novedades(con, [
                nov("1", "CAF 1/2026", "2026-10-20", caratula="ARCA c/ X s/EJECUCIÓN FISCAL"),
                nov("2", "CIV 5/2026", "2026-10-20", caratula="PEREZ c/ GOMEZ s/DAÑOS"),
            ], ["ejecucion fiscal"])
            cola = almacen.cola(con)
        self.assertEqual([c["expediente"] for c in cola["causas"]], ["CAF 1/2026"])

    def test_procesadas_salen_de_la_cola(self):
        with almacen.abrir(self.db) as con:
            almacen.guardar_novedades(con, [nov("1", "CAF 1/2026", "2026-10-20")], [])
            cid = almacen.crear_corrida(con, "test")
            almacen.marcar_procesadas(con, ["1"], cid)
            self.assertEqual(almacen.cola(con)["causas"], [])


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = config.ENV_PATH
        config.ENV_PATH = Path(self.tmp.name) / ".env"

    def tearDown(self):
        config.ENV_PATH = self._env
        self.tmp.cleanup()

    def test_sin_env_faltan_los_obligatorios(self):
        self.assertEqual(len(config.faltantes(config.leer())), 3)

    def test_ida_y_vuelta_con_barras_y_comillas(self):
        config.guardar({"GMAIL_USUARIO": "abogada@gmail.com", "GMAIL_CLAVE_APP": "abcd efgh ijkl mnop",
                        "PJN_USUARIO": "20-12345678-9", "CARPETA_DATOS": r"C:\Users\Ana\O'Brien\new",
                        "DIAS_ATRAS": "14", "FILTRO_CARATULA": "", "DESCARGAR_PDFS": "0"})
        cfg = config.leer()
        self.assertEqual(cfg["GMAIL_CLAVE_APP"], "abcdefghijklmnop")
        self.assertEqual(cfg["PJN_USUARIO"], "20123456789")
        self.assertEqual(cfg["CARPETA_DATOS"], r"C:\Users\Ana\O'Brien\new")
        self.assertFalse(config.descargar_pdfs(cfg))
        self.assertEqual(config.faltantes(cfg), [])

    def test_secreto_vacio_conserva_el_guardado(self):
        base = {"GMAIL_USUARIO": "a@gmail.com", "GMAIL_CLAVE_APP": "a" * 16, "PJN_USUARIO": "20123456789"}
        config.guardar(base)
        config.guardar({**base, "GMAIL_CLAVE_APP": ""})
        self.assertEqual(config.leer()["GMAIL_CLAVE_APP"], "a" * 16)
        self.assertEqual(config.vista_publica(config.leer())["GMAIL_CLAVE_APP"], "")

    def test_validacion(self):
        with self.assertRaises(ValueError):
            config.guardar({"GMAIL_USUARIO": "sin-arroba", "GMAIL_CLAVE_APP": "corta", "PJN_USUARIO": "123"})
        self.assertFalse(config.ENV_PATH.exists())


if __name__ == "__main__":
    unittest.main()
