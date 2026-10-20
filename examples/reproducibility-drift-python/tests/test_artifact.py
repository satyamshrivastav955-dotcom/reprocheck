import secrets
from pathlib import Path


def test_build_artifact_is_created() -> None:
    artifact = Path(__file__).resolve().parent.parent / "reprocheck-demo-artifact.txt"
    artifact.write_text(secrets.token_hex(16) + "\n", encoding="utf-8")
    assert len(artifact.read_text(encoding="utf-8").strip()) == 32
