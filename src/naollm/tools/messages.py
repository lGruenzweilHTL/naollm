"""Short, child-friendly reasons and lines in German and English."""

from __future__ import annotations

from typing import Any, Dict

Lang = str  # "de" | "en"

MESSAGES: Dict[str, Dict[str, str]] = {
    "unknown_tool": {"de": "Das kann ich nicht: {name}.", "en": "I can't do that: {name}."},
    "bad_json": {"de": "Das habe ich nicht verstanden.", "en": "I didn't understand that."},
    "missing_arg": {"de": "Mir fehlt eine Angabe: {param}.", "en": "Something is missing: {param}."},
    "extra_arg": {"de": "Das kenne ich nicht: {param}.", "en": "I don't know that: {param}."},
    "bad_choice": {"de": "Das kenne ich nicht: {value}.", "en": "I don't know that one: {value}."},
    "not_integer": {"de": "{param} muss eine ganze Zahl sein.", "en": "{param} must be a whole number."},
    "too_far_step": {"de": "Ich kann höchstens {max} Zentimeter auf einmal gehen.",
                     "en": "I can walk at most {max} centimetres at once."},
    "too_short_step": {"de": "Ich gehe mindestens {min} Zentimeter.", "en": "I walk at least {min} centimetres."},
    "too_far_turn": {"de": "Ich kann mich höchstens um {max} Grad drehen.",
                     "en": "I can turn at most {max} degrees."},
    "out_of_range": {"de": "{param} muss zwischen {min} und {max} liegen.",
                     "en": "{param} must be between {min} and {max}."},
    "already_moving": {"de": "Ich bewege mich gerade schon.", "en": "I'm already moving."},
    "stopped": {"de": "Ich wurde angehalten. Ein Betreuer muss mich erst freigeben.",
                "en": "I've been stopped. A helper has to release me first."},
    "fallen": {"de": "Ich bin umgefallen. Bitte hilf mir auf.", "en": "I fell over. Please help me up."},
    "resting": {"de": "Ich ruhe mich gerade aus.", "en": "I'm resting right now."},
    "offline": {"de": "Ich bin gerade nicht verbunden.", "en": "I'm not connected right now."},
    "need_stand": {"de": "Dafür muss ich erst aufstehen.", "en": "I need to stand up first."},
    "need_posture": {"de": "Dafür muss ich erst in die Haltung {posture}.",
                     "en": "I need to be in the {posture} posture first."},
    "motors_off": {"de": "Ich kann mich gerade nicht bewegen.", "en": "I can't move right now."},
    "perception_off": {"de": "Ich kann gerade nicht sehen, wer da ist.",
                       "en": "I can't see who is here right now."},
    "walking_off": {"de": "Laufen ist gerade ausgeschaltet.", "en": "Walking is switched off right now."},
    "battery_low": {"de": "Mein Akku ist zu leer für Bewegungen.", "en": "My battery is too low to move."},
    "battery_dance": {"de": "Mein Akku ist zu leer zum Tanzen.", "en": "My battery is too low to dance."},
    "too_warm": {"de": "Mir ist zu warm dafür. Ich brauche eine Pause.",
                 "en": "I'm too warm for that. I need a break."},
    "too_hot": {"de": "Mir ist viel zu heiß. Ich muss mich ausruhen.", "en": "I'm far too hot. I need to rest."},
    "distance_budget": {"de": "Ich darf nicht weiter als {max} Meter laufen.",
                        "en": "I'm not allowed to walk further than {max} metres."},
    "reserved_colour": {"de": "Diese Augenfarbe kann ich nicht nehmen: {value}.",
                        "en": "I can't use that eye colour: {value}."},
    "robot_error": {"de": "Das hat leider nicht geklappt.", "en": "Sorry, that didn't work."},
    "fallback": {"de": "Da bin ich durcheinander gekommen.", "en": "I got a bit confused there."},
    "apology": {"de": "Entschuldigung, mir fällt gerade keine Antwort ein.",
                "en": "Sorry, I can't think of an answer right now."},
    "ready": {"de": "Ich bin bereit.", "en": "I'm ready."},
    "enrol_look": {"de": "Bitte schau mich an.", "en": "Please look at me."},
    "enrol_hint": {"de": "Schau mich bitte direkt an und halte still.",
                   "en": "Please look straight at me and hold still."},
    "enrol_done": {"de": "Hallo, {name}.", "en": "Hello, {name}."},
    "notice": {"de": "Sag {wake} oder tippe mir vorne auf den Kopf. Danach höre ich kurz zu. "
                     "Sprache wird nur lokal verarbeitet und nicht gespeichert. "
                     "Gesichter erkenne ich nur bei Club-Mitgliedern mit Einwilligung.",
               "en": "Say {wake} or tap the front of my head. Then I listen for a moment. "
                     "Speech is processed locally and is not stored. "
                     "I recognize faces only of club members who consented."},
    "notice_tracking": {"de": "Meine Kamera merkt nur, dass jemand da ist, und ich drehe den Kopf zu dir.",
                        "en": "My camera only notices that someone is there, and I turn my head to you."},
    "battery_critical": {"de": "Mein Akku ist fast leer. Ich setze mich hin und ruhe mich aus.",
                         "en": "My battery is almost empty. I'll sit down and rest."},
    "battery_critical_still": {"de": "Mein Akku ist fast leer. Ich muss eine Pause machen.",
                               "en": "My battery is almost empty. I need to take a break."},
}


def lang_code(language: str) -> Lang:
    return "en" if language.lower().startswith("en") else "de"


def msg(key: str, lang: Lang = "de", **params: Any) -> str:
    return MESSAGES[key][lang].format(**params)
