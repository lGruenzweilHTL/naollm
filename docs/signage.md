# Booth signage

> **Draft, to be checked by the school.** This is a template for the school's data-protection contact and the club supervisor (SPEC §12, §17 question 5). It is not legal text.

Print the variant that matches `config.yaml`. Change the wake phrases if `audio.wake_words` changes.

## Standard: robot wake engine, motors on

**Deutsch**

> Sag „Hallo NAO" oder tippe ihm vorne auf den Kopf. Danach hört er kurz zu und verarbeitet Sprache nur lokal auf diesem Laptop. Es wird nichts gespeichert. Die Kamera erkennt nur, dass jemand da ist, und dreht den Kopf zu dir. Gesichter werden nur bei Club-Mitgliedern mit Einwilligung erkannt.

**English**

> Say "Hey NAO" or tap the front of his head. He then listens for a moment and processes speech only locally on this laptop. Nothing is stored. The camera only notices that someone is there, and he turns his head to you. Faces are recognized only for club members who consented.

## Motors off

Leave out „und dreht den Kopf zu dir" and "and he turns his head to you".

## Laptop wake engine (`audio.wake_engine: laptop`)

Replace the second sentence with:

> Sein Mikrofon schickt die ganze Zeit Ton an diesen Laptop. Dort wird er nur auf „Hallo NAO" geprüft und gleich wieder verworfen. Nach dem Weckwort hört er kurz zu und verarbeitet Sprache nur lokal auf diesem Laptop.

> His microphone sends audio to this laptop all the time. There it is only checked for "Hey NAO" and then discarded. After the wake phrase he listens for a moment and processes speech only locally on this laptop.

## Age game (`privacy.age_game_enabled: true`)

Only after the school has cleared the game. Add:

> KI-Hinweis: Wenn du NAO darum bittest, schätzt er mit einer KI ungefähr dein Alter. Die Schätzung wird nicht gespeichert. Geschlecht und Gefühle werden nie erkannt.

> AI notice: If you ask NAO, he uses AI to roughly guess your age. The guess is not stored. Gender and emotions are never detected.

## Cloud endpoint

`naollm preflight` fails when the LLM endpoint is not local. If the school ever accepts a cloud endpoint, the signage must say that conversation text leaves the local network, and to which provider. Do not use this booth text in that case.
