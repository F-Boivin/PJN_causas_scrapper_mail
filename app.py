#!/usr/bin/env python3
"""App local de PJN Novedades: configuración, login y panel en el navegador.

Doble click en iniciar.bat → se abre http://127.0.0.1:8770 (sólo accesible
desde esta PC):

- La primera vez muestra el formulario de Configuración (Gmail, usuario del
  PJN, carpeta de datos) y genera el `.env`. Nadie edita archivos a mano.
- Pide la clave del PJN: se verifica contra el SSO y vive sólo en memoria
  mientras la app está abierta — no se guarda.
- Panel: buscar novedades (lanza scraper.py), progreso en vivo, STOP, alertas
  de primer auto y sentencia (del último barrido y del mes) e historial, con
  el log .txt de cada barrido (en la carpeta de datos, `logs/`).

Cerrar la ventana negra = cerrar la app (y olvidar la clave).
"""

import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import almacen
import config
import excel
import gmail_pjn
from pjn import CredencialesInvalidas, login_pjn
from scraper import STOP_FLAG

PORT = 8770
HOSTS_OK = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
TIMEOUT_CORRIDA = 2 * 3600
HTML = config.DIR_APP / "web" / "app.html"

lock = threading.Lock()
estado = {"usuario": None, "password": None, "corrida": None}


def log_app(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Corridas
# ---------------------------------------------------------------------------

def lanzar(origen: str) -> str | None:
    """Arranca un barrido en segundo plano. Retorna un error o None."""
    with lock:
        c = estado["corrida"]
        if c and c["activa"]:
            return "Ya hay un barrido en curso"
        if not estado["password"]:
            return "Sesión cerrada: ingresá de nuevo"
        corrida = {
            "id": int(time.time() * 1000), "origen": origen, "activa": True,
            "deteniendo": False, "inicio": time.time(), "fin": None, "lineas": [],
            "progreso": None, "t_primera": None, "resumen": None, "rc": None,
        }
        estado["corrida"] = corrida
        cred = (estado["usuario"], estado["password"])
    threading.Thread(target=_correr, args=(corrida, cred), daemon=True).start()
    return None


def _correr(corrida: dict, cred: tuple):
    if os.path.exists(STOP_FLAG):
        os.remove(STOP_FLAG)
    env = dict(os.environ, PJN_PASSWORD=cred[1], PYTHONIOENCODING="utf-8")
    args = [sys.executable, "-u", str(config.DIR_APP / "scraper.py"), f"--origen={corrida['origen']}"]
    proc = subprocess.Popen(args, cwd=str(config.DIR_APP), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace")
    timeout = threading.Timer(TIMEOUT_CORRIDA, proc.kill)
    timeout.start()

    for raw in proc.stdout:
        linea = raw.rstrip("\n")
        if linea.startswith("CORRIDA_JSON "):
            try:
                _abrir_log(corrida, json.loads(linea[len("CORRIDA_JSON "):]))
            except json.JSONDecodeError:
                pass
            continue
        for marca, campo in (("PROGRESO_JSON ", "progreso"), ("RESUMEN_JSON ", "resumen")):
            if linea.startswith(marca):
                try:
                    datos = json.loads(linea[len(marca):])
                except json.JSONDecodeError:
                    break
                with lock:
                    corrida[campo] = datos
                    if campo == "progreso" and corrida["t_primera"] is None:
                        corrida["t_primera"] = time.time()
                break
        else:
            _agregar(corrida, linea)
    rc = proc.wait()
    vencido = not timeout.is_alive()
    timeout.cancel()
    if vencido:
        _agregar(corrida, f"[app] El barrido superó {TIMEOUT_CORRIDA // 3600} h y se cortó.")
    if os.path.exists(STOP_FLAG):
        os.remove(STOP_FLAG)
    _cerrar_log(corrida, rc)
    with lock:
        corrida["rc"] = rc
        corrida["activa"] = False
        corrida["fin"] = time.time()
    log_app(f"Barrido terminado (rc={rc})")


def _agregar(corrida: dict, linea: str):
    with lock:
        corrida["lineas"].append(linea)
    _escribir_log(corrida, linea)


def _escribir_log(corrida: dict, linea: str):
    f = corrida.get("txt")
    if not f:
        return
    try:
        f.write(linea + "\n")
        f.flush()
    except (OSError, ValueError):
        pass


def _abrir_log(corrida: dict, datos: dict):
    """Abre el .txt del barrido cuando scraper.py avisa qué corrida de la base
    es, con lo que ya había escrito. Se escribe línea a línea: queda aunque la
    app se cierre a mitad del barrido."""
    ruta = config.ruta_log(config.leer(), datos.get("inicio"))
    if not ruta:
        return
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        corrida["txt"] = open(ruta, "w", encoding="utf-8")
    except OSError as e:
        _agregar(corrida, f"[app] No se pudo crear el log .txt: {e}")
        return
    inicio = datetime.strptime(datos["inicio"], "%Y-%m-%d %H:%M:%S")
    _escribir_log(corrida, f"PJN Novedades · barrido {datos.get('id')} · {corrida['origen']}"
                           f" · inicio {inicio:%d/%m/%Y %H:%M:%S}")
    _escribir_log(corrida, "")
    with lock:
        previas = list(corrida["lineas"])
    for linea in previas:
        _escribir_log(corrida, linea)


def _cerrar_log(corrida: dict, rc: int):
    duracion = timedelta(seconds=int(time.time() - corrida["inicio"]))
    _escribir_log(corrida, "")
    _escribir_log(corrida, f"Fin {datetime.now():%d/%m/%Y %H:%M:%S} · duración {duracion}"
                           + (f" · scraper.py terminó con error (código {rc})" if rc else ""))
    f = corrida.pop("txt", None)
    if f:
        f.close()


def detener():
    with lock:
        c = estado["corrida"]
        if not (c and c["activa"]):
            return
        c["deteniendo"] = True
    open(STOP_FLAG, "w").close()


# ---------------------------------------------------------------------------
# Login PJN
# ---------------------------------------------------------------------------

def validar_login(usuario: str, password: str) -> str | None:
    """Prueba el login en el SSO del PJN. Retorna un error o None."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
        try:
            login_pjn(browser.new_page(), usuario, password)
        finally:
            browser.close()
    except CredencialesInvalidas as e:
        return str(e)
    except Exception as e:
        if "Executable doesn't exist" in str(e):
            return "Falta instalar el navegador del scraper: corré instalar.bat"
        return f"No se pudo verificar el login: {e}"
    finally:
        pw.stop()
    return None


# ---------------------------------------------------------------------------
# Vistas JSON
# ---------------------------------------------------------------------------

def vista_estado(desde_id: int, desde_linea: int) -> dict:
    cfg = config.leer()
    with lock:
        c = estado["corrida"]
        out = {
            "configurado": not config.faltantes(cfg),
            "faltan": config.faltantes(cfg),
            "logueado": bool(estado["password"]),
            "usuario": estado["usuario"] or "",
            "usuario_sugerido": cfg.get("PJN_USUARIO", ""),
            "corrida": None,
        }
        if not c:
            return out
        p = c["progreso"] or {}
        eta = None
        hechas, total = p.get("hechas", 0), p.get("total", 0)
        if c["activa"] and hechas and c["t_primera"]:
            eta = (time.time() - c["t_primera"]) / hechas * (total - hechas)
        mismo = desde_id == c["id"]
        out["corrida"] = {
            "id": c["id"], "origen": c["origen"], "activa": c["activa"],
            "deteniendo": c["deteniendo"], "inicio": c["inicio"], "fin": c["fin"],
            "progreso": c["progreso"], "eta": eta, "resumen": c["resumen"], "rc": c["rc"],
            "lineas": c["lineas"][desde_linea if mismo else 0:],
            "desde": desde_linea if mismo else 0,
        }
        return out


def _fila(a: dict) -> dict:
    return {k: a.get(k) or "" for k in ("clave", "fecha", "fecha_iso", "expediente", "caratula",
                                         "detalle", "dependencia", "link_archivo", "link_causa")} \
        | {"tiene_pdf": bool(a.get("pdf"))}


def vista_alertas(mes: str) -> dict:
    cfg = config.leer()
    mes = mes or date.today().strftime("%Y-%m")
    with almacen.abrir(config.ruta_db(cfg)) as con:
        ultima = almacen.ultima_corrida_con_resultado(con)
        del_barrido = almacen.alertas_de_corrida(con, ultima["id"]) if ultima else {}
        del_mes = almacen.alertas_del_mes(con, mes)
        meses = almacen.meses_con_alertas(con)
        cont = almacen.contadores(con)
    if mes not in meses:
        meses = sorted(set(meses) | {mes}, reverse=True)
    return {
        "ultima": ultima,
        "barrido": {k: [_fila(a) for a in v] for k, v in del_barrido.items()},
        "mes": mes,
        "del_mes": {k: [_fila(a) for a in v] for k, v in del_mes.items()},
        "meses": meses,
        "contadores": cont,
        "excel_existe": config.ruta_excel(cfg).exists(),
    }


def vista_historial() -> list:
    cfg = config.leer()
    with almacen.abrir(config.ruta_db(cfg)) as con:
        filas = almacen.historial(con, 10)
    for f in filas:
        f["log"] = bool(ruta_log(cfg, f))
    return filas


def ruta_log(cfg: dict, corrida: dict | None) -> Path | None:
    """El .txt de un barrido, si existe."""
    p = config.ruta_log(cfg, corrida["inicio"]) if corrida else None
    return p if p and p.is_file() else None


def abrir_en_sistema(ruta: Path):
    if sys.platform.startswith("win"):
        os.startfile(str(ruta))  # noqa: S606 — ruta propia, no del usuario web
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(ruta)])
    else:
        subprocess.Popen(["xdg-open", str(ruta)])


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _cabeceras_comunes(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")

    def _json(self, data, code=200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cabeceras_comunes()
        self.end_headers()
        self.wfile.write(body)

    def _archivo(self, ruta: Path, tipo: str):
        body = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(body)))
        self._cabeceras_comunes()
        self.end_headers()
        self.wfile.write(body)

    def _permitido(self) -> bool:
        # Host: frena DNS rebinding. X-App: un sitio ajeno no puede mandar este
        # header a localhost sin preflight CORS, que no respondemos.
        if self.headers.get("Host") not in HOSTS_OK:
            self._json({"error": "host no permitido"}, 403)
            return False
        if self.path.startswith("/api/") and self.headers.get("X-App") != "1":
            self._json({"error": "falta X-App"}, 403)
            return False
        return True

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if not self._permitido():
            return
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        ruta = url.path
        if ruta == "/":
            self._archivo(HTML, "text/html; charset=utf-8")
            return
        if ruta == "/api/estado":
            try:
                rid, desde = int(q.get("run", 0)), int(q.get("desde", 0))
            except ValueError:
                rid, desde = 0, 0
            self._json(vista_estado(rid, desde))
            return
        if ruta == "/api/config":
            self._json(config.vista_publica(config.leer()))
            return
        if not estado["password"]:
            self._json({"error": "sesión cerrada"}, 401)
            return
        try:
            if ruta == "/api/alertas":
                self._json(vista_alertas(q.get("mes", "")))
            elif ruta == "/api/historial":
                self._json(vista_historial())
            elif ruta.startswith("/pdf/"):
                self._pdf(unquote(ruta[len("/pdf/"):]))
            elif ruta.startswith("/log/"):
                self._log(ruta[len("/log/"):])
            else:
                self._json({"error": "no existe"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _pdf(self, clave: str):
        cfg = config.leer()
        with almacen.abrir(config.ruta_db(cfg)) as con:
            ruta = almacen.ruta_pdf(con, clave)
        p = Path(ruta).resolve() if ruta else None
        base = config.carpeta_datos(cfg).resolve()
        if not p or not p.is_file() or base not in p.parents:
            self._json({"error": "PDF no encontrado"}, 404)
            return
        self._archivo(p, mimetypes.guess_type(p.name)[0] or "application/pdf")

    def _log(self, corrida_id: str):
        cfg = config.leer()
        c = None
        if corrida_id.isascii() and corrida_id.isdigit():
            with almacen.abrir(config.ruta_db(cfg)) as con:
                c = almacen.corrida(con, int(corrida_id))
        p = ruta_log(cfg, c)
        if not p:
            self._json({"error": "Ese barrido no tiene log"}, 404)
            return
        self._archivo(p, "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._permitido():
            return
        datos = self._body()
        ruta = self.path

        # --- Configuración: disponible sin login (es lo primero que se hace)
        if ruta == "/api/config":
            with lock:
                if estado["corrida"] and estado["corrida"]["activa"]:
                    self._json({"error": "Esperá a que termine el barrido"}, 409)
                    return
            try:
                cfg = config.guardar({k: datos.get(k) for k in config.DEFECTOS})
            except ValueError as e:
                self._json({"error": str(e)}, 400)
                return
            log_app("Configuración guardada")
            self._json({"ok": True, "faltan": config.faltantes(cfg)})
            return
        if ruta == "/api/probar-gmail":
            cfg = config.leer()
            usuario = (datos.get("GMAIL_USUARIO") or cfg["GMAIL_USUARIO"]).strip()
            clave = (datos.get("GMAIL_CLAVE_APP") or cfg["GMAIL_CLAVE_APP"]).strip()
            try:
                self._json({"ok": True, **gmail_pjn.probar(usuario, clave, 30)})
            except gmail_pjn.ErrorGmail as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:
                self._json({"error": f"No se pudo probar Gmail: {e}"}, 500)
            return
        if ruta == "/api/login":
            usuario = "".join(c for c in str(datos.get("usuario", "")) if c.isdigit())
            password = str(datos.get("password", ""))
            if not usuario or not password:
                self._json({"error": "Completá usuario y clave"}, 400)
                return
            error = validar_login(usuario, password)
            if error:
                self._json({"error": error}, 401)
                return
            with lock:
                estado["usuario"], estado["password"] = usuario, password
            log_app(f"Sesión iniciada ({usuario})")
            self._json({"ok": True})
            return
        if ruta == "/api/logout":
            with lock:
                if estado["corrida"] and estado["corrida"]["activa"]:
                    self._json({"error": "Esperá a que termine el barrido (o STOP)"}, 409)
                    return
                estado["usuario"], estado["password"] = None, None
            self._json({"ok": True})
            return

        if not estado["password"]:
            self._json({"error": "sesión cerrada"}, 401)
            return
        cfg = config.leer()
        if ruta == "/api/correr":
            error = lanzar("App")
            self._json({"error": error} if error else {"ok": True}, 409 if error else 200)
        elif ruta == "/api/detener":
            detener()
            self._json({"ok": True})
        elif ruta == "/api/excel":
            try:
                with almacen.abrir(config.ruta_db(cfg)) as con:
                    excel.exportar(con, config.ruta_excel(cfg))
                self._json({"ok": True})
            except excel.ErrorExcel as e:
                self._json({"error": str(e)}, 409)
        elif ruta == "/api/abrir":
            destino = {"excel": config.ruta_excel(cfg),
                       "carpeta": config.carpeta_datos(cfg)}.get(datos.get("que"))
            with lock:
                en_curso = bool(estado["corrida"] and estado["corrida"]["activa"])
            if datos.get("que") == "excel" and not en_curso and config.ruta_db(cfg).exists():
                # Si un barrido no pudo escribirlo (estaba abierto), lo pendiente se agrega ahora.
                try:
                    with almacen.abrir(config.ruta_db(cfg)) as con:
                        excel.exportar(con, destino)
                except excel.ExcelIlegible as e:
                    self._json({"error": str(e)}, 409)
                    return
                except excel.ExcelAbierto:
                    pass  # ya está abierto: se muestra como está
            if not destino or not destino.exists():
                self._json({"error": "Todavía no existe: corré un barrido primero"}, 404)
                return
            abrir_en_sistema(destino)
            self._json({"ok": True})
        else:
            self._json({"error": "no existe"}, 404)


class Servidor(ThreadingHTTPServer):
    """El puerto, en exclusiva. En Windows, el SO_REUSEADDR que HTTPServer pone
    por defecto deja que dos programas escuchen en el mismo puerto: la app no
    se enteraba de que ya estaba abierta y el navegador caía en cualquiera."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    url = f"http://127.0.0.1:{PORT}/"
    try:
        server = Servidor(("127.0.0.1", PORT), Handler)
    except OSError:
        print("La app ya está abierta: se abre el navegador.")
        webbrowser.open(url)
        return
    log_app(f"PJN Novedades · abierta en {url}  (cerrar esta ventana = cerrar la app)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
