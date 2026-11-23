import unittest

import server


class WordPressMenuParserTests(unittest.TestCase):
    def test_yuza_uses_main_image_and_full_srcset_original(self):
        html = """
        <header><img class="custom-logo" src="/logo.png"></header>
        <main><h1>Denné menu</h1>
        <img src="/uploads/menu-1024x700.png"
             srcset="/uploads/menu-768x525.png 768w, /uploads/menu-1024x700.png 1024w, /uploads/menu.png 1600w">
        </main>
        <div class="promo"><img src="/uploads/promo.png"></div>
        """
        result = server.parse_yuza(html)
        self.assertEqual(result["weekImages"], [{
            "src": "https://yuza.sk/uploads/menu-1024x700.png",
            "full": "https://yuza.sk/uploads/menu.png",
        }])

    def test_flow_ignores_outside_promo_and_caps_display_image(self):
        html = """
        <body><header><img class="site-logo" src="/logo.webp"></header>
        <div class="page-content"><h2>OBEDOVÉ MENU</h2>
        <img src="/uploads/weekly-menu.png"
             srcset="/uploads/weekly-menu-1024.png 1024w, /uploads/weekly-menu.png 2032w">
        </div></body><footer><div id="popup"><img src="/uploads/duck-promo.png"></div></footer>
        """
        result = server.parse_flow(html)
        self.assertEqual(result["weekImages"], [{
            "src": "https://www.flw.sk/uploads/weekly-menu-1024.png",
            "full": "https://www.flw.sk/uploads/weekly-menu.png",
        }])

    def test_missing_main_image_is_reported(self):
        with self.assertRaisesRegex(ValueError, "menu image not found"):
            server.parse_flow("<body><header>Obedové menu</header></body><footer>footer</footer>")


if __name__ == "__main__":
    unittest.main()
