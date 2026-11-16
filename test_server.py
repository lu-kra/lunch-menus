import unittest
import urllib.error
from unittest.mock import patch

import server


class SmeMenuTests(unittest.TestCase):
    def test_dated_menus_include_soups_prices_and_permanent_offer(self):
        menu = """
        <div class="dnesne_menu">
          <h2>Obedové menu Pondelok (16.11.2026)</h2>
          <div class="jedlo_polozka">
            <div class="left">Polievka: 0,33l Vývar 1,3,9</div>
          </div>
          <div class="jedlo_polozka">
            <div class="left">1: 150g Kuracie mäso<br>ryža &amp; zelenina (1, 7)</div>
            <span class="right"><b>7,50&nbsp;€</b></span>
          </div>
          <div class="jedlo_polozka">
            <div class="left"><b>Stála ponuka na tento týždeň:</b></div>
          </div>
          <div class="jedlo_polozka">
            <div class="left">3. 150g Vyprážaný syr 1,3,7</div>
            <span class="right"><b>8.00 €</b></span>
          </div>
        </div>
        <div class='ostatne_menu hidden'>
          <h2>Obedové menu Utorok (17.11.2026)</h2>
          <div class='jedlo_polozka'><div class='left'>0.33l Zeleninová polievka 9</div></div>
          <div class='jedlo_polozka'>
            <div class='left'>1: 150g Ryba, zemiaky A: 4</div>
          </div>
        </div>
        """
        days = server.parse_sme(menu)["days"]
        self.assertEqual(list(days), [0, 1])
        self.assertEqual(days[0]["date"], "16.11.")
        self.assertEqual(days[1]["date"], "17.11.")
        self.assertEqual(days[0]["items"], [
            server.item("Polievka", "0,33l Vývar", allergens="1,3,9"),
            server.item("Menu 1", "150g Kuracie mäso ryža & zelenina", "7,50 €", "1,7"),
            server.item("Stála ponuka · Menu 3", "150g Vyprážaný syr", "8.00 €", "1,3,7"),
        ])
        self.assertEqual(days[1]["items"], [
            server.item("Polievka", "0.33l Zeleninová polievka", allergens="9"),
            server.item("Menu 1", "150g Ryba, zemiaky", allergens="4"),
        ])

    def test_unavailable_menu_is_not_silently_accepted(self):
        for html in (
            "<title>Security Verification | SME</title>",
            '<div class="dnesne_menu"><h2>Obedové menu (16.11.2026)</h2></div>',
        ):
            with self.subTest(html=html), self.assertRaises(ValueError):
                server.parse_sme(html)

    def test_blocked_source_keeps_restaurant_and_original_link(self):
        restaurant = server.RESTAURANTS[-1]
        error = urllib.error.HTTPError(restaurant["fetch"], 403, "Forbidden", {}, None)
        with patch.object(server, "fetch", side_effect=error):
            result = server.scrape_one(restaurant)
        self.assertEqual(result["id"], "ukapra")
        self.assertEqual(result["url"], restaurant["url"])
        self.assertEqual(result["days"], {})
        self.assertIn("HTTP 403", result["error"])
        self.assertIn("kliknutím na názov", result["error"])


if __name__ == "__main__":
    unittest.main()
