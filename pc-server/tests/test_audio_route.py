import tempfile
import unittest
from pathlib import Path

from streaming.audio_route import ROUTE_PC, ROUTE_TV, AudioRoute


class FakeAudio:
    def __init__(self, muted=False):
        self.muted = muted

    def get_mute(self):
        return self.muted

    def set_mute(self, muted):
        self.muted = muted


class AudioRouteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.flag = Path(self.dir.name) / "flag"

    def tearDown(self):
        self.dir.cleanup()

    def make(self, muted=False):
        audio = FakeAudio(muted)
        return AudioRoute(audio, self.flag), audio

    def test_audio_sulla_tv_silenzia_il_pc_solo_a_schermo_acceso(self):
        route, audio = self.make()
        self.assertFalse(audio.muted)            # secondo schermo spento: non si tocca
        route.session(True)
        self.assertTrue(audio.muted)
        self.assertTrue(self.flag.exists())
        route.session(False)
        self.assertFalse(audio.muted)
        self.assertFalse(self.flag.exists())

    def test_audio_sul_pc_non_silenzia(self):
        route, audio = self.make()
        route.set_route(ROUTE_PC)
        route.session(True)
        self.assertFalse(audio.muted)

    def test_cambio_di_percorso_a_sessione_attiva(self):
        route, audio = self.make()
        route.session(True)
        self.assertTrue(audio.muted)
        route.set_route(ROUTE_PC)
        self.assertFalse(audio.muted)
        route.set_route(ROUTE_TV)
        self.assertTrue(audio.muted)

    def test_gia_muto_per_scelta_dell_utente_resta_muto(self):
        route, audio = self.make(muted=True)
        route.session(True)
        route.session(False)
        self.assertTrue(audio.muted)             # non l'abbiamo mai toccato
        self.assertFalse(self.flag.exists())

    def test_scegliere_il_pc_riaccende_altoparlanti_muti(self):
        route, audio = self.make(muted=True)     # muti da prima (non per colpa nostra)
        route.session(True)
        self.assertTrue(audio.muted)
        route.set_route(ROUTE_PC)
        self.assertFalse(audio.muted)
        route.set_route(ROUTE_TV)                # tornando alla TV adesso li spegniamo noi
        self.assertTrue(audio.muted)
        route.session(False)
        self.assertFalse(audio.muted)

    def test_percorso_sconosciuto_ignorato(self):
        route, _ = self.make()
        self.assertEqual(route.set_route("boh"), ROUTE_TV)

    def test_ripristino_dopo_arresto_brusco(self):
        route, audio = self.make()
        route.session(True)
        self.assertTrue(audio.muted)
        # nuova esecuzione dell'app: stato in memoria perso, resta il file marcatore
        nuova, _ = AudioRoute(audio, self.flag), None
        nuova.restore_leftover()
        self.assertFalse(audio.muted)
        self.assertFalse(self.flag.exists())

    def test_errore_del_backend_non_propaga(self):
        class Rotto:
            def get_mute(self):
                raise OSError("nessun dispositivo")

            def set_mute(self, muted):
                raise OSError("nessun dispositivo")

        route = AudioRoute(Rotto(), self.flag)
        route.session(True)                      # non deve sollevare
        route.session(False)


if __name__ == "__main__":
    unittest.main()
