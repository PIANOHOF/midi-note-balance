from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
from io import BytesIO, StringIO
from pathlib import Path
import random
import tempfile
import unittest

import mido
import midi_note_balance as tool


def smf(*tracks, kind=None, ppqn=480):
    mid = mido.MidiFile(type=(0 if len(tracks) == 1 else 1) if kind is None else kind,
                        ticks_per_beat=ppqn)
    mid.tracks.extend(mido.MidiTrack(track) for track in tracks)
    out = BytesIO()
    mid.save(file=out)
    return out.getvalue()


def on(note=60, channel=0, time=0, velocity=80):
    return mido.Message("note_on", note=note, channel=channel, time=time, velocity=velocity)


def off(note=60, channel=0, time=0):
    return mido.Message("note_off", note=note, channel=channel, time=time)


class LedgerTests(unittest.TestCase):
    def test_balanced_and_zero_velocity(self):
        r = tool.analyze_bytes(smf([on(), on(time=480, velocity=0)]))
        self.assertEqual((r["matched_explicit_pairs"], r["open_note_on_count"],
                          r["zero_velocity_note_on_events"]), (1, 0, 1))

    def test_missing_off_and_extra_off(self):
        r = tool.analyze_bytes(smf([off(61), on(60, time=5), off(62, time=3)]))
        self.assertEqual(r["unmatched_note_off_count"], 2)
        self.assertEqual(r["open_note_ons"], [{"port": "default", "channel": 1,
                         "note": 60, "tick": 5, "track": 1, "event": 2, "velocity": 80}])

    def test_note_extremes_and_channel_separation(self):
        r = tool.analyze_bytes(smf([on(0), off(0, channel=1), on(127, channel=15), off(127, channel=15)]))
        self.assertEqual((r["open_note_on_count"], r["unmatched_note_off_count"],
                          r["matched_explicit_pairs"]), (1, 1, 1))

    def test_overlap_fifo_keeps_later_start(self):
        r = tool.analyze_bytes(smf([on(time=2), on(time=3), off(time=4)]))
        self.assertEqual(r["overlap_count"], 1)
        self.assertEqual(r["open_note_ons"][0]["tick"], 5)

    def test_cross_track_pairing(self):
        r = tool.analyze_bytes(smf([on()], [off(time=480)]))
        self.assertEqual(r["matched_explicit_pairs"], 1)
        self.assertEqual(r["open_note_on_count"], 0)

    def test_ambiguous_same_tick_does_not_invent_balance(self):
        r = tool.analyze_bytes(smf([on(time=480)], [off(time=480)]))
        self.assertFalse(r["balance_available"])
        self.assertIsNone(r["open_note_on_count"])
        self.assertIsNone(r["matched_explicit_pairs"])
        self.assertEqual(r["ambiguous_order_count"], 1)
        self.assertIn("Keine belastbare Paarbilanz", tool.as_text(r))

    def test_same_track_zero_length_note_order_is_known(self):
        r = tool.analyze_bytes(smf([on(), off()]))
        self.assertTrue(r["balance_available"])
        self.assertEqual(r["matched_explicit_pairs"], 1)

    def test_same_tick_different_note_or_channel_is_not_ambiguous(self):
        r = tool.analyze_bytes(smf([on()], [off(61), off(channel=1)]))
        self.assertTrue(r["balance_available"])

    def test_logical_ports_are_separate_and_track_local(self):
        p = lambda n: mido.MetaMessage("midi_port", port=n)
        r = tool.analyze_bytes(smf([p(2), on(), p(3), off(time=10)], [p(2), off(time=5)]))
        self.assertEqual((r["matched_explicit_pairs"], r["unmatched_note_off_count"]), (1, 1))
        self.assertEqual(r["unmatched_note_offs"][0]["port"], 3)

    def test_unspecified_port_not_silently_mapped_to_zero(self):
        r = tool.analyze_bytes(smf([on()], [mido.MetaMessage("midi_port", port=0), off(time=5)]))
        self.assertEqual((r["open_note_on_count"], r["unmatched_note_off_count"]), (1, 1))

    def test_mode_commands_do_not_fake_individual_note_offs(self):
        rows = [on()] + [mido.Message("control_change", control=c, value=0, time=1)
                         for c in tool.MODE_NAMES]
        r = tool.analyze_bytes(smf(rows))
        self.assertEqual(r["open_note_on_count"], 1)
        self.assertEqual(r["channel_mode_event_count"], 7)
        self.assertEqual(r["matched_explicit_pairs"], 0)

    def test_cc64_is_observation_not_voice_simulation(self):
        r = tool.analyze_bytes(smf([on(), mido.Message("control_change", control=64, value=127), off(time=4)]))
        self.assertEqual(r["open_note_on_count"], 0)
        self.assertEqual(r["last_stored_cc64_values"][0]["last_stored_value"], 127)

    def test_metadata_and_sysex_are_not_executed_or_copied(self):
        r = tool.analyze_bytes(smf([mido.MetaMessage("lyrics", text="PRIVATE LYRICS"),
            mido.Message("sysex", data=[1, 2, 3]), on(), off(time=5)]))
        self.assertNotIn("PRIVATE LYRICS", str(r))
        self.assertNotIn("sysex", str(r).lower().replace("sysex execution", ""))

    def test_empty_valid_track(self):
        r = tool.analyze_bytes(smf([]))
        self.assertEqual((r["duration_ticks"], r["open_note_on_count"]), (0, 0))

    def test_unsupported_and_invalid_inputs(self):
        for data in [b"RIFFxx", b"MThd", smf([], kind=2), smf([], ppqn=-25),
                     b"MThd" + bytes(tool.MAX_BYTES)]:
            with self.subTest(length=len(data)):
                with self.assertRaises(ValueError):
                    tool.analyze_bytes(data)

    def test_row_limit_preserves_totals(self):
        r = tool.analyze_bytes(smf([on(note=i % 128, time=1) for i in range(2005)]))
        self.assertEqual(r["open_note_on_count"], 2005)
        self.assertEqual(len(r["open_note_ons"]), 2000)
        self.assertTrue(r["lists_truncated"])

    def test_seeded_streams_against_independent_counting_oracle(self):
        rng = random.Random(92614)
        for trial in range(100):
            balance, extra, matched = Counter(), 0, 0
            messages = []
            for i in range(120):
                key = (rng.randrange(3), rng.randrange(60, 66))
                if rng.randrange(2):
                    balance[key] += 1
                    messages.append(on(key[1], key[0], time=1))
                else:
                    if balance[key]:
                        balance[key] -= 1
                        matched += 1
                    else:
                        extra += 1
                    messages.append(off(key[1], key[0], time=1))
            r = tool.analyze_bytes(smf(messages))
            self.assertEqual((r["open_note_on_count"], r["unmatched_note_off_count"],
                              r["matched_explicit_pairs"]), (sum(balance.values()), extra, matched))

    def test_pedal_row_limit_includes_default_and_explicit_ports(self):
        rows = [mido.Message("control_change", channel=ch, control=64, value=127)
                for ch in range(16)]
        for port in range(128):
            rows.append(mido.MetaMessage("midi_port", port=port))
            rows.extend(mido.Message("control_change", channel=ch, control=64, value=127)
                        for ch in range(16))
        r = tool.analyze_bytes(smf(rows))
        self.assertEqual(r["last_stored_cc64_value_count"], 2064)
        self.assertEqual(len(r["last_stored_cc64_values"]), 2000)
        self.assertTrue(r["lists_truncated"])

    def test_cli_json_and_input_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "example.mid"
            original = smf([on(), off(time=480)])
            path.write_bytes(original)
            out = StringIO()
            with redirect_stdout(out):
                code = tool.main([str(path), "--format", "json"])
            self.assertEqual(code, 0)
            self.assertIn('"open_note_on_count": 0', out.getvalue())
            self.assertEqual(path.read_bytes(), original)
            path.write_bytes(b"not MIDI")
            with redirect_stderr(StringIO()):
                self.assertEqual(tool.main([str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
