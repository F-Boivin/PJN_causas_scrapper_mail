#!/usr/bin/env python3
"""Un barrido de novedades del PJN, de punta a punta.

    1. Lee de Gmail los mails de novedades del PJN de los últimos días.
    2. Arma la cola: una entrada por causa con novedades sin revisar.
    3. Entra al SCW (Playwright, sin ventana) y lee las actuaciones de cada
       causa, paginando hasta llegar a lo ya registrado.
    4. Registra SOLO las actuaciones nuevas CON documento (las que no traen
       archivo, como "A DESPACHO" o "EXPEDIENTE EN LETRA", son ruido procesal)
       y descarga su PDF.
    5. Marca las que son primer auto o sentencia (`clasificador.alerta`).
    6. Regenera el Excel.

Idempotente: re-correr no duplica (clave md5 por actuación). Si una causa
falla, sus mails quedan pendientes y el próximo barrido la reintenta.

Lo lanza la app (iniciar.bat), que pasa la clave del PJN por el entorno. Sin
la app: `PJN_PASSWORD=... python scraper.py`. Emite `PROGRESO_JSON` por causa
y `RESUMEN_JSON` al final (los lee la app); respeta el archivo `.detener`
entre causas.
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

from playwright.sync_api import sync_playwright

import almacen
import config
import excel
import gmail_pjn
from clasificador import PRIMER_AUTO, SENTENCIA, alerta
from pjn import (
    abrir_causa,
    codigo_expediente,
    compute_clave,
    descargar_archivo,
    extraer_actuaciones,
    extraer_datos_generales,
    fecha_iso,
    ir_a_pagina_siguiente,
    log,
    login_pjn,
    normalizar_fecha,
)

# La app crea este archivo para pedir STOP: se respeta entre causas, así
# ninguna queda a medias.
STOP_FLAG = str(config.DIR_APP / ".detener")

# Tope de páginas de actuaciones por causa (15 filas c/u, la más nueva primero).
MAX_PAGINAS = 5

# Una actuación anterior a la novedad más vieja del mail, menos este margen,
# es historia de la causa: se registra, pero no alerta en el barrido.
MARGEN_HISTORIA_DIAS = 7


def _nombre_archivo(texto: str, largo: int = 90) -> str:
    """Texto apto para nombre de archivo en Windows."""
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", texto or "")
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:largo].rstrip(" .") or "documento"


def ruta_pdf(cfg: dict, expediente: str, f: dict) -> str:
    """PDFs/<CAF 12345-2025>/<aaaa-mm-dd> <tipo> - <detalle>.pdf, sin pisar otro."""
    carpeta = config.carpeta_pdfs(cfg) / _nombre_archivo(expediente.replace("/", "-"))
    base = _nombre_archivo(f"{f['fecha_iso'] or 'sin fecha'} {f['tipo']} - {f['detalle']}")
    destino = carpeta / f"{base}.pdf"
    n = 2
    while destino.exists():
        destino = carpeta / f"{base} ({n}).pdf"
        n += 1
    return str(destino)


def procesar_causa(page, item: dict, claves: set, desde: dict, cfg: dict, cred: tuple,
                   con, corrida_id: int) -> dict:
    """Scrapea una causa de la cola y registra las actuaciones nuevas.

    Paginación: si la causa ya tiene actuaciones registradas, avanza páginas
    hasta una que no traiga nada nuevo — así también rellena huecos de
    corridas anteriores — con tope MAX_PAGINAS. Primer scrapeo de la causa:
    sólo la página 1. Nunca registra actuaciones anteriores a la más vieja ya
    registrada (`desde`): no vuelca el histórico.
    """
    abrir_causa(page, item["link"], *cred)
    datos = extraer_datos_generales(page)
    expediente = codigo_expediente(datos.get("expediente") or item["expediente"])
    caratula = datos.get("caratula") or item["caratula"]
    piso = desde.get(expediente)
    corte_historia = ""
    if item["desde"]:
        d = date.fromisoformat(item["desde"]) - timedelta(days=MARGEN_HISTORIA_DIAS)
        corte_historia = d.isoformat()

    nuevas, duplicadas, anteriores, sin_archivo, paginas = [], 0, 0, 0, 0
    tope = False
    while True:
        paginas += 1
        nuevas_pagina = conocidas_pagina = anteriores_pagina = 0
        for act in extraer_actuaciones(page):
            if not act["link_archivo"]:
                sin_archivo += 1
                continue
            fecha_norm = normalizar_fecha(act["fecha"])
            clave = compute_clave(expediente, fecha_norm, act["tipo"], act["detalle"], act["a_fs"])
            if clave in claves:
                duplicadas += 1
                conocidas_pagina += 1
                continue
            iso = fecha_iso(act["fecha"])
            if piso and iso and iso < piso:
                anteriores += 1
                anteriores_pagina += 1
                conocidas_pagina += 1
                continue
            claves.add(clave)
            nuevas_pagina += 1
            nuevas.append({
                "clave": clave, "expediente": expediente, "fecha": act["fecha"],
                "fecha_iso": iso, "tipo": act["tipo"], "detalle": act["detalle"],
                "a_fs": act["a_fs"], "link_archivo": act["link_archivo"],
                "alerta": alerta(act, expediente, caratula),
                "historica": bool(corte_historia and iso and iso < corte_historia),
                "alzada": bool(act["negrita"]),
            })

        if not piso:
            break  # primer scrapeo de la causa: sólo la página 1
        if anteriores_pagina:
            break  # ya pasó la actuación más vieja registrada
        if conocidas_pagina and not nuevas_pagina:
            break  # página entera ya registrada: lo que sigue es más viejo
        if paginas >= MAX_PAGINAS:
            tope = True
            log(f"    Tope de {MAX_PAGINAS} páginas sin llegar a lo ya registrado "
                f"— revisar la causa a mano por si falta algo más viejo", "warn")
            break
        if not ir_a_pagina_siguiente(page):
            break

    warnings = int(tope)
    archivos = 0
    if config.descargar_pdfs(cfg):
        for f in nuevas:
            destino = descargar_archivo(page, f["link_archivo"], ruta_pdf(cfg, expediente, f))
            if destino:
                f["pdf"] = destino
                archivos += 1
            else:
                warnings += 1

    almacen.guardar_causa(con, expediente, datos, item["link"])
    almacen.guardar_actuaciones(con, nuevas, corrida_id)
    almacen.marcar_procesadas(con, item["msg_ids"], corrida_id)
    con.commit()

    en_barrido = [f for f in nuevas if f["alerta"] and not f["historica"]]
    for f in en_barrido:
        nombre = "PRIMER AUTO" if f["alerta"] == PRIMER_AUTO else "SENTENCIA"
        log(f"    » {nombre}: {f['fecha']} · {f['detalle']}", "ok")

    return {"nuevas": len(nuevas), "duplicadas": duplicadas, "anteriores": anteriores,
            "archivos": archivos, "sin_archivo": sin_archivo, "paginas": paginas,
            "tope": tope, "warnings": warnings,
            "primeros_autos": sum(f["alerta"] == PRIMER_AUTO for f in en_barrido),
            "sentencias": sum(f["alerta"] == SENTENCIA for f in en_barrido),
            "historicas": sum(f["historica"] for f in nuevas)}


def progreso(**datos):
    """Línea estructurada para la barra de progreso de la app."""
    print("PROGRESO_JSON " + json.dumps(datos), flush=True)


def resumen(**datos):
    print("RESUMEN_JSON " + json.dumps(datos), flush=True)


def main() -> int:
    inicio = datetime.now()
    origen = "Manual"
    for arg in sys.argv[1:]:
        if arg.startswith("--origen="):
            origen = arg.split("=", 1)[1]
    log("PJN Novedades — barrido")

    cfg = config.leer()
    faltan = config.faltantes(cfg)
    password = os.environ.get("PJN_PASSWORD", "")
    if faltan or not password:
        log("Falta configurar: " + ", ".join(faltan + ([] if password else ["clave del PJN"])),
            "error")
        resumen(ok=False, errores=1, corrida_id=None)
        return 1
    cred = (cfg["PJN_USUARIO"], password)

    totales = {"mails": 0, "causas": 0, "nuevas": 0, "duplicadas": 0, "archivos": 0,
               "sin_archivo": 0, "errores": 0, "warnings": 0, "primeros_autos": 0,
               "sentencias": 0, "historicas": 0}
    con_tope, detenido = [], False

    with almacen.abrir(config.ruta_db(cfg)) as con:
        almacen.cerrar_colgadas(con)
        corrida_id = almacen.crear_corrida(con, origen)

        # 1. Gmail
        dias = config.dias_atras(cfg)
        log(f"Leyendo mails del PJN de los últimos {dias} días en {cfg['GMAIL_USUARIO']}...")
        try:
            novedades = gmail_pjn.buscar_novedades(cfg["GMAIL_USUARIO"], cfg["GMAIL_CLAVE_APP"],
                                                   dias, almacen.mails_vistos(con))
            totales["mails"] = almacen.guardar_novedades(con, novedades,
                                                         config.filtros_caratula(cfg))
            con.commit()
            log(f"{totales['mails']} mails nuevos del PJN", "ok")
        except gmail_pjn.ErrorGmail as e:
            log(f"Gmail: {e}", "error")
            log("Se revisa igual lo que había quedado pendiente de antes.", "warn")
            totales["errores"] += 1

        # 2. Cola
        cola = almacen.cola(con)
        for exp in cola["sin_link"]:
            log(f"Mail sin link a la causa (revisalo a mano): {exp}", "warn")
            totales["warnings"] += 1
        almacen.ignorar_sin_link(con)
        pendientes = cola["causas"]
        log(f"{len(pendientes)} causas con novedades para revisar")

        # 3. PJN
        if pendientes:
            claves, desde = almacen.indice(con)
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                login_pjn(page, *cred)
                time.sleep(2)
                total = len(pendientes)
                for i, item in enumerate(pendientes, start=1):
                    if os.path.exists(STOP_FLAG):
                        detenido = True
                        log(f"Detenido: {total - i + 1} causas quedan para el próximo barrido", "warn")
                        break
                    progreso(actual=i, total=total, hechas=i - 1, expediente=item["expediente"],
                             nuevas=totales["nuevas"], primeros_autos=totales["primeros_autos"],
                             sentencias=totales["sentencias"], errores=totales["errores"])
                    log(f"[{i}/{total}] {item['expediente']} - {item['caratula'][:60]}")
                    try:
                        r = procesar_causa(page, item, claves, desde, cfg, cred, con, corrida_id)
                        if r["tope"]:
                            con_tope.append(item["expediente"])
                        totales["causas"] += 1
                        for k in ("nuevas", "duplicadas", "archivos", "sin_archivo", "warnings",
                                  "primeros_autos", "sentencias", "historicas"):
                            totales[k] += r[k]
                        log(f"    {r['nuevas']} nuevas, {r['duplicadas']} ya registradas, "
                            f"{r['archivos']} PDFs, {r['paginas']} pág."
                            + (f", {r['historicas']} de la historia de la causa"
                               if r["historicas"] else "")
                            + (f", {r['warnings']} advertencias" if r["warnings"] else ""), "ok")
                    except Exception as e:
                        totales["errores"] += 1
                        log(f"    Error en {item['expediente']}: {e} (se reintenta en el "
                            f"próximo barrido)", "error")
                    time.sleep(1)
                else:
                    progreso(actual=total, total=total, hechas=total, expediente="",
                             nuevas=totales["nuevas"], primeros_autos=totales["primeros_autos"],
                             sentencias=totales["sentencias"], errores=totales["errores"])
            except KeyboardInterrupt:
                detenido = True
                log("Interrumpido: lo que falta queda para el próximo barrido", "warn")
            except Exception as e:
                log(f"Error: {e}", "error")
                totales["errores"] += 1
            finally:
                browser.close()
                pw.stop()

        # 4. Excel
        excel_ok = True
        try:
            ruta = excel.exportar(con, config.ruta_excel(cfg))
            log(f"Excel actualizado: {ruta}", "ok")
        except excel.ErrorExcel as e:
            excel_ok = False
            totales["warnings"] += 1
            log(str(e), "warn")

        duracion = str(datetime.now() - inicio).split(".")[0]
        log(f"Fin: {totales['causas']}/{len(pendientes)} causas, {totales['nuevas']} actuaciones "
            f"nuevas, {totales['primeros_autos']} primeros autos, {totales['sentencias']} "
            f"sentencias, {totales['errores']} errores, duración {duracion}", "ok")
        if con_tope:
            log(f"Tope de {MAX_PAGINAS} páginas en: {', '.join(con_tope)}", "warn")

        estado = "DETENIDA" if detenido else ("OK" if totales["errores"] == 0 else "CON ERRORES")
        almacen.cerrar_corrida(con, corrida_id, estado, {**totales, "duracion": duracion})

    resumen(ok=totales["errores"] == 0, corrida_id=corrida_id, duracion=duracion,
            detenido=detenido, excel_ok=excel_ok, pendientes=len(pendientes), **totales)
    return 0


if __name__ == "__main__":
    sys.exit(main())
