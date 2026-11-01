"""El clasificador de alertas contra rótulos reales del PJN.

`casos_sentencia.json` es el corpus de regresión del relevamiento de
sentencias de ejecuciones fiscales: sólo pares (tipo, detalle) — etiquetas
procesales del sistema —, sin carátulas, números ni links.

    python -m unittest discover tests
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clasificador import (PRIMER_AUTO, SENTENCIA, alerta, clasificar_sentencia,  # noqa: E402
                          es_ejecucion, es_primer_auto, sigla_fuero)

CASOS = json.loads((Path(__file__).parent / "casos_sentencia.json").read_text(encoding="utf-8"))

EJECUCION = "FISCO NACIONAL c/ EMPRESA DE PRUEBA S.A. s/EJECUCION FISCAL"

PRIMER_AUTO_CASOS = [
    (True, "SEC 18 - PRIMER AUTO 1 (S/BONO - C/DOM. ELECTR.)"),
    (True, "INICIO AÑO 2025 SEC 6"),
    (True, "PRIMER DESPACHO -SE PROVEE DEMANDA- ACLARE TIPO SOCIETARIO"),
    (True, "PRIMER DESPACHO -SIN BONO- SEC 22 ACLARE SECTOR-"),
    (True, "DÉJESE SIN EFECTO. 1° AUTO (DISP. 120/2025).-"),
    (True, "1ER AUTO ANTICIPATORIO FISCO 2026"),
    (True, "*AUTO* - DIGITALES - ARCA - INICIAL EJECUCION (S2)"),
    (True, "PRIMER PROVEIDO ARCA - INT X BONO"),
    (True, "INICIO FISCO SIN BONO ( A.R.C.A.)"),
    (False, "SEC 18 - PRIMER AUTO - PREVIO ACLARE TIPO SOCIETARIO"),
    (False, "REQUIERE JUSTIFIQUE INICIO EJECUCIÓN RG AFIP 5482/24"),
    (False, "PRIMER DESPACHO -SUCESIÓN-"),
    (False, "INICIO - ACLARE TIPO SOCIETARIO"),
    (False, "INICIO SUCESIÓN"),
    (False, "DESPACHO: INICIO SUCESION - PREVIO DENUNCIE FECHA DE FALLECIMIENTO"),
    (False, "ACREDITADA SEA LA PERSONERIA SE PROVEERA EL ESCRITO DE INICIO"),
    (False, "SUCESION 1° AUTO"),
    (True, "SUCESION VUELVE DEL FISCAL SOMOS COMPETENTES"),
    (True, "F PRIMER AUTO + SUCESIÓN - DEUDA POSTERIOR A FALLECIMIENTO"),
    (False, "VUELVE DEL FISCAL SOMOS COMPETENTES"),
    (False, "DESPACHO - LETRA PRIMER AUTO"),
    (True, "SIN EFECTO MAND DE INT DE PAGO DILIG/PRIMER AUTO"),
    (False, "ESTESE AL AUTO ANTICIPATORIO"),
    (False, "NULIDAD MANDAMIENTO - FECHA LIBRAMIENTO ANTERIOR 1°AUTO"),
    (False, "HONORARIOS - INICIO EJECUCION - EMBARGOS -"),
    (False, "DEJESE SIN EFECTO EL PRIMER AUTO"),
    (False, "REVOCAR-PRIMER AUTO"),
    (False, "VIF - VISTA AL FISCAL DIGITAL"),
]


class TestSentencia(unittest.TestCase):
    def test_corpus_caf(self):
        for tipo, detalle, esperado in CASOS["CAF"]:
            with self.subTest(detalle=detalle):
                self.assertEqual(clasificar_sentencia(tipo, detalle, "CAF"), esperado)

    def test_corpus_css(self):
        for tipo, detalle, esperado in CASOS["CSS"]:
            with self.subTest(detalle=detalle):
                self.assertEqual(clasificar_sentencia(tipo, detalle, "CSS"), esperado)

    def test_str_es_sentencia_solo_en_css(self):
        self.assertEqual(clasificar_sentencia("FIRMA DESPACHO", "STR EJF", "CSS"), "sentencia")
        self.assertEqual(clasificar_sentencia("FIRMA DESPACHO", "STR EJF", "CAF"), "")

    def test_fuero_desconocido_usa_lo_comun(self):
        self.assertEqual(clasificar_sentencia("FIRMA DESPACHO", "SENTENCIA EXPEDITA", "FSM"),
                         "sentencia")


class TestPrimerAuto(unittest.TestCase):
    def test_rotulos(self):
        for esperado, rotulo in PRIMER_AUTO_CASOS:
            with self.subTest(rotulo=rotulo):
                self.assertEqual(es_primer_auto(rotulo), esperado)


class TestAlerta(unittest.TestCase):
    def act(self, tipo, detalle, negrita=0):
        return {"tipo": tipo, "detalle": detalle, "negrita": negrita}

    def test_primer_auto_en_ejecucion(self):
        self.assertEqual(alerta(self.act("FIRMA DESPACHO", "PRIMER AUTO ARCA"), "CAF 1/2026",
                                EJECUCION), PRIMER_AUTO)

    def test_primer_auto_solo_en_despacho(self):
        self.assertEqual(alerta(self.act("ESCRITO AGREGADO", "INICIO DEMANDA"), "CAF 1/2026",
                                EJECUCION), "")

    def test_primer_auto_no_en_otro_proceso(self):
        self.assertEqual(alerta(self.act("FIRMA DESPACHO", "PRIMER AUTO"), "CIV 1/2026",
                                "PEREZ c/ GOMEZ s/DAÑOS Y PERJUICIOS"), "")

    def test_caratula_vacia_no_descarta(self):
        self.assertEqual(alerta(self.act("FIRMA DESPACHO", "PRIMER AUTO"), "CAF 1/2026", ""),
                         PRIMER_AUTO)

    def test_sentencia_expedita(self):
        self.assertEqual(alerta(self.act("FIRMA DESPACHO", "SENTENCIA EXPEDITA - S17"),
                                "CAF 1/2026", EJECUCION), SENTENCIA)

    def test_sentencia_str_en_css(self):
        self.assertEqual(alerta(self.act("FIRMA DESPACHO", "STR ARCA"), "CSS 5/2025", EJECUCION),
                         SENTENCIA)

    def test_alzada_no_alerta(self):
        self.assertEqual(alerta(self.act("FIRMA DESPACHO", "SENTENCIA", negrita=1), "CAF 1/2026",
                                EJECUCION), "")

    def test_pedido_de_parte_no_alerta(self):
        self.assertEqual(alerta(self.act("ESCRITO AGREGADO", "SOLICITA SE DICTE SENTENCIA"),
                                "CAF 1/2026", EJECUCION), "")


class TestAuxiliares(unittest.TestCase):
    def test_sigla(self):
        self.assertEqual(sigla_fuero("CSS 12345/2025"), "CSS")
        self.assertEqual(sigla_fuero(""), "")

    def test_ejecucion(self):
        self.assertTrue(es_ejecucion("ARCA c/ X s/EJEC.FISCAL"))
        self.assertTrue(es_ejecucion("MIGRACIONES c/ X s/PROCESO DE EJECUCION TRIBUTARIA"))
        self.assertFalse(es_ejecucion("X c/ Y s/EJECUCION DE HONORARIOS"))


if __name__ == "__main__":
    unittest.main()
