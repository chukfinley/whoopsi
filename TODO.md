# TODO — Eigener Schlaf-Algo-Trainer (Weg zu ~80–85 %)

**Ziel:** Ein eigenes Modell trainieren, das vom **Handgelenk-Signal** (HR, HRV/RR, Accel, Gyro, PPG, SpO2, Temp) auf das **echte Schlafstadium** schließt — so genau wie physikalisch möglich (~80–85 %, das ist die Modalitäts-Decke; selbst Menschen mit EEG sind sich nur ~83 % einig).

## Warum überhaupt
- Aktueller Stand: Hybrid-Modell **~74 %, alle Stages ≥70 %** (Awake 75, Light 70, Deep 87, REM 72). Gut, aber:
- Decke liegt am **Signal**, nicht am Algo. Whoop-Labels (unsere „Wahrheit") sind selbst nur Handgelenk-Schätzungen → wir lernen Whoops Fehler mit.
- **Der einzige echte Sprung:** auf Daten trainieren wo **Armband-Rohdaten + echtes Schlaflabor (PSG/EEG) gleichzeitig** gemessen wurden. Genau das hat Whoop gemacht.

## Datensätze (öffentlich, recherchiert — `algorithms/algo12_seq/analysis/datasets_psg_wrist.md`)
| Dataset | Signale | Nächte | Zugang | Priorität |
|---|---|---|---|---|
| **DREAMT** (PhysioNet) | Handgelenk **PPG + Accel + HR + IBI** + PSG 5-Klassen | 100 | Account + CITI-Training + DUA (paar Tage) | ⭐ Bester Match |
| **Walch 2019** (`sleep-accel`, PhysioNet) | Accel + HR + PSG | 31 | **Offen, 1-Klick** | Sofort-Prototyp |
| **BIDSleep** (PhysioNet) | Accel + HR + PSG | 253 | Offen | Volumen HR+Accel |
| **MESA** (NSRR) | Finger-PPG + Aktigrafie + PSG | 2.237 | NSRR-Antrag (Komitee) | Großer PPG-Encoder |

## Schritte
- [ ] **1. DREAMT-Zugang beantragen JETZT** (Credentialing dauert) — PhysioNet-Account, CITI-Training, DUA signieren: https://physionet.org/content/dreamt/
- [ ] **2. Walch sofort runterladen** (offen) → Pipeline + Fine-tuning-Harness bauen während DREAMT-Antrag läuft.
- [ ] **3. Feature-Pipeline angleichen** — unsere 137 Features (`algo12_seq/build_aug.py` + `regen_gates.py`) auf die Dataset-Signale mappen (PPG/HR/Accel). Gemeinsamer Feature-Satz Whoop ↔ DREAMT.
- [ ] **4. Pretrain** auf DREAMT (+ ggf. MESA) → 4-Klassen Wake/Light/Deep/REM. Arch-Optionen: unser LightGBM-Gate-Cascade ODER ein TCN/Conformer (siehe `sleepkit_eval.md`).
- [ ] **5. Fine-tune** auf unsere 78 Whoop-Nächte (Domain-Gap: DREAMT=Empatica, wir=Whoop; nicht zero-shot).
- [ ] **6. Eval** LONO/5-fold gegen Whoop-Labels — Ziel: REM-vs-Light + Awake über die aktuelle 74 %-Decke heben. Vergleich im Dashboard (`gen_dashboard.py`).
- [ ] **7. Integrieren** als neue Methode (`preds/<name>.npy`) + ins Produktions-Modell falls besser.

## Realismus-Check
- DREAMT = Apnoe-Kohorte → viele Wach-/Arousal-Events → trifft genau unsere **Awake-Schwäche** (nur 6.9 % der Daten).
- MESA-PPG ist **Finger**, nicht Handgelenk → Teil-Mismatch, trotzdem riesiger PPG-Encoder.
- Erwartung: **+5–10 pp** realistisch, nicht 100 %. „Exakt" existiert bei Schlaf-Staging nicht (Inter-Rater ~83 %).
- Optionaler Königsweg (teuer): **eigene Studie** — Leute mit EEG-Kappe + Whoop gleichzeitig. Nur falls öffentliche Datensätze nicht reichen.

## Weitere Ideen / Brainstorm (aus der Agenten-Recherche)

### Features klauen (billig, sofort machbar — auf Signalen die wir HABEN)
- [ ] **SleepKit REM-Features** (AmbiqAI, BSD-3): sauberes **LF/HF-Verhältnis** (Bänder 0.04–0.15 / 0.15–0.4 Hz), **Atemraten-Variabilität**, **Puls-Amplituden-Variabilität** (PIAV/PIIV). Bester billiger Schuss aufs REM-vs-Light-Problem. (`analysis/sleepkit_eval.md`)
- [ ] **YASA-Methodik neu implementieren** (kein EEG nötig): **Triple-Temporal-Smoothing** jedes Features (roh + 7.5-min zentriert-triangular + 2-min trailing rolling). Hilft vermutlich Awake-vs-Light-Grenze. (`analysis/eval_yasa.md`)
- [ ] **Atem-Feature aus RR** (JMIR e24704 Firstbeat-Ansatz): Atem-zu-Atem-Intervall-Variabilität + RSA-HF-Amplitude. REM = unregelmäßige Atmung, Deep = langsam/regelmäßig. (`analysis/eval_jmir2021.md`)

### Architektur-Experimente
- [ ] **TCN / Conformer Sequenz-Modell** statt LightGBM-Gates+Viterbi — Feature-Sequenz rein, Übergänge end-to-end lernen; Conformer für ~90-min REM-Zyklus-Periodik. (BiGRU war zu langsam/kein Gewinn — verworfen.)
- [ ] **ECG-only NN** (CIBM 2024, DOI 10.1016/j.compbiomed.2024.108545, κ≈0.725) — beat-basierte Cardio-Features, nah an unserem HRV-Pfad. Nur falls sauberere RR-Extraktion gelingt.

### Strain — Daten-Problem, nicht Formel-Problem (Befund 2026-06-12)
- Unser Strain korreliert **−0.23** mit Whoop-Strain (algo4 UND ein sauberer TRIMP, beide negativ). Datums-Shift hilft nicht.
- Ursache: **`corr(Tages-Abdeckung, Whoop-Strain) = −0.23`** — an aktiven Tagen (hoher Strain) haben wir die WENIGSTEN Sensordaten. PPG-HR versagt bei Bewegung/Sport + VPS/BLE-Erfassung reißt unterwegs ab. Die strain-definierenden Trainingsminuten fehlen in der DB.
- = Strain-Äquivalent zum EEG-Problem: Info nicht erfasst, nicht falsch gerechnet. Kalibrieren sinnlos (kein Signal).
- [ ] **Fix nur über bessere Tages-Erfassung**: kontinuierliche HR auch beim Sport (BLE-Sync häufiger / lückenlos / Bewegungs-robuste HR). Dann TRIMP/Banister oder PySR-Formel (`log(steps·(√zone13+0.105))+√zone45`, MAE 1.04 laut `algorithms/CLAUDE.md`) anwenden.
- [ ] Bis dahin: Strain im Dashboard als „datenlimitiert" markiert lassen.

### Datenqualität (oft mehr wert als Algo)
- [ ] **Mehr Nächte** — DB wächst automatisch über VPS. Awake ist nur 6.9 % → jede Nacht hilft der Minderheit.
- [ ] **Sauberere RR/IBI** aus PPG `rawHex` (aktuell nur ~31 % Records mit Temp/PPG) — bessere HRV = besserer REM-Detektor. Wake-Schwäche ist teils PPG-RR-Rauschen, nicht der Algo.
- [ ] **Gyro/Skin-Temp** voll nutzen (Gyro war Schlüssel-Separator für Awake; Temp noch wenig genutzt).

### Königsweg (teuer, nur falls nötig)
- [ ] **Eigene PSG-Studie**: viele Leute, EEG-Kappe + Whoop gleichzeitig → perfekte Labels → near-perfekter Algo. Whoops eigener Weg. Nur falls öffentliche Datensätze (DREAMT/MESA) nicht reichen.

### Verworfen (Sackgassen, dokumentiert)
- Accelerometer-Bewegung trennt Awake/REM NICHT (AUC ~0.5) — `mv_burst_to_hr_response` & Co tot.
- YASA / Mentalab / SleepEEGpy / SIESTA — alle **EEG-only**, laufen nicht auf Handgelenk-Daten.
- SleepECG pretrained — kippt auf unserer PPG-HR-Qualität (sagt alles REM).
- TabPFN / EBM / CRF — schlechter als HistGBT (siehe `algorithms/CLAUDE.md`).

## Was schon erledigt ist
- Balancierter Klassifizierer `algo12_seq/` (alle Stages ≥70 %, Hybrid). Modelle in `algo12_seq/models/`.
- Externe Tools geprüft: YASA/Mentalab/SleepEEGpy/SIESTA = **EEG-only, unbrauchbar**; SleepKit = Teile-Spender; SleepECG = bricht auf unserer Signalqualität (alles-REM). → Pretrain auf echten PSG+Wrist-Daten ist der Weg.
- Dashboards: `algo12_seq/gen_dashboard.py` (Methoden-Vergleich), `gen_daily.py` (Whoop-artige Tagesansicht: Schlaf/Recovery/Strain Whoop vs. uns).
