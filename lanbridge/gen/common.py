"""Shared helpers for configuration generators."""

from __future__ import annotations

from pathlib import Path

from ..settings import Settings


def banner(title: str, cfg: Settings, role: str, extra: str = "") -> str:
    lines = [
        f"# {title}",
        f"# Site: {cfg.site.name}   Role: {role}",
        f"# LAN: {cfg.network_address}/{cfg.prefixlen}  gw {cfg.site.gateway}",
    ]
    if extra:
        lines.append(f"# {extra}")
    return "\n".join(lines)


def write_files(outdir: Path, files: dict[str, str]) -> list[Path]:
    """Write a {relative_path: content} map, creating directories as needed."""
    written: list[Path] = []
    for rel, content in files.items():
        target = outdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        if rel.endswith((".sh", ".ps1")):
            target.chmod(0o755)
        written.append(target)
    return sorted(written)
