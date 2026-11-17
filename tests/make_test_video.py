"""Génère une vidéo de test 100 % hors ligne : mire animée avec changements
de scène, voix de synthèse Windows (français) et deux « explosions ».

  python tests/make_test_video.py chemin/sortie.mp4
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipper.config import ffmpeg  # noqa: E402

LINES = [
    ("Salut tout le monde, bienvenue sur ce nouveau stream.", 2.0),
    ("Aujourd'hui on part faire un braquage avec l'équipe.", 3.0),
    ("Attention, la police arrive juste derrière nous !", 1.0),
    ("Accroche-toi, on va sauter du pont avec la voiture !", 2.5),
    ("Non mais c'est complètement fou, on a réussi !", 4.0),
    ("Bon, on se calme, on va déposer l'argent au garage.", 3.0),
    ("Franchement, c'était la meilleure course poursuite de ma vie.", 2.0),
    ("Abonnez-vous si vous voulez voir la suite demain.", 1.0),
]

PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fr = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'fr*' } | Select-Object -First 1
if ($fr) { $s.SelectVoice($fr.VoiceInfo.Name) }
$s.Rate = 1
$p = New-Object System.Speech.Synthesis.PromptBuilder
{body}
$s.SetOutputToWaveFile('{out}')
$s.Speak($p)
$s.Dispose()
"""


def make_speech(out: Path) -> None:
    body = []
    for text, pause in LINES:
        safe = text.replace("'", "''")
        body.append(f"$p.AppendText('{safe}')")
        body.append(f"$p.AppendBreak([TimeSpan]::FromSeconds({pause}))")
    script = PS.replace("{body}", "\n".join(body)).replace("{out}", str(out))
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as f:
        f.write(script)
        ps1 = f.name
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1], check=True, capture_output=True, timeout=120)
    finally:
        Path(ps1).unlink(missing_ok=True)


def make_test_video(out: Path, duration: float = 70.0) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    speech = out.with_suffix(".speech.wav")
    make_speech(speech)
    seg = duration / 4
    cmd = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d={seg}",
        "-f", "lavfi", "-t", str(seg), "-i", "mandelbrot=s=1280x720:r=30",
        "-f", "lavfi", "-i", f"smptehdbars=s=1280x720:r=30:d={seg}",
        "-f", "lavfi", "-t", str(seg), "-i", "life=s=1280x720:r=30:mold=10:ratio=0.1:life_color=#00ff88:death_color=#202060",
        "-i", str(speech),
        "-f", "lavfi", "-i", f"anoisesrc=d={duration}:c=pink:a=0.9",
        "-filter_complex",
        "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0,format=yuv420p[v];"
        "[5:a]volume='if(between(t,24,27)+between(t,52,54),1,0)':eval=frame[n];"
        "[4:a]aresample=48000,apad[sp];[sp][n]amix=inputs=2:duration=shortest:normalize=0[a]",
        "-map", "[v]", "-map", "[a]", "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", str(out),
    ]
    subprocess.run(cmd, check=True, timeout=300)
    speech.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "_tmp" / "test_video.mp4"
    print(make_test_video(dest))
