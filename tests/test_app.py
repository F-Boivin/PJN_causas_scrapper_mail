"""El log .txt de cada barrido: lo escribe la app mientras corre scraper.py y el
historial lo enlaza. Sin red ni login: un proceso falso hace de scraper.py."""

import http.client
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import almacen  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402

INICIO = "2026-11-03 11:54:08"


class ProcesoFalso:
    """Lo que imprimiría scraper.py, sin correrlo."""

    def __init__(self, lineas, rc=0):
        self.stdout = iter(linea + "\n" for linea in lineas)
        self.rc = rc

    def wait(self):
        return self.rc

    def kill(self):
        pass


def corrida_nueva():
    return {"id": 1, "origen": "App", "activa": True, "deteniendo": False,
            "inicio": time.time(), "fin": None, "lineas": [], "progreso": None,
            "t_primera": None, "resumen": None, "rc": None}


class TestLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {"CARPETA_DATOS": self.tmp.name}
        leer = mock.patch.object(config, "leer", return_value=self.cfg)
        leer.start()
        self.addCleanup(leer.stop)
        silencio = mock.patch.object(app, "log_app")
        silencio.start()
        self.addCleanup(silencio.stop)
        self.log = Path(self.tmp.name) / "logs" / "2026-11-03 115408.txt"

    def correr(self, lineas, rc=0):
        corrida = corrida_nueva()
        with mock.patch.object(app.subprocess, "Popen", return_value=ProcesoFalso(lineas, rc)):
            app._correr(corrida, ("20111111112", "clave-de-prueba"))
        return corrida

    def test_escribe_el_barrido_entero(self):
        corrida = self.correr([
            "[11:54:08] [INFO ] PJN Novedades — barrido",
            'CORRIDA_JSON {"id": 3, "inicio": "%s"}' % INICIO,
            "[11:54:09] [INFO ] Leyendo mails del PJN de los últimos 7 días...",
            'PROGRESO_JSON {"actual": 1, "total": 1, "hechas": 0}',
            "[11:54:12] [OK   ] Fin: 1/1 causas, 2 actuaciones nuevas",
            'RESUMEN_JSON {"ok": true, "corrida_id": 3}',
        ])
        lineas = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lineas[0], "PJN Novedades · barrido 3 · App · inicio 03/11/2026 11:54:08")
        self.assertEqual(lineas[2:5], [
            "[11:54:08] [INFO ] PJN Novedades — barrido",  # anterior al aviso de la corrida
            "[11:54:09] [INFO ] Leyendo mails del PJN de los últimos 7 días...",
            "[11:54:12] [OK   ] Fin: 1/1 causas, 2 actuaciones nuevas",
        ])
        self.assertTrue(lineas[-1].startswith("Fin "))
        self.assertNotIn("código", lineas[-1])
        texto = "\n".join(lineas)
        self.assertNotIn("_JSON", texto)
        self.assertNotIn("clave-de-prueba", texto)
        # la app sigue leyendo los avisos como antes
        self.assertEqual(corrida["resumen"], {"ok": True, "corrida_id": 3})
        self.assertEqual(len(corrida["lineas"]), 3)
        self.assertNotIn("txt", corrida)

    def test_error_queda_en_el_pie(self):
        self.correr(['CORRIDA_JSON {"id": 3, "inicio": "%s"}' % INICIO,
                     "Traceback (most recent call last):"], rc=1)
        pie = self.log.read_text(encoding="utf-8").splitlines()[-1]
        self.assertIn("terminó con error (código 1)", pie)

    def test_sin_corrida_no_hay_log(self):
        # falló antes de crear la corrida en la base: no hay barrido en el historial
        corrida = self.correr(["[11:54:08] [ERROR] Falta configurar: clave del PJN",
                               'RESUMEN_JSON {"ok": false, "errores": 1, "corrida_id": null}'], rc=1)
        self.assertFalse((Path(self.tmp.name) / "logs").exists())
        self.assertEqual(corrida["lineas"], ["[11:54:08] [ERROR] Falta configurar: clave del PJN"])

    def test_inicio_raro_no_da_ruta(self):
        for inicio in ("", None, "03/11/2026 11:54", "../../.env"):
            self.assertIsNone(config.ruta_log(self.cfg, inicio))


class TestHistorial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {"CARPETA_DATOS": self.tmp.name}
        leer = mock.patch.object(config, "leer", return_value=self.cfg)
        leer.start()
        self.addCleanup(leer.stop)
        with almacen.abrir(config.ruta_db(self.cfg)) as con:
            self.sin_log = almacen.crear_corrida(con, "App")
            self.con_log = almacen.crear_corrida(con, "App")
            con.execute("UPDATE corridas SET inicio = ? WHERE id = ?", (INICIO, self.con_log))
        self.log = config.ruta_log(self.cfg, INICIO)
        self.log.parent.mkdir(parents=True)
        self.log.write_bytes("PJN Novedades · barrido de prueba\r\n".encode("utf-8"))

    def test_marca_los_barridos_con_log(self):
        h = {f["id"]: f["log"] for f in app.vista_historial()}
        self.assertEqual(h, {self.sin_log: False, self.con_log: True})

    def test_la_ruta_sirve_el_log(self):
        srv = app.Servidor(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        def pedir(ruta):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request("GET", ruta, headers={"Host": f"127.0.0.1:{app.PORT}"})
            r = c.getresponse()
            cuerpo = r.read()
            c.close()
            return r.status, r.getheader("Content-Type"), cuerpo

        self.assertEqual(pedir(f"/log/{self.con_log}")[0], 401)  # sin sesión, nada
        with mock.patch.dict(app.estado, {"usuario": "20111111112", "password": "x"}):
            status, tipo, cuerpo = pedir(f"/log/{self.con_log}")
            self.assertEqual(status, 200)
            self.assertEqual(tipo, "text/plain; charset=utf-8")
            self.assertEqual(cuerpo, self.log.read_bytes())
            for ruta in (f"/log/{self.sin_log}", "/log/999", "/log/abc", "/log/..%2F.env"):
                self.assertEqual(pedir(ruta)[0], 404, ruta)


if __name__ == "__main__":
    unittest.main()
