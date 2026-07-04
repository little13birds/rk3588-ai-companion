"""Regression checks for HDMI eye GUI audio isolation."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EYE_RENDERER = ROOT / "eye_engine" / "eye_renderer.py"


def test_eye_renderer_never_initializes_pygame_mixer():
    source = EYE_RENDERER.read_text(encoding="utf-8")
    assert "SDL_AUDIODRIVER" in source
    assert "dummy" in source
    assert "pygame.init()" not in source
    assert "pygame.display.init()" in source
    assert "pygame.font.init()" in source
    assert "pygame.mixer.init" not in source


if __name__ == "__main__":
    test_eye_renderer_never_initializes_pygame_mixer()
    print("test_eye_renderer_never_initializes_pygame_mixer PASS")
