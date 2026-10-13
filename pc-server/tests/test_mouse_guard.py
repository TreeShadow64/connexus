import unittest

from remote_input.mouse_guard import MODE_FREE, MODE_PRIMARY, MODE_SECONDARY, target_rect

MAIN = {"x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True, "description": "AMD Radeon RX 6700 XT"}
TV = {"x": 1920, "y": 0, "width": 1920, "height": 1080, "primary": False, "description": "Parsec Virtual Display Adapter"}
OTHER = {"x": -1280, "y": 0, "width": 1280, "height": 1024, "primary": False, "description": "Monitor generico"}


class TargetRectTest(unittest.TestCase):
    def test_libero_non_confina(self):
        self.assertIsNone(target_rect(MODE_FREE, [MAIN, TV]))

    def test_solo_schermo_1(self):
        self.assertEqual(target_rect(MODE_PRIMARY, [MAIN, TV]), (0, 0, 1920, 1080))

    def test_solo_schermo_2_e_il_monitor_virtuale(self):
        self.assertEqual(target_rect(MODE_SECONDARY, [MAIN, TV]), (1920, 0, 3840, 1080))

    def test_schermo_2_preferisce_il_virtuale_ad_altri_monitor(self):
        self.assertEqual(target_rect(MODE_SECONDARY, [MAIN, OTHER, TV]), (1920, 0, 3840, 1080))

    def test_schermo_2_senza_virtuale_usa_il_primo_altro(self):
        self.assertEqual(target_rect(MODE_SECONDARY, [MAIN, OTHER]), (-1280, 0, 0, 1024))

    def test_schermo_2_assente_lascia_libero(self):
        # TV spenta: il puntatore non deve restare intrappolato
        self.assertIsNone(target_rect(MODE_SECONDARY, [MAIN]))

    def test_primario_non_in_cima_alla_lista(self):
        self.assertEqual(target_rect(MODE_PRIMARY, [TV, MAIN]), (0, 0, 1920, 1080))

    def test_nessuno_schermo_o_modo_sconosciuto(self):
        self.assertIsNone(target_rect(MODE_PRIMARY, []))
        self.assertIsNone(target_rect("boh", [MAIN, TV]))


if __name__ == "__main__":
    unittest.main()
