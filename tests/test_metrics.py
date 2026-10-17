import unittest
from datetime import date, datetime, time, timedelta

import metrics as M
import settings as S
from tests.fixtures import (LT_PACE, MON, act, cfg, lap, model, night, readiness, season,
                            series, subt_session)

SUN_NIGHT = datetime.combine(MON + timedelta(days=7), time(0, 1))   # week complete
WED_NOON = datetime.combine(MON + timedelta(days=2), time(12))


class Reps(unittest.TestCase):
    def test_junk_lap_is_ignored(self):
        # real data had a 26.7 m / 9.7 s ACTIVE lap that Garmin's summary counted as a rep
        a = subt_session(0, [160, 160])
        a.laps.append(lap(99, 26.7, 9.7, 150))
        reps = M.reps_for(a, LT_PACE, cfg())
        self.assertEqual(len(reps), 2)

    def test_cap_boundary_counts_as_within(self):
        reps = M.reps_for(subt_session(0, [165]), LT_PACE, cfg())
        self.assertTrue(reps[0].within)

    def test_cap_depends_on_rep_length(self):
        long_rep = M.reps_for(subt_session(0, [167], rep_m=1200), LT_PACE, cfg())[0]
        short_rep = M.reps_for(subt_session(0, [167], rep_m=1000), LT_PACE, cfg())[0]
        self.assertEqual((long_rep.cap, long_rep.within), (165, False))
        self.assertEqual((short_rep.cap, short_rep.within), (170, True))

    def test_faster_than_lt_is_speed_not_threshold(self):
        reps = M.reps_for(subt_session(0, [185], rep_m=400, rep_pace=LT_PACE - 20), LT_PACE, cfg())
        self.assertEqual(reps[0].kind, "speed")
        self.assertIsNone(reps[0].within)

    def test_much_slower_than_lt_is_aerobic(self):
        reps = M.reps_for(subt_session(0, [140], rep_pace=LT_PACE + 60), LT_PACE, cfg())
        self.assertEqual(reps[0].kind, "aerobic")

    def test_session_slightly_faster_than_lt_is_still_subt(self):
        # real sub-T reps often run a few s/km under Garmin's LT estimate
        reps = M.reps_for(subt_session(0, [160, 160], rep_pace=LT_PACE - 5), LT_PACE, cfg())
        self.assertEqual({r.kind for r in reps}, {"subt"})

    def test_one_rep_run_too_fast_is_still_judged_against_the_cap(self):
        # a per-rep rule would call this rep "speed" and hide the breach
        a = subt_session(0, [160, 160, 160])
        fast = a.laps[3]                       # second rep
        fast.duration_s = fast.distance_m / 1000 * (LT_PACE - 15)
        fast.avg_hr = 176
        reps = M.reps_for(a, LT_PACE, cfg())
        self.assertEqual([r.kind for r in reps], ["subt"] * 3)
        self.assertFalse(reps[1].within)


class Sessions(unittest.TestCase):
    def test_over_cap_session_is_red_with_both_totals(self):
        a = subt_session(1, [164, 165, 167])          # 165 = cap: within
        s = M.classify([a], model(a), cfg(), season())[0]
        self.assertEqual((s.kind, s.status), ("subt", "act"))
        self.assertAlmostEqual(s.within_km, 3.2)
        self.assertAlmostEqual(s.over_km, 1.6)
        self.assertEqual(s.title, "Sub-T 3×1600")

    def test_speed_session_counts_no_threshold_km(self):
        a = subt_session(1, [182] * 6, rep_m=400, rep_pace=LT_PACE - 25)
        s = M.classify([a], model(a), cfg(), season())[0]
        self.assertEqual((s.kind, s.within_km, s.over_km), ("speed", 0, 0))

    def test_easy_run_leak_caught_by_share_when_average_looks_fine(self):
        # average pulled down by a walk at the end; most running sat above the cap
        ser = series((25, 150, 3.0, None), (15, 120, 1.6, None))
        a = act(minutes=40, avg_hr=139, ser=ser)
        s = M.classify([a], model(a), cfg(), season())[0]
        self.assertEqual((s.kind, s.status), ("easy", "act"))

    def test_easy_run_without_samples_is_not_judged_on_share(self):
        a = act(minutes=40, avg_hr=135, ser=None)
        s = M.classify([a], model(a), cfg(), season())[0]
        self.assertEqual(s.status, "none")
        self.assertIn("no HR detail", s.notes[0])

    def test_long_run_is_longest_unstructured_run_over_an_hour(self):
        short = act(day=0, minutes=45)
        longer = act(day=5, minutes=95, avg_hr=150)
        ss = M.classify([short, longer], model(short, longer), cfg(), season())
        kinds = {s.act.id: s.kind for s in ss}
        self.assertEqual(kinds[longer.id], "long")
        self.assertEqual(kinds[short.id], "easy")
        self.assertEqual(next(s for s in ss if s.kind == "long").status, "act")      # over its cap is red

    def test_no_long_run_under_an_hour(self):
        a = act(minutes=50)
        self.assertEqual(M.classify([a], model(a), cfg(), season())[0].kind, "easy")

    def test_race_from_season_plan_even_when_untagged(self):
        a = act(day=6, km=21.1, minutes=85)
        sea = season(races=[S.Race(a.day, "Half", "HM")])
        self.assertEqual(M.classify([a], model(a), cfg(), sea)[0].kind, "race")

    def test_race_from_garmin_event_type_and_test_from_name(self):
        r = act(day=1, event="race")
        t = act(day=2, type="virtual_ride", name="FTP test")
        kinds = [s.kind for s in M.classify([r, t], model(r, t), cfg(), season())]
        self.assertEqual(kinds, ["race", "test"])

    def test_test_match_is_word_bounded(self):
        a = act(name="Fastest 10k")
        self.assertNotEqual(M.classify([a], model(a), cfg(), season())[0].kind, "test")

    def test_speed_session_closes_hard_session_gate(self):
        today = MON + timedelta(days=2)
        speed = subt_session(2, [180] * 4, rep_m=400, rep_pace=LT_PACE - 25)
        speed.start = datetime.combine(today, time(8))
        m = model(speed, nights=[night(today)], readiness=readiness(today))
        hs = M.hard_session(m, cfg(), season(), datetime.combine(today, time(12)))
        self.assertEqual(hs.state, "closed")


class Bike(unittest.TestCase):
    def test_discipline_flags(self):
        q = subt_session(1, [160])
        on_hard_day = act(day=1, hour=7, type="road_biking", km=30, minutes=60, avg_hr=120)
        long_run = act(day=6, minutes=100, avg_hr=140)
        day_before = act(day=5, type="road_biking", km=40, minutes=80, avg_hr=120)
        hot = act(day=3, type="road_biking", km=30, minutes=60, avg_hr=140)
        far = act(day=4, type="road_biking", km=75, minutes=150, avg_hr=120)
        acts = [q, on_hard_day, long_run, day_before, hot, far]
        rides = {s.act.id: s.flags for s in M.classify(acts, model(*acts), cfg(), season())
                 if s.kind == "ride"}
        self.assertIn("hard-run day", rides[on_hard_day.id][0])
        self.assertIn("before the long run", rides[day_before.id][0])
        self.assertIn("easy ride avg HR 140", rides[hot.id][0])
        self.assertIn("over 60 km", rides[far.id][0])

    def test_threshold_minutes_use_30s_smoothing(self):
        # 10 min at 95 % FTP, plus 1-s spikes that must not count
        ser = series((10, 150, 9.0, 237.5), (10, 120, 8.0, 150))
        ser["p"][-30] = 400
        self.assertAlmostEqual(M.threshold_minutes(ser, 250, 0.88), 10.0, delta=0.6)

    def test_running_share_uses_hilly_divisor(self):
        run = act(day=0, km=30, minutes=150)
        flat = act(day=1, type="road_biking", km=35, minutes=70, elev=100)    # 2.9 m/km -> /3.5
        hilly = act(day=2, type="road_biking", km=30, minutes=80, elev=600)   # 20 m/km -> /3.0
        wk = M.week_summary(MON, model(run, flat, hilly), cfg(), season(), SUN_NIGHT)
        self.assertAlmostEqual(wk.run_share.value, 30 / (30 + 10 + 10))
        self.assertEqual(wk.run_share.status, "act")

    def test_running_share_off_in_recovery_weeks(self):
        run = act(day=0, km=10, minutes=50)
        ride = act(day=1, type="road_biking", km=90, minutes=200)
        sea = season(phases=[S.Phase("Recovery", MON, MON + timedelta(days=6), recovery=True)])
        wk = M.week_summary(MON, model(run, ride), cfg(), sea, SUN_NIGHT)
        self.assertEqual(wk.run_share.status, "neutral")

    def test_no_ftp_means_no_threshold_minutes(self):
        ride = act(day=1, type="road_biking", km=30, minutes=60)
        wk = M.week_summary(MON, model(ride), cfg(), season(), SUN_NIGHT)
        self.assertEqual((wk.bike_thr_min.value, wk.bike_thr_min.note), (None, "no FTP set"))


class WeekFairness(unittest.TestCase):
    def sea(self, run_km=50.0):
        return season(phases=[S.Phase("Base", MON, MON + timedelta(days=6), run_km=run_km,
                                      threshold=(12, 16))])

    def test_short_of_target_midweek_is_not_flagged(self):
        a = act(day=0, km=10)
        wk = M.week_summary(MON, model(a), cfg(), self.sea(), WED_NOON)
        self.assertEqual((wk.run_km.status, wk.threshold.status), ("neutral", "neutral"))

    def test_short_of_target_at_week_end_is_amber(self):
        a = act(day=0, km=10)
        wk = M.week_summary(MON, model(a), cfg(), self.sea(), SUN_NIGHT)
        self.assertEqual((wk.run_km.status, wk.threshold.status), ("warn", "warn"))

    def test_over_target_flags_immediately(self):
        acts = [act(day=0, km=30, minutes=150), act(day=1, km=30, minutes=150)]
        wk = M.week_summary(MON, model(*acts), cfg(), self.sea(50), WED_NOON)
        self.assertEqual(wk.run_km.status, "warn")

    def test_run_days_flag_once_unreachable(self):
        # Friday evening, two run days, one run already today: 2 + 2 left < 5
        acts = [act(day=0), act(day=4, hour=7)]
        fri_eve = datetime.combine(MON + timedelta(days=4), time(20))
        wk = M.week_summary(MON, model(*acts), cfg(), self.sea(), fri_eve)
        self.assertEqual(wk.run_days.status, "warn")

    def test_no_target_means_nothing_to_judge_not_no_data(self):
        wk = M.week_summary(MON, model(act()), cfg(), season(), WED_NOON)
        self.assertEqual(wk.run_km.status, "neutral")      # no dot; grey is for missing data only


class Sleep(unittest.TestCase):
    def test_bedtime_mean_wraps_midnight(self):
        nights = [night(MON, "23:40"), night(MON + timedelta(days=1), "00:20")]
        mb = M.mean_bedtime(model(nights=nights), MON + timedelta(days=1))
        self.assertEqual(mb, 720)        # minutes after noon -> 00:00

    def test_solid_night_needs_both_score_and_hours(self):
        c = cfg()
        self.assertTrue(M.is_solid(night(MON, hours=6.5, score=70), c))
        self.assertFalse(M.is_solid(night(MON, hours=6.4, score=90), c))
        self.assertFalse(M.is_solid(night(MON, hours=8, score=69), c))

    def test_late_bedtime_flags(self):
        nights = [night(MON - timedelta(days=i), "00:45") for i in range(7)]
        late, _ = M.bedtime_late(model(nights=nights), cfg(), MON)
        self.assertTrue(late)


class HardSession(unittest.TestCase):
    def nights(self, day, ok=True):
        return [night(day, score=80 if ok else 50), night(day - timedelta(days=1))]

    def test_open_when_everything_passes(self):
        today = MON + timedelta(days=3)
        m = model(nights=self.nights(today), readiness=readiness(today))
        hs = M.hard_session(m, cfg(), season(), datetime.combine(today, time(9)))
        self.assertEqual(hs.state, "open")

    def test_closed_48h_after_over_cap_session_with_opening_time(self):
        today = MON + timedelta(days=3)
        q = subt_session(2, [175, 176], hour=18)
        m = model(q, nights=self.nights(today), readiness=readiness(today))
        hs = M.hard_session(m, cfg(), season(), datetime.combine(today, time(9)))
        self.assertEqual(hs.state, "closed")
        self.assertEqual(hs.opens, q.end + timedelta(hours=48))

    def test_last_night_bad_means_two_more_nights(self):
        today = MON + timedelta(days=3)
        m = model(nights=self.nights(today, ok=False), readiness=readiness(today))
        hs = M.hard_session(m, cfg(), season(), datetime.combine(today, time(9)))
        self.assertEqual(hs.opens.date(), today + timedelta(days=2))

    def test_recovery_time_over_24h_closes(self):
        today = MON + timedelta(days=3)
        m = model(nights=self.nights(today), readiness=readiness(today, recovery_h=30))
        hs = M.hard_session(m, cfg(), season(), datetime.combine(today, time(9)))
        self.assertEqual(hs.opens, datetime.combine(today, time(13)))

    def test_recovery_time_reopens_after_limit(self):
        today = MON + timedelta(days=3)
        m = model(nights=self.nights(today), readiness=readiness(today, recovery_h=30))
        hs = M.hard_session(m, cfg(), season(), datetime.combine(today, time(14)))
        self.assertEqual(hs.state, "open")

    def test_latest_reading_after_a_workout_counts(self):
        # morning said 8 h recovery; a long run at 14:00 pushed it to 40 h
        today = MON + timedelta(days=3)
        m = model(nights=self.nights(today), readiness=readiness(today, recovery_h=8),
                  readiness_now=readiness(today, level="LOW", score=30, recovery_h=40, at_hour=14))
        hs = M.hard_session(m, cfg(), season(), datetime.combine(today, time(18)))
        self.assertEqual(hs.state, "closed")
        self.assertEqual(hs.opens, datetime.combine(today + timedelta(days=1), time(6)))

    def test_poor_after_a_workout_closes_and_alerts(self):
        today = MON + timedelta(days=3)
        m = model(nights=self.nights(today), readiness=readiness(today),
                  readiness_now=readiness(today, level="POOR", score=12, recovery_h=10, at_hour=15))
        now = datetime.combine(today, time(18))
        self.assertEqual(M.hard_session(m, cfg(), season(), now).state, "closed")
        wk = M.week_summary(MON, m, cfg(), season(), now)
        self.assertTrue(any(a.what == "Readiness POOR" for a in M.alerts(wk, m, cfg(), now)))

    def test_missing_readiness_is_no_data_not_open(self):
        today = MON + timedelta(days=3)
        hs = M.hard_session(model(nights=self.nights(today)), cfg(), season(),
                            datetime.combine(today, time(9)))
        self.assertEqual(hs.state, "nodata")


class Load(unittest.TestCase):
    def test_ramp_alert_needs_two_weeks_above_band(self):
        today = MON + timedelta(days=13)
        both = [(today - timedelta(days=i), 1400, 1000) for i in range(14)]
        one = [(today - timedelta(days=i), 1400 if i < 7 else 1000, 1000) for i in range(14)]
        self.assertTrue(M.acwr_alert(model(load=both), cfg(), today)[0])
        self.assertFalse(M.acwr_alert(model(load=one), cfg(), today)[0])


class PaceAtHr(unittest.TestCase):
    def test_skips_first_minutes_and_uses_band_only(self):
        # first 10 min fast but out of band, then 20 min in band at 5:00/km
        ser = series((10, 150, 4.0, None), (20, 137, 1000 / 300, None))
        p = M.pace_at_hr(act(minutes=30, ser=ser), cfg())
        self.assertAlmostEqual(p.pace, 300, delta=1)
        self.assertTrue(p.valid)

    def test_warm_hilly_and_indoor_runs_are_grey(self):
        ser = series((30, 137, 3.3, None))
        warm = M.pace_at_hr(act(minutes=30, ser=ser, temp_c=24), cfg())
        hilly = M.pace_at_hr(act(minutes=30, km=10, elev=80, ser=ser), cfg())
        indoor = M.pace_at_hr(act(minutes=30, type="treadmill_running", ser=ser, temp_c=None), cfg())
        self.assertFalse(warm.valid or hilly.valid or indoor.valid)
        self.assertIn("warm 24 °C", warm.reasons)

    def test_run_within_24h_of_quality_is_excluded(self):
        # tired legs after intervals read as lost fitness
        ser = series((30, 137, 3.3, None))
        run = act(day=2, hour=17, minutes=30, ser=ser)
        quality_end = datetime.combine(MON + timedelta(days=1), time(19))   # 22 h before
        p = M.pace_at_hr(run, cfg(), [quality_end])
        self.assertFalse(p.valid)
        self.assertIn("< 24 h after quality", p.reasons)
        self.assertTrue(M.pace_at_hr(run, cfg(), [quality_end - timedelta(hours=3)]).valid)

    def test_too_little_time_in_band_gives_no_point(self):
        ser = series((12, 137, 3.3, None), (20, 125, 3.0, None))
        self.assertIsNone(M.pace_at_hr(act(minutes=32, ser=ser), cfg()))


class Alerts(unittest.TestCase):
    def test_every_alert_carries_an_action(self):
        q = subt_session(1, [175])
        ride = act(day=1, hour=7, type="road_biking", km=80, minutes=200, avg_hr=140)
        nights = [night(MON + timedelta(days=i), "01:30") for i in range(7)]
        m = model(q, ride, nights=nights)
        wk = M.week_summary(MON, m, cfg(), season(), SUN_NIGHT)
        out = M.alerts(wk, m, cfg(), SUN_NIGHT - timedelta(minutes=2))
        self.assertGreaterEqual(len(out), 4)
        for a in out:
            self.assertTrue(a.action, a.what)
        self.assertTrue(any("Cap breached" in a.what and "+48 h" in a.action for a in out))


class Review16Oct(unittest.TestCase):
    """Regressions for the items re-opened in the 16 Oct status review."""

    def nights(self, day):
        return [night(day), night(day - timedelta(days=1))]

    def test_subt_without_hr_still_starts_the_24h_window(self):
        today = MON + timedelta(days=3)
        q = subt_session(2, [None, None], hour=18)
        m = model(q, nights=self.nights(today), readiness=readiness(today))
        hs = M.hard_session(m, cfg(), season(), datetime.combine(today, time(9)))
        self.assertEqual(hs.state, "closed")
        self.assertEqual(hs.opens, q.end + timedelta(hours=24))
        self.assertIn("no HR", hs.checks[2].detail)

    def test_recovery_cell_alert_and_check_agree_after_the_window_opens(self):
        # read 28 h at 12:34; by 22:00 only 18.6 h are left
        today = MON + timedelta(days=3)
        r = readiness(today, recovery_h=28)
        r.at = datetime.combine(today, time(12, 34))
        m = model(nights=self.nights(today), readiness=r)
        now = datetime.combine(today, time(22))
        ts = M.today_state(m, cfg(), now)
        self.assertEqual(ts.recovery.status, "good")
        self.assertAlmostEqual(ts.recovery.value, 28 - (now - r.at).total_seconds() / 3600)
        self.assertIsNone(ts.recovery_opens)
        rec = next(c for c in M.hard_session(m, cfg(), season(), now).checks if c.label == "Recovery time")
        self.assertEqual(rec.state, "ok")
        wk = M.week_summary(MON, m, cfg(), season(), now)
        self.assertFalse(any(a.key == "recovery" for a in M.alerts(wk, m, cfg(), now)))

    def test_recovery_before_the_window_opens_alerts_with_the_same_time(self):
        today = MON + timedelta(days=3)
        r = readiness(today, recovery_h=28)
        r.at = datetime.combine(today, time(12, 34))
        m = model(nights=self.nights(today), readiness=r)
        now = datetime.combine(today, time(14))
        hs = M.hard_session(m, cfg(), season(), now)
        wk = M.week_summary(MON, m, cfg(), season(), now)
        alert = next(a for a in M.alerts(wk, m, cfg(), now) if a.key == "recovery")
        self.assertEqual(hs.opens, datetime.combine(today, time(16, 34)))
        self.assertIn("16:34", alert.what)

    def test_carryover_is_labelled_and_only_carries_missed_targets(self):
        sea = season(phases=[S.Phase("Base", MON, MON + timedelta(days=13), run_km=50.0)])
        acts = [act(day=0, km=10), subt_session(1, [175])]
        nights = [night(MON + timedelta(days=i), "01:30", hours=5, score=50) for i in range(9)]
        new_monday = datetime.combine(MON + timedelta(days=7), time(9))
        m = model(*acts, nights=nights, readiness=readiness(new_monday.date(), level="LOW"))
        last = M.week_summary(MON, m, cfg(), sea, new_monday)
        carried = M.carryover_alerts(last, cfg(), "Last week (W11)")
        self.assertTrue(carried)
        self.assertTrue(all(a.what.startswith("Last week (W11): ") for a in carried))
        self.assertIn("Run km 17 / 50", " ".join(a.what for a in carried))
        # nothing that today's own alerts already say, and no stale session alerts
        self.assertFalse(any(a.what.split(": ", 1)[1].startswith(("Readiness", "Sleep ", "Bedtime", "Cap"))
                             for a in carried))

    def test_pre_season_weeks_have_no_week_number(self):
        sea = season()
        self.assertIsNone(sea.week_number(sea.week1_start - timedelta(days=7)))
        self.assertEqual(sea.week_number(sea.week1_start), 1)
        self.assertEqual(sea.week_number(sea.week1_start + timedelta(days=13)), 2)

    def test_one_bad_easy_run_fails_the_week_even_when_pooled_passes(self):
        good = series((60, 130, 3.0, None))
        bad = series((20, 150, 3.0, None))          # all of it above the cap
        runs = [act(day=0, minutes=60, avg_hr=130, ser=good), act(day=1, minutes=60, avg_hr=130, ser=good),
                act(day=2, hour=7, minutes=20, avg_hr=150, ser=bad)]
        m = model(*runs)
        wk = M.week_summary(MON, m, cfg(), season(), WED_NOON)
        self.assertEqual(len(wk.easy_failed), 1)
        self.assertEqual(wk.easy_avg_hr.status, "good")       # pooled 135 passes on its own
        keys = [a.key for a in M.alerts(wk, m, cfg(), WED_NOON)]
        self.assertEqual(keys.count("easy"), 1)

    def test_shown_never_rounds_onto_the_limit(self):
        self.assertEqual(M.shown(140.4, 140), "140.4")
        self.assertEqual(M.shown(141, 140), "141")
        self.assertEqual(M.shown(0.096, 0.10, pct=True), "9.6%")
        self.assertEqual(M.shown(3.04, 3.0, 1), "3.04")

    def test_every_amber_or_red_verdict_has_an_alert(self):
        sea = season(phases=[S.Phase("Base", MON, MON + timedelta(days=6), run_km=50.0, threshold=(12, 16))])
        today = SUN_NIGHT.date()
        r = readiness(today, level="LOW", score=30, recovery_h=40)
        r.hrv_weekly, r.sleep_history_fb, r.sleep_history_pct = 40, "POOR", 30
        acts = [subt_session(1, [175]), act(day=2, hour=7, minutes=20, avg_hr=150, ser=series((20, 150, 3, None))),
                act(day=3, type="road_biking", km=90, minutes=240, avg_hr=140),
                act(day=4, minutes=90, km=15, avg_hr=150)]
        nights = [night(MON + timedelta(days=i), "01:30", hours=5, score=50) for i in range(8)]
        m = model(*acts, nights=nights, readiness=r)
        now = datetime.combine(today, time(8))
        wk = M.week_summary(MON, m, cfg(), sea, SUN_NIGHT)
        ts = M.today_state(m, cfg(), now)
        keys = {a.key for a in M.week_alerts(wk, cfg()) + M.day_alerts(ts, m, cfg(), now)}
        failing = {k for k, st in M.verdicts(wk, ts).items() if st in ("warn", "act")}
        self.assertGreaterEqual(len(failing), 10)
        self.assertEqual(failing - keys, set())


class CoachAnswers16Oct(unittest.TestCase):
    def sea(self, band):
        return season(phases=[S.Phase("P", MON, MON + timedelta(days=6), threshold=band)])

    def test_sub_t_in_a_no_quality_week_flags_at_once(self):
        m = model(subt_session(1, [150, 150]))
        wk = M.week_summary(MON, m, cfg(), self.sea((0, 0)), WED_NOON)
        self.assertEqual(wk.threshold.status, "warn")
        a = next(a for a in M.alerts(wk, m, cfg(), WED_NOON) if a.key == "threshold")
        self.assertIn("no sub-T planned", a.what)

    def test_single_figure_band_allows_the_run_km_tolerance(self):
        m = model(subt_session(1, [150] * 7))                      # 7 x 1600 = 11.2 km
        wk = M.week_summary(MON, m, cfg(), self.sea((10.5, 10.5)), SUN_NIGHT)
        self.assertEqual(wk.threshold.status, "good")
        self.assertEqual(M.band_text(10.5, 10.5), "10.5")
        self.assertEqual(M.band_text(12, 16), "12–16")


    def test_a_range_is_judged_as_written(self):
        m = model(subt_session(1, [150] * 7))                      # 11.2 km, band 12-16
        wk = M.week_summary(MON, m, cfg(), self.sea((12, 16)), SUN_NIGHT)
        self.assertEqual(wk.threshold.status, "warn")

    def test_recovery_text_never_rounds_onto_the_limit(self):
        today = MON + timedelta(days=3)
        r = readiness(today, recovery_h=30, at_hour=7)
        m = model(nights=[night(today), night(today - timedelta(days=1))], readiness=r)
        now = datetime.combine(today, time(12, 40))                     # 24.33 h left
        rec = next(c for c in M.hard_session(m, cfg(), season(), now).checks if c.label == "Recovery time")
        self.assertEqual(rec.state, "fail")
        self.assertTrue(rec.detail.startswith("24.3 h left"), rec.detail)


class PageVerdicts(unittest.TestCase):
    """End to end: every amber or red cell on the built home page has an alert
    with the same verdict key. Reads the HTML, so a cell added later without an
    alert fails here even if verdicts() forgets it."""

    def test_every_amber_or_red_cell_on_the_page_has_an_alert(self):
        import re
        import build_dashboard as B
        sea = season(phases=[S.Phase("Base", MON, MON + timedelta(days=6), run_km=50.0, threshold=(12, 16))])
        today = MON + timedelta(days=6)
        r = readiness(today, level="LOW", score=30, recovery_h=40)
        r.hrv_weekly, r.sleep_history_fb, r.sleep_history_pct = 40, "POOR", 30
        acts = [subt_session(1, [175]), act(day=2, hour=7, minutes=20, avg_hr=150, ser=series((20, 150, 3, None))),
                act(day=3, type="road_biking", km=90, minutes=240, avg_hr=140),
                act(day=4, minutes=90, km=15, avg_hr=150)]
        nights = [night(MON + timedelta(days=i), "01:30", hours=5, score=50) for i in range(-14, 8)]
        load = [(today - timedelta(days=i), 1500, 1000) for i in range(14)]
        m = model(*acts, nights=nights, readiness=r, load=load)
        html = B.home(m, cfg(), sea, datetime.combine(today, time(23, 30)))
        failing = set(re.findall(r'class="[^"]*\bst-(?:warn|act)\b[^"]*" data-v="(\w+)"', html))
        alerted = set(re.findall(r'data-k="(\w+)"', html))
        self.assertGreaterEqual(len(failing), 10, failing)
        self.assertEqual(failing - alerted, set())


class RecoveryAcrossMidnight(unittest.TestCase):
    def test_yesterdays_countdown_counts_before_the_first_reading(self):
        today = MON + timedelta(days=3)
        y = readiness(today - timedelta(days=1), recovery_h=40, at_hour=18)
        m = model(nights=[night(today), night(today - timedelta(days=1))], readiness=y)
        now = datetime.combine(today, time(6))                   # no reading yet today
        ts = M.today_state(m, cfg(), now)
        self.assertIsNone(ts.reading)
        self.assertAlmostEqual(ts.recovery.value, 28)
        self.assertEqual(ts.recovery.status, "warn")
        self.assertIn("at " + f"{y.at:%a} 18:00", ts.recovery.note)
        rec = next(c for c in M.hard_session(m, cfg(), season(), now, ts).checks if c.label == "Recovery time")
        self.assertEqual(rec.state, "fail")


class TolerancesAndOverrides(unittest.TestCase):
    """Coach-set slack around caps and targets, and the coach's word on a session."""

    def test_rep_just_over_the_cap_counts_inside_the_tolerance(self):
        q = subt_session(1, [164, 167, 169])                       # cap 165 for 1600 m reps
        c = cfg(threshold={"cap_tolerance_bpm": 3})
        s = M.classify([q], model(q), c, season())[0]
        self.assertEqual([r.within for r in s.reps], [True, True, False])
        self.assertEqual([r.near for r in s.reps], [False, True, False])
        self.assertAlmostEqual(s.within_km, 3.2)
        self.assertEqual(s.status, "act")                          # 169 is past cap + 3
        q2 = subt_session(1, [164, 167])
        s2 = M.classify([q2], model(q2), c, season())[0]
        self.assertEqual(s2.status, "warn")
        wk = M.week_summary(MON, model(q2), c, season(), SUN_NIGHT)
        alerts = M.alerts(wk, model(q2), c, SUN_NIGHT)
        self.assertTrue(any(a.key == "near_cap" and "inside the tolerance" in a.what for a in alerts))
        self.assertFalse(any(a.key == "over_cap" for a in alerts))

    def test_threshold_tolerance_widens_a_range(self):
        sea = season(phases=[S.Phase("P", MON, MON + timedelta(days=6), threshold=(12, 16))])
        m = model(subt_session(1, [150] * 7))                      # 11.2 km
        self.assertEqual(M.week_summary(MON, m, cfg(), sea, SUN_NIGHT).threshold.status, "warn")
        loose = cfg(runs={"threshold_tolerance": 0.1})             # 12 x 0.9 = 10.8
        self.assertEqual(M.week_summary(MON, m, loose, sea, SUN_NIGHT).threshold.status, "good")

    def test_coach_reclassifies_a_planned_fast_session(self):
        q = subt_session(3, [177, 181], hour=18)
        sea = season(sessions=[S.SessionOverride(q.day, kind="speed", note="planned: fast session")])
        s = M.classify([q], model(q), cfg(), sea)[0]
        self.assertEqual((s.kind, s.over_km, s.within_km, s.status), ("speed", 0.0, 0.0, "good"))
        self.assertEqual(s.coach, "planned: fast session")
        wk = M.week_summary(MON, model(q), cfg(), sea, SUN_NIGHT)
        self.assertFalse(any(a.key == "over_cap" for a in M.alerts(wk, model(q), cfg(), SUN_NIGHT)))
        # still a hard session: 48 h before the next one
        today = MON + timedelta(days=4)
        m = model(q, nights=[night(today), night(today - timedelta(days=1))], readiness=readiness(today))
        hs = M.hard_session(m, cfg(), sea, datetime.combine(today, time(9)))
        self.assertEqual(hs.opens, q.end + timedelta(hours=48))

    def test_accepted_verdict_is_green_but_still_hard(self):
        q = subt_session(1, [168, 170], hour=18)
        sea = season(sessions=[S.SessionOverride(q.day, accept=True, note="close enough")])
        s = M.classify([q], model(q), cfg(), sea)[0]
        self.assertEqual((s.kind, s.status, s.over_km, s.accepted, s.breached), ("subt", "good", 0.0, True, True))
        today = MON + timedelta(days=2)
        m = model(q, nights=[night(today), night(today - timedelta(days=1))], readiness=readiness(today))
        hs = M.hard_session(m, cfg(), sea, datetime.combine(today, time(9)))
        self.assertEqual(hs.opens, q.end + timedelta(hours=48))


class ThreeLevelBands(unittest.TestCase):
    """v1.2.0: green / amber / red around caps, weekly targets, easy runs, sleep."""

    def test_band_verdict_levels(self):
        bv = lambda v, done=True: M.band_verdict(v, 50, 50, 0.05, 0.15, 0.10, done)
        self.assertEqual([bv(50), bv(48), bv(44), bv(42), bv(54), bv(56)],
                         ["good", "good", "warn", "act", "warn", "act"])
        self.assertEqual(bv(42, done=False), "neutral")              # shortfall waits for the week end
        self.assertEqual(M.band_verdict(4.8, 6.4, 6.4, 0.1, 0.25, 0.15, True), "warn")   # on the edge
        self.assertEqual(M.band_verdict(1.0, 0, 0, 0.1, 0.25, 0.15, False), "act")      # no sub-T planned
        self.assertEqual(M.band_verdict(42, 50, 50, 0.1, 0, 0, True), "warn")           # no zone: as before

    def test_cap_tolerance_by_rep_length(self):
        long_ = subt_session(1, [166, 167, 168])                      # cap 165, long reps
        c = cfg(threshold={"cap_tolerance_long_bpm": 2, "cap_tolerance_short_bpm": 0})
        s = M.classify([long_], model(long_), c, season())[0]
        self.assertEqual([(r.within, r.near) for r in s.reps], [(True, True), (True, True), (False, False)])
        self.assertAlmostEqual((s.within_km, s.over_km), (3.2, 1.6))
        short = subt_session(1, [171], rep_m=1000)                    # cap 170, short reps: no leeway
        self.assertFalse(M.classify([short], model(short), c, season())[0].reps[0].within)

    def test_speed_reps_judged_on_the_pace_band(self):
        q = subt_session(3, [176, 180], rep_m=1000, rep_pace=215)     # 3:35/km
        q2 = subt_session(3, [176], rep_m=1000, rep_pace=208)         # 3:28/km: 7 s too fast
        for act_, want in ((q, "good"), (q2, "act")):
            sea = season(sessions=[S.SessionOverride(act_.day, kind="speed", pace=(215.0, 222.0))])
            s = M.classify([act_], model(act_), cfg(), sea)[0]
            self.assertEqual(s.status, want)
        wk = M.week_summary(MON, model(q2), cfg(), season(sessions=[
            S.SessionOverride(q2.day, kind="speed", pace=(215.0, 222.0))]), SUN_NIGHT)
        self.assertTrue(any(a.key == "pace" and "3:28" in a.what for a in M.week_alerts(wk, cfg())))

    def test_easy_run_amber_zone(self):
        ser = series((45, 130, 3.0, None), (5, 150, 3.0, None))       # 10 % above the cap
        ser7 = series((47, 130, 3.0, None), (3, 150, 3.0, None))      # 6 %
        c = cfg(easy={"share_green": 0.05, "max_share_above": 0.10})  # 50 min: easy, not the long run
        self.assertEqual(M.classify([act(minutes=50, avg_hr=133, ser=ser)], model(), c, season())[0].status, "act")
        run = act(minutes=50, avg_hr=132, ser=ser7)
        self.assertEqual(M.classify([run], model(run), c, season())[0].status, "warn")
        wk = M.week_summary(MON, model(run), c, season(), SUN_NIGHT)
        self.assertEqual(wk.easy.status, "warn")
        self.assertTrue(any(a.key == "easy" and a.level == "warn" for a in M.week_alerts(wk, c)))

    def test_amber_nights_and_the_pre_quality_gate(self):
        c = cfg(sleep={"solid_score": 75, "solid_hours": 7.0, "amber_score": 70, "amber_hours": 6.5},
                hard_session={"red_night_ok_readiness": "MODERATE"})
        self.assertEqual([M.night_level(night(MON, hours=7.5, score=80), c),
                          M.night_level(night(MON, hours=6.8, score=72), c),
                          M.night_level(night(MON, hours=6.0, score=80), c)], ["good", "warn", "act"])
        today = MON + timedelta(days=3)
        now = datetime.combine(today, time(9))
        gate = lambda nights, level: next(
            ch for ch in M.hard_session(model(nights=nights, readiness=readiness(today, level=level)),
                                        c, season(), now).checks if ch.label == "Two solid nights")
        amber = [night(today, hours=6.8, score=72), night(today - timedelta(days=1), hours=6.8, score=72)]
        self.assertEqual(gate(amber, "HIGH").state, "ok")
        one_red = [night(today, hours=5, score=50), night(today - timedelta(days=1))]
        self.assertEqual(gate(one_red, "MODERATE").state, "ok")
        self.assertEqual(gate(one_red, "LOW").state, "fail")
        two_red = [night(today, hours=5, score=50), night(today - timedelta(days=1), hours=5, score=50)]
        self.assertEqual(gate(two_red, "HIGH").state, "fail")

    def test_solid_nights_week_reads_amber_on_solid_or_amber_nights(self):
        c = cfg(sleep={"solid_score": 75, "solid_hours": 7.0, "amber_score": 70, "amber_hours": 6.5,
                       "solid_nights_target": 4, "ok_nights_target": 5})
        nights = ([night(MON + timedelta(days=i), hours=7.5, score=80) for i in range(3)]
                  + [night(MON + timedelta(days=i), hours=6.8, score=72) for i in range(3, 5)]
                  + [night(MON + timedelta(days=i), hours=5, score=50) for i in range(5, 7)])
        wk = M.week_summary(MON, model(nights=nights), c, season(), SUN_NIGHT)
        self.assertEqual((wk.solid_nights.value, wk.solid_nights.status), (3, "warn"))


class ConfigNotTracked(unittest.TestCase):
    """Values from the personal, gitignored configs must not appear in tracked files."""

    def test_personal_season_values_are_not_in_git(self):
        import re
        import subprocess
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        conf = S.PRIVATE / "config.toml"
        if not conf.exists():
            self.skipTest("no personal private/config.toml")
        try:
            files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                                   check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        sea = S.load_season(conf)
        needles = set()
        for r in sea.races:
            d = r.date
            # the ways a date gets written in prose, not just ISO
            needles |= {d.isoformat(), f"{d.day} {d:%b} {d.year}".lower(), f"{d.day} {d:%B} {d.year}".lower(),
                        f"{d:%d.%m.%Y}", f"{d.day}.{d.month}.{d.year}"}
            for word in (r.name, r.short):
                if len(word) >= 4:
                    needles.add(word.lower())
        ath = S.load_athlete(conf)
        # personal values that are distinctive enough to search for
        rc = ath["readiness"]
        needles |= {f"{rc['hrv_low']}–{rc['hrv_high']}", f"{rc['hrv_low']}-{rc['hrv_high']}"}
        if sea.week1_start:
            w = sea.week1_start
            needles |= {w.isoformat(), f"{w.day} {w:%b} {w.year}".lower()}
        if ath["bike"].get("ftp_w"):
            needles.add(f"ftp_w = {ath['bike']['ftp_w']}")
        hits = []
        for f in files:
            if f.endswith("example.toml"):
                continue
            try:
                text = (root / f).read_text().lower()
            except (OSError, UnicodeDecodeError):
                continue
            hits += [f"{f}: {n}" for n in needles if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text)]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
