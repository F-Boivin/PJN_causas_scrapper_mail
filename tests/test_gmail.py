"""El parseo de los mails del PJN y la búsqueda IMAP, sin red.

Los mails son de prueba (datos inventados) con la forma de los avisos de
scw-noreply@pjn.gov.ar: asunto "Novedad en expediente <FUERO> <nº>/<año>",
y en el cuerpo "<expediente> - <carátula> Fecha: dd-mm-aaaa" y el link al SCW.
"""

import sys
import unittest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gmail_pjn  # noqa: E402

LINK = ("http://scw.pjn.gov.ar/scw/consultaNovedad.seam?identificacion=20000000001"
        "&idCamara=3&eid=12345678")


def mail(asunto, texto=None, htm=None, fecha="Tue, 21 Oct 2026 21:15:00 -0300") -> bytes:
    m = MIMEMultipart("alternative")
    m["Subject"] = asunto
    m["From"] = "scw-noreply@pjn.gov.ar"
    m["Date"] = fecha
    if texto is not None:
        m.attach(MIMEText(texto, "plain", "utf-8"))
    if htm is not None:
        m.attach(MIMEText(htm, "html", "utf-8"))
    return m.as_bytes()


TEXTO = (
    "Sistema de Notificaciones Electrónicas\n"
    "Se registró una novedad en el expediente CAF 099001/2026 - FISCO NACIONAL - AFIP c/ "
    "EMPRESA DE PRUEBA S.A. s/EJECUCION FISCAL\n"
    "Fecha: 21-10-2026\n"
    f"Para consultarla ingrese a {LINK}\n")


class TestParseo(unittest.TestCase):
    def test_mail_de_texto(self):
        n = gmail_pjn.parsear_mail(mail("Novedad en expediente CAF 99001/2026", TEXTO), "1")
        self.assertEqual(n["msg_id"], "1")
        self.assertEqual(n["expediente"], "CAF 99001/2026")
        self.assertEqual(n["caratula"],
                         "FISCO NACIONAL - AFIP c/ EMPRESA DE PRUEBA S.A. s/EJECUCION FISCAL")
        self.assertEqual(n["fecha_novedad"], "2026-10-21")
        self.assertEqual(n["link"], LINK)
        self.assertEqual(n["recibido"], "2026-10-22T00:15:00Z")

    def test_link_solo_en_el_href_del_html(self):
        htm = ("<p>Novedad en el expediente CSS 5/2025 - ARCA c/ PERSONA DE PRUEBA "
               "s/EJECUCION FISCAL</p><p>Fecha: 03-09-2025</p>"
               f"<a href=\"{LINK.replace('&', '&amp;')}\">Ver la novedad</a>")
        n = gmail_pjn.parsear_mail(mail("Novedad en expediente CSS 5/2025", htm=htm))
        self.assertEqual(n["expediente"], "CSS 5/2025")
        self.assertEqual(n["caratula"], "ARCA c/ PERSONA DE PRUEBA s/EJECUCION FISCAL")
        self.assertEqual(n["fecha_novedad"], "2025-09-03")
        self.assertEqual(n["link"], LINK)

    def test_asunto_con_acentos_codificado(self):
        n = gmail_pjn.parsear_mail(mail("Novedad en expediente FSM 99002/2025 — notificación",
                                        TEXTO.replace("CAF 099001/2026", "FSM 99002/2025")))
        self.assertEqual(n["expediente"], "FSM 99002/2025")
        self.assertIn("notificación", n["asunto"])

    def test_mail_sin_link(self):
        n = gmail_pjn.parsear_mail(mail("Novedad en expediente CAF 1/2026",
                                        "expediente CAF 1/2026 - X c/ Y Fecha: 01-11-2026"))
        self.assertEqual(n["link"], "")
        self.assertEqual(n["caratula"], "X c/ Y")


class FakeIMAP:
    """Lo mínimo de imaplib que usa buscar_novedades, con respuestas de Gmail."""

    def __init__(self, mails: dict[str, tuple[str, bytes]]):
        self.mails = mails  # uid -> (gm_msgid, raw)
        self.carpeta = None
        self.pedidos = []

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"',
                      b'(\\All \\HasNoChildren) "/" "[Gmail]/Todos"']

    def select(self, nombre, readonly=False):
        self.carpeta, self.readonly = nombre, readonly
        return "OK", [str(len(self.mails)).encode()]

    def uid(self, comando, *args):
        if comando == "SEARCH":
            self.consulta = args[1]
            return "OK", [" ".join(self.mails).encode()]
        uids, que = args[0].split(","), args[1]
        self.pedidos.append((uids, que))
        if que == "(X-GM-MSGID)":
            return "OK", [f"{i} (X-GM-MSGID {self.mails[u][0]} UID {u})".encode()
                          for i, u in enumerate(uids, 1)]
        resp = []
        for i, u in enumerate(uids, 1):
            raw = self.mails[u][1]
            if i % 2:  # el UID antes del cuerpo...
                resp += [(f"{i} (UID {u} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
            else:      # ...o en el cierre: Gmail no garantiza el orden
                resp += [(f"{i} (BODY[] {{{len(raw)}}}".encode(), raw), f" UID {u})".encode()]
        return "OK", resp

    def logout(self):
        pass


class TestBusqueda(unittest.TestCase):
    def setUp(self):
        self.fake = FakeIMAP({
            "11": ("900", mail("Novedad en expediente CAF 1/2026", TEXTO.replace("099001", "1"))),
            "12": ("901", mail("Novedad en expediente CAF 2/2026", TEXTO.replace("099001", "2"))),
            "13": ("902", mail("Novedad en expediente CAF 3/2026", TEXTO.replace("099001", "3"))),
        })
        self._conectar = gmail_pjn.conectar
        gmail_pjn.conectar = lambda u, c: self.fake

    def tearDown(self):
        gmail_pjn.conectar = self._conectar

    def test_busca_en_todos_sin_marcar_leidos_y_saltea_vistos(self):
        novedades = gmail_pjn.buscar_novedades("x@gmail.com", "a" * 16, 7, {"900"})
        self.assertEqual(self.fake.carpeta, '"[Gmail]/Todos"')
        self.assertTrue(self.fake.readonly)
        self.assertEqual(self.fake.consulta, '"from:scw-noreply@pjn.gov.ar newer_than:7d"')
        self.assertEqual(sorted(n["msg_id"] for n in novedades), ["901", "902"])
        self.assertEqual(sorted(n["expediente"] for n in novedades),
                         ["CAF 2/2026", "CAF 3/2026"])
        # el cuerpo se pide con BODY.PEEK (no marca como leído) y sólo de los nuevos
        self.assertEqual(self.fake.pedidos[-1], (["12", "13"], "(UID BODY.PEEK[])"))


if __name__ == "__main__":
    unittest.main()
