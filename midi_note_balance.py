"""Explicit Note On/Off ledger for SMF 0/1. Read only; no receiver simulation."""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from io import BytesIO
import json
from pathlib import Path
import struct
import sys

import mido

MAX_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 250_000
MAX_ROWS = 2_000
MODE_NAMES = {120: "All Sound Off", 121: "Reset All Controllers",
              123: "All Notes Off", 124: "Omni Mode Off",
              125: "Omni Mode On", 126: "Mono Mode On", 127: "Poly Mode On"}


def location(port, msg, tick, track, event):
    return {"port": port, "channel": msg.channel + 1, "note": msg.note,
            "tick": tick, "track": track, "event": event}


def analyze_bytes(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("Mehr als 8 MiB; bitte einen kleineren SMF-Export verwenden.")
    if not data.startswith(b"MThd"):
        raise ValueError("Erwartet wird Standard MIDI (MThd), kein Audio, RMID, SYX oder UMP.")
    try:
        mid = mido.MidiFile(file=BytesIO(data), clip=False)
    except (EOFError, OSError, ValueError, TypeError, struct.error) as exc:
        raise ValueError(f"SMF nicht lesbar: {exc}") from exc
    if mid.type not in (0, 1) or mid.ticks_per_beat <= 0:
        raise ValueError("Nur synchrone SMF 0/1 mit positiver PPQN-Zeitteilung; kein Typ 2/SMPTE.")
    if not mid.tracks or (mid.type == 0 and len(mid.tracks) != 1):
        raise ValueError("Ungueltige Spuranzahl.")
    if sum(map(len, mid.tracks)) > MAX_EVENTS:
        raise ValueError("Mehr als 250000 Ereignisse; bitte einen kleineren Export verwenden.")

    events, ports, mode_rows, pedal = [], set(), [], {}
    duration = 0
    for ti, track in enumerate(mid.tracks, 1):
        tick, port = 0, "default"
        for ei, msg in enumerate(track, 1):
            if not isinstance(msg.time, int) or msg.time < 0:
                raise ValueError("Ereigniszeit muss eine nichtnegative ganze Tickzahl sein.")
            tick += msg.time
            if msg.type == "midi_port":
                port = msg.port
            elif msg.type in ("note_on", "note_off", "control_change"):
                ports.add(port)
                events.append((tick, ti, ei, port, msg))
        duration = max(duration, tick)
    events.sort(key=lambda row: row[:3])

    # Different tracks have no universal same-tick serialization order. Refuse
    # a balance when opposite note actions at that tick could change it.
    simultaneous = defaultdict(lambda: {"on": set(), "off": set()})
    for tick, ti, ei, port, msg in events:
        if msg.type in ("note_on", "note_off"):
            action = "on" if msg.type == "note_on" and msg.velocity > 0 else "off"
            simultaneous[(tick, port, msg.channel, msg.note)][action].add(ti)
    ambiguous = []
    for (tick, port, ch, note), actions in simultaneous.items():
        if any(a != b for a in actions["on"] for b in actions["off"]):
            ambiguous.append({"tick": tick, "port": port, "channel": ch + 1,
                              "note": note, "on_tracks": sorted(actions["on"]),
                              "off_tracks": sorted(actions["off"])})

    queues = defaultdict(deque)
    unmatched_off, overlaps = [], []
    on_count = off_count = zero_off_count = matched_count = 0
    unmatched_off_count = overlap_count = mode_count = 0
    for tick, ti, ei, port, msg in events:
        if msg.type == "control_change":
            if msg.control in MODE_NAMES:
                mode_count += 1
                if len(mode_rows) < MAX_ROWS:
                    mode_rows.append({"port": port, "channel": msg.channel + 1,
                                      "tick": tick, "track": ti, "event": ei,
                                      "controller": msg.control, "value": msg.value,
                                      "name": MODE_NAMES[msg.control]})
            elif msg.control == 64:
                pedal[(port, msg.channel)] = {"port": port, "channel": msg.channel + 1,
                                              "last_stored_value": msg.value,
                                              "tick": tick, "track": ti, "event": ei}
            continue
        row = location(port, msg, tick, ti, ei)
        key = (port, msg.channel, msg.note)
        if msg.type == "note_on" and msg.velocity > 0:
            on_count += 1
            if queues[key]:
                overlap_count += 1
                if len(overlaps) < MAX_ROWS:
                    overlaps.append({**row, "already_open": len(queues[key])})
            queues[key].append({**row, "velocity": msg.velocity})
        else:
            off_count += 1
            zero_off_count += int(msg.type == "note_on")
            if queues[key]:
                queues[key].popleft()
                matched_count += 1
            else:
                unmatched_off_count += 1
                if len(unmatched_off) < MAX_ROWS:
                    unmatched_off.append(row)
    open_rows = [row for queue in queues.values() for row in queue]
    open_rows.sort(key=lambda row: (row["tick"], row["track"], row["event"]))
    complete = not ambiguous
    return {
        "tool": "MIDI Note Balance", "schema_version": 1,
        "analysis_status": "explicit_event_ledger" if complete else "ambiguous_cross_track_order",
        "balance_available": complete, "smf_type": mid.type,
        "track_count": len(mid.tracks), "ppqn": mid.ticks_per_beat,
        "duration_ticks": duration, "note_on_events": on_count,
        "note_off_events_including_zero_velocity": off_count,
        "zero_velocity_note_on_events": zero_off_count,
        "matched_explicit_pairs": matched_count if complete else None,
        "open_note_on_count": len(open_rows) if complete else None,
        "open_note_ons": open_rows[:MAX_ROWS] if complete else [],
        "unmatched_note_off_count": unmatched_off_count if complete else None,
        "unmatched_note_offs": unmatched_off if complete else [],
        "overlap_count": overlap_count if complete else None,
        "overlapping_note_ons": overlaps if complete else [],
        "ambiguous_order_count": len(ambiguous),
        "ambiguous_order_examples": ambiguous[:MAX_ROWS],
        "channel_mode_event_count": mode_count, "channel_mode_events": mode_rows,
        "last_stored_cc64_value_count": len(pedal),
        "last_stored_cc64_values": list(pedal.values())[:MAX_ROWS],
        "logical_ports": sorted(ports, key=lambda value: str(value)),
        "row_limit_per_list": MAX_ROWS,
        "lists_truncated": (any(n > MAX_ROWS for n in [len(ambiguous), mode_count, len(pedal)])
                            or (complete and any(n > MAX_ROWS for n in
                                [len(open_rows), unmatched_off_count, overlap_count]))),
        "rules": [
            "Explicit Note On/Off pairing only; Note On velocity zero counts as Off.",
            "FIFO for repeated same-note Note Ons; overlaps are reported, not declared invalid.",
            "Channel-mode commands and sustain are listed but do not replace explicit Note Offs in this ledger.",
            "The default route is separate from each explicit MIDI Port identifier; actual DAW routing is unknown.",
            "Ticks are stored positions, not seconds; no sounding-voice, release-tail or hardware diagnosis.",
            "No MIDI outputs, audio, SysEx execution, networking or source-file changes.",
        ],
    }


def as_text(r: dict) -> str:
    lines = [r["tool"], f"SMF {r['smf_type']}; Spuren {r['track_count']}; PPQN {r['ppqn']}",
             f"Status: {r['analysis_status']}; Ende: Tick {r['duration_ticks']}",
             f"Note On: {r['note_on_events']}; Note Off inkl. Nullvelocity: "
             f"{r['note_off_events_including_zero_velocity']}"]
    if r["balance_available"]:
        lines += [f"Explizite Paare: {r['matched_explicit_pairs']}; offene Note Ons: {r['open_note_on_count']}",
                  f"Off ohne vorheriges On: {r['unmatched_note_off_count']}; ueberlappende Ons: {r['overlap_count']}",
                  "Offene Note Ons: Port | Kanal | MIDI-Notennummer | Tick | Spur | Ereignis"]
        lines += [f"{x['port']} | {x['channel']} | {x['note']} | {x['tick']} | {x['track']} | {x['event']}"
                  for x in r["open_note_ons"]]
    else:
        lines.append("Keine belastbare Paarbilanz: gegenlaeufige Ereignisse auf verschiedenen Spuren beim selben Tick.")
    lines += [f"Channel-Mode-Befehle: {r['channel_mode_event_count']}; sie schliessen diese explizite Bilanz nicht.",
              "Details zu Offs, Ueberlappungen, Reihenfolge, CC64 und Mode-Befehlen: --format json."]
    if r["lists_truncated"]:
        lines.append(f"Detail-Listen auf jeweils {MAX_ROWS} Zeilen begrenzt; Gesamtzahlen bleiben vollstaendig.")
    lines += r["rules"]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explizite Note-On/Off-Bilanz einer SMF-Datei, nur lesend.")
    parser.add_argument("midi_file", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        with args.midi_file.open("rb") as source:
            report = analyze_bytes(source.read(MAX_BYTES + 1))
    except (OSError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
          if args.format == "json" else as_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
