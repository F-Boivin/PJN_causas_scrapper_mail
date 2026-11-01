# PJN Novedades — guía para el asistente de instalación

Esta guía es para el asistente de IA que ayuda a instalar la app: Claude Code
(la lee a través de `CLAUDE.md`), Codex (la lee directo) o Gemini CLI (a
través de `GEMINI.md`).

Esta carpeta es una app local que lee los avisos de novedades del PJN desde
Gmail, revisa esas causas en el sistema de consulta del PJN (SCW), detecta
primeros autos y sentencias, y arma un Excel. Ver `README.md`.

Lo más probable es que la persona que te habla sea **un abogado o abogada que
no programa** y que quiere instalar la app. Tu trabajo es dejarla andando.

## Reglas (no negociables)

1. **Nunca pidas contraseñas en el chat**: ni la contraseña de aplicación de
   Gmail ni la clave del PJN. Si la persona te las pega igual, decile que no
   hacía falta, que no las vas a usar, y que las cargue en el formulario. La
   contraseña de aplicación se carga en el formulario de Configuración de la
   app, que genera el `.env`. La clave del PJN se ingresa en la pantalla de
   login de la app y no se guarda.
2. **No escribas ni edites el `.env` a mano**, y no lo muestres en el chat:
   lo genera el formulario. Para saber si falta configurar algo, abrí la app;
   el formulario lo dice.
3. **Nunca subas, commitees ni compartas** `.env`, `datos/`, `*.db`, `*.xlsx`
   ni PDFs: tienen datos de causas y credenciales. El `.gitignore` ya los
   bloquea; no lo debilites.
4. No modifiques el código salvo que haya un error real que lo impida
   funcionar. Si lo tocás, corré los tests (abajo).
5. Hablá en castellano simple, un paso por vez, y ejecutá vos los comandos.
   Antes de un paso que la persona tiene que hacer en el navegador (crear la
   contraseña de aplicación, por ejemplo), explicale exactamente qué tocar.

## Instalación paso a paso (Windows)

Usá PowerShell. Todos los comandos, desde esta carpeta.

1. **Python 3.10 o más nuevo**:
   `python --version`. Si falla o abre la Microsoft Store, instalalo con
   `winget install -e --id Python.Python.3.12 --scope user` y pedí que cierre
   y vuelva a abrir el asistente (Windows necesita una terminal nueva para
   encontrarlo). Alternativa: python.org, tildando "Add python.exe to PATH".
   Si está el lanzador `py`, `py -3 --version` también sirve: usá `py -3` en
   lugar de `python` en los pasos siguientes.
2. **Librerías**: `python -m pip install -r requirements.txt`
3. **Navegador del scraper**: `python -m playwright install chromium`
4. **Verificación**: `python -m unittest discover -s tests`. Tiene que decir
   `OK`; un test abre un Chromium local, sin red.
5. **Abrir la app**: `python app.py`. Abre el navegador en
   `http://127.0.0.1:8770`. Lanzala en segundo plano, porque queda corriendo.
   Explicá que la ventana negra **es** la app.
6. **Acceso directo en el Escritorio**: crealo siempre, sin preguntar. Es el
   mismo que ofrece `instalar.bat`. En PowerShell, desde esta carpeta:

   ```powershell
   $d = [Environment]::GetFolderPath('Desktop'); $s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'PJN Novedades.lnk')); $s.TargetPath = (Resolve-Path .\iniciar.bat).Path; $s.WorkingDirectory = (Get-Location).Path; $s.Save()
   ```

   Contale que de ahora en más abre la app con el ícono **PJN Novedades** del
   Escritorio, y que si mueve la carpeta de la app hay que volver a crearlo.
7. **Configuración**: el formulario se abre solo la primera vez. Guiá a la
   persona campo por campo:
   - **Gmail**: la dirección donde le llegan los avisos de
     `scw-noreply@pjn.gov.ar`.
   - **Contraseña de aplicación**: requiere la verificación en 2 pasos de
     Google. Se crea en https://myaccount.google.com/apppasswords: nombre
     "PJN Novedades" → Crear → copiar las 16 letras al formulario. Que
     después toque **Probar conexión con Gmail**.
   - **Usuario del PJN**: su CUIT/CUIL.
   - **Opciones**: los valores por defecto sirven. Si usa Google Drive para
     escritorio u OneDrive, puede elegir una carpeta sincronizada como
     carpeta de datos, así tiene el Excel en la nube.
8. **Primer barrido**: que ingrese su clave del PJN en la app y toque
   **▶ Buscar novedades**. Si quiere traer más historia la primera vez, puede
   subir "Días hacia atrás" (hasta 90) en Configuración.

En Mac o Linux (no probado), lo mismo con `python3`. `instalar.bat` e `iniciar.bat` son
sólo para Windows.

## Problemas típicos

| Síntoma | Causa y solución |
|---|---|
| `python` abre la Microsoft Store | Es el alias de Windows: instalar Python (paso 1). |
| `pip` falla por permisos | `python -m pip install --user -r requirements.txt` |
| `playwright install` falla | Proxy o antivirus. Reintentar. Si hay proxy corporativo, `HTTPS_PROXY`. |
| "Gmail rechazó el acceso" | No es una contraseña de aplicación, o falta la verificación en 2 pasos. |
| Probar conexión: 0 mails | Los avisos llegan a otra cuenta, o hace más de 30 días que no llegan. |
| "Usuario o contraseña del PJN incorrectos" | Que pruebe a mano en https://portalpjn.pjn.gov.ar |
| Timeouts del PJN | El PJN bloquea IPs de nube/datacenter: tiene que correr desde una conexión residencial u oficina en Argentina. A veces es lentitud del PJN: reintentar; lo pendiente se retoma solo. |
| "La app ya está abierta" | Ya hay una instancia en el puerto 8770: usar esa pestaña, o cerrar su ventana negra. |
| El Excel no se actualiza | Está abierto en Excel: cerrarlo y volver a buscar novedades. |

## Para trabajar en el código

- Tests: `python -m unittest discover -s tests`, sin red ni login.
- `clasificador.py` copia reglas calibradas en un relevamiento de sentencias
  de ejecuciones fiscales. `tests/casos_sentencia.json` es su corpus de
  regresión. No cambies un patrón sin agregar el caso real que lo justifica.
- La base SQLite es la fuente de verdad; el Excel se regenera entero.
- El servidor escucha sólo en 127.0.0.1, con chequeo de `Host` y del header
  `X-App` contra sitios ajenos. No lo abras a la red.
- Nada de datos reales en tests ni en commits: carátulas, CUIT y links de
  ejemplo tienen que ser inventados.
