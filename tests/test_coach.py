"""coach.py: the prompt asks for what's missing, the answer is parsed strictly
and merged into the config as text (comments and layout survive)."""
import tempfile
import tomllib
import unittest
from datetime import date, datetime
from pathlib import Path

import coach
import settings as S

SEASON = """# my plan
[season]
week1_start = 2030-01-07

[[phase]]
name = "Base"
start = 2030-01-07
end = 2030-03-31
run_km = 40

# ---- weeks ----

[[week]]  # Mon 7 Jan
n = 1
run_km = 30               # easy start

[[week]]  # Mon 21 Jan
n = 3
run_km = 50
"""
ATHLETE = """[easy]
hr_cap_bpm = 140

[bike]
max_hours = 3.0
# ftp_w = 250
"""
TODAY = date(2030, 1, 9)            # Wk1


def answer(body: str) -> str:
    return f"Here you go.\n```toml\n{body}\n```\nFLAGS: none\nMISSING: none"


class Coach(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        # one config file, as in real use: settings first, then the season plan
        self.season = self.athlete = root / "config.toml"
        self.season.write_text(ATHLETE + "\n" + SEASON)
        self.backups = root / "backups"

    def apply(self, text):
        return coach.apply(coach.parse(text), self.season, archive=None,
                           now=datetime(2030, 1, 9, 12), backups=self.backups)

    def test_prompt_lists_the_coming_weeks_and_what_is_missing(self):
        sea, cfg = S.load_season(self.season), S.load_athlete(self.athlete)
        nd = coach.needs(sea, cfg, TODAY)
        self.assertEqual([w.n for w in nd.weeks], [1, 2, 3, 4, 5])
        self.assertTrue(nd.urgent and nd.ftp_missing)
        p = coach.prompt(sea, cfg, TODAY)
        self.assertIn("Wk1 · Mon 7 Jan · run km 30 · threshold km MISSING", p)
        self.assertIn("Wk2 · Mon 14 Jan · run km 40", p)             # phase default
        self.assertIn("bike.ftp_w = MISSING", p)

    def test_existing_week_is_updated_in_place_and_comments_survive(self):
        changes = self.apply(answer("[[week]]\nn = 1\nrun_km = 35\nthreshold = [4, 6]"))
        self.assertEqual(changes, ["Wk1 run_km: 30 → 35", "Wk1 threshold: — → [4, 6]"])
        text = self.season.read_text()
        self.assertIn("# my plan", text)
        self.assertIn("hr_cap_bpm = 140", text)                     # settings untouched
        self.assertIn("# ---- weeks ----", text)
        self.assertEqual(text.count("n = 1\n"), 1)
        sea = S.load_season(self.season)
        self.assertEqual(sea.weeks[1]["run_km"], 35)
        self.assertEqual(sea.targets(date(2030, 1, 7))["threshold"], (4.0, 6.0))

    def test_new_week_goes_in_order(self):
        self.apply(answer("[[week]]\nn = 2\nthreshold = [0, 0]\n\n[[week]]\nn = 9\nrun_km = 60"))
        text = self.season.read_text()
        self.assertLess(text.index("n = 1\n"), text.index("n = 2\n"))
        self.assertLess(text.index("n = 2\n"), text.index("n = 3\n"))
        self.assertLess(text.index("n = 3\n"), text.index("n = 9\n"))
        self.assertIn("[[week]]  # Mon 14 Jan\nn = 2", text)
        weeks = tomllib.loads(text)["week"]
        self.assertEqual([w["n"] for w in weeks], [1, 2, 3, 9])

    def test_ftp_goes_into_the_bike_section(self):
        self.assertEqual(self.apply(answer("[bike]\nftp_w = 251")), ["bike FTP: — → 251 W"])
        cfg = tomllib.loads(self.athlete.read_text())
        self.assertEqual(cfg["bike"], {"max_hours": 3.0, "ftp_w": 251})
        self.assertEqual(self.apply(answer("[bike]\nftp_w = 251")), [])

    def test_backup_written_before_a_change(self):
        self.apply(answer("[[week]]\nn = 3\nrun_km = 55"))
        backups = list(self.backups.iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), ATHLETE + "\n" + SEASON)

    def test_bad_values_are_reported_not_guessed(self):
        up = coach.parse(answer("[[week]]\nn = 4\nrun_km = \"about 50\"\nthreshold = [16, 12]\nnote = 1\n"
                                "[[week]]\nn = 5\nrun_km = 45\n[race]\nname = 'x'"))
        self.assertEqual(up.weeks, {5: {"run_km": 45.0}})
        joined = " ".join(up.warnings)
        for bit in ("run_km 'about 50'", "threshold [16, 12]", "ignored 'note'", "[[race]] entries"):
            self.assertIn(bit, joined)

    def test_answers_without_values_are_refused(self):
        for text in ("no block, just prose", answer("[[week]]\nn = 3\n# threshold = MISSING"),
                     answer("[[week]\nn = 3"), answer("x = 1") + answer("y = 2")):
            with self.assertRaises(ValueError, msg=text):
                coach.parse(text)

    def test_preview_writes_nothing(self):
        files, changes = coach.plan(coach.parse(answer("[[week]]\nn = 3\nrun_km = 55")),
                                    self.season, "today")
        self.assertEqual(changes, ["Wk3 run_km: 50 → 55"])
        self.assertEqual(self.season.read_text(), ATHLETE + "\n" + SEASON)


    def test_sessions_settings_and_races_are_merged(self):
        changes = self.apply(answer(
            '[[session]]\ndate = 2030-01-08\nkind = "speed"\nnote = "planned fast session"\n\n'
            '[[race]]\ndate = 2030-03-01\nname = "Spring 10k"\npriority = "A"\n\n'
            '[threshold]\ncap_tolerance_bpm = 2\n\n[runs]\nthreshold_tolerance = 0.1\n\n'
            '[easy]\nhr_cap_bpm = 142'))
        self.assertIn("easy.hr_cap_bpm: 140 → 142", changes)
        self.assertIn("threshold.cap_tolerance_bpm: — → 2", changes)
        sea, cfg = S.load_season(self.season), S.load_athlete(self.season)
        self.assertEqual((cfg["easy"]["hr_cap_bpm"], cfg["threshold"]["cap_tolerance_bpm"],
                          cfg["runs"]["threshold_tolerance"]), (142, 2, 0.1))
        self.assertEqual(sea.sessions[0].kind, "speed")
        self.assertEqual([r.name for r in sea.races], ["Spring 10k"])
        # the same answer again changes nothing
        self.assertEqual(self.apply(answer('[[session]]\ndate = 2030-01-08\nkind = "speed"\n'
                                           'note = "planned fast session"\n[easy]\nhr_cap_bpm = 142')), [])

    def test_unknown_or_wrong_typed_settings_are_refused(self):
        up = coach.parse(answer('[easy]\nhr_cap_bpm = "high"\nnew_rule = 3\n[app]\nport = 80\n'
                                '[runs]\nthreshold_tolerance = 5\n[[week]]\nn = 2\nrun_km = 40'))
        self.assertEqual(up.settings, {})
        joined = " ".join(up.warnings)
        for bit in ("easy.hr_cap_bpm", "unknown key 'new_rule'", "ignored 'app'", "threshold_tolerance 5"):
            self.assertIn(bit, joined)

    def test_notes_are_kept_and_archived(self):
        text = answer("[[week]]\nn = 3\nrun_km = 55") + "\nNOTES: watch the left calf."
        up = coach.parse(text)
        self.assertIn("watch the left calf", up.notes)
        archive = Path(self.tmp.name) / "coach"
        coach.apply(up, self.season, now=datetime(2030, 1, 9, 12), backups=self.backups,
                    archive=archive, answer=text)
        saved = next(archive.iterdir()).read_text()
        self.assertIn("Wk3 run_km: 50 → 55", saved)
        self.assertIn("watch the left calf", saved)


    def test_pace_band_and_readiness_rule_from_an_answer(self):
        up = coach.parse(answer('[[session]]\ndate = 2030-01-08\npace = "3:35 - 3:42"\n'
                                '[hard_session]\nred_night_ok_readiness = "moderate"\n'
                                '[runs]\nrun_km_amber_under = 0.15\n[sleep]\namber_hours = 6.5'))
        self.assertEqual(up.sessions[0]["pace"], "3:35-3:42")
        self.assertEqual(up.settings[("hard_session", "red_night_ok_readiness")], "MODERATE")
        self.assertEqual(up.settings[("sleep", "amber_hours")], 6.5)       # hours, not a fraction
        bad = coach.parse(answer('[[session]]\ndate = 2030-01-08\npace = "fast"\n[[week]]\nn = 1\nrun_km = 30\n'
                                 '[hard_session]\nred_night_ok_readiness = "SOMETIMES"'))
        self.assertTrue(any("pace" in w for w in bad.warnings))
        self.assertTrue(any("red_night_ok_readiness" in w for w in bad.warnings))


if __name__ == "__main__":
    unittest.main()
