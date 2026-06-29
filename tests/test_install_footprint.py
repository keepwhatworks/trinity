"""Regression guard pinning the "two visible homes" install footprint.

Bar from the loop spec:
  > Visible footprint: just ~/.trinity/ + ~/.local/bin/.

This test parses install.sh and asserts every file-write target lands
under one of the allowed roots:
  - $HOME/.trinity/  (canonical source + data)
  - $HOME/.local/bin/  (wrappers + resolver script)
  - $HOME/.claude/skills/  (legacy symlink alias only — the parent
    dir must exist, but ONLY for the symlink to ~/.trinity/code/)

A new install.sh write target outside this set is the silent footprint
regression we want to catch — without this, "two visible homes"
quietly becomes three, four, etc.

Also asserts:
  - The legacy skill path is created as a SYMLINK only (`ln -s`),
    never as a real cloned directory.
  - The canonical install location is ~/.trinity/code/.
  - The resolver script is dropped to ~/.local/bin/.
"""
from __future__ import annotations

import re
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


# Allowed write-target prefixes. Each line in install.sh that creates
# a file / directory / symlink must resolve to a path that starts with
# one of these. The `$HOME` placeholder gets substituted at test time.
ALLOWED_PREFIXES = (
    "$HOME/.trinity/",
    "$HOME/.local/bin/",
    "$HOME/.claude/skills/",  # parent dir for the legacy symlink only
)


def _extract_write_targets() -> list[tuple[str, str]]:
    """Return [(line, target_path)] for every file/dir/symlink-creating
    operation in install.sh. Targets are kept as raw shell strings
    (with $VAR refs intact) so the assertion can substitute the known
    install.sh variables and validate."""
    text = INSTALL_SH.read_text()
    targets: list[tuple[str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        # Skip comments and blanks.
        if not stripped or stripped.startswith("#"):
            continue
        # mkdir -p "<path>"
        m = re.search(r'^\s*mkdir\s+-p\s+"?([^"\n]+?)"?\s*$', line)
        if m:
            targets.append((line, m.group(1)))
            continue
        # mkdir -p "$(dirname "<path>")"  — captures the dirname argument
        m = re.search(r'^\s*mkdir\s+-p\s+"\$\(dirname\s+"([^"]+)"\)"\s*$', line)
        if m:
            targets.append((line, m.group(1)))
            continue
        # cp "<src>" "<dst>"
        m = re.search(r'^\s*cp\s+"([^"]+)"\s+"([^"]+)"\s*$', line)
        if m:
            targets.append((line, m.group(2)))
            continue
        # ln -s "<src>" "<dst>"
        m = re.search(r'^\s*ln\s+-s\s+"([^"]+)"\s+"([^"]+)"\s*$', line)
        if m:
            targets.append((line, m.group(2)))
            continue
        # cat > "<dst>" <<EOF
        m = re.search(r'^\s*cat\s+>\s+"([^"]+)"\s+<<', line)
        if m:
            targets.append((line, m.group(1)))
            continue
        # git clone ... <target>
        # Multi-line continuation; match the standalone target line.
        m = re.search(r'^\s*"\$TRINITY_REPO_URL"\s+"([^"]+)"\s*$', line)
        if m:
            targets.append((line, m.group(1)))
            continue
        # rm -f "<path>"  (the legacy symlink replacement)
        m = re.search(r'^\s*rm\s+-f\s+"([^"]+)"\s*$', line)
        if m:
            targets.append((line, m.group(1)))
            continue
    return targets


def _resolve_shell_vars(raw: str) -> str:
    """Substitute the install.sh defaults so the path can be checked
    against ALLOWED_PREFIXES."""
    return (
        raw
        .replace("$TRINITY_SKILL_DIR", "$HOME/.trinity/code")
        .replace("$TRINITY_SKILL_LEGACY", "$HOME/.claude/skills/trinity")
        .replace("$TRINITY_BIN_DIR", "$HOME/.local/bin")
        .replace("$RESOLVER_DST", "$HOME/.local/bin/trinity-path-resolver.sh")
        # The dirname of $TRINITY_SKILL_LEGACY is $HOME/.claude/skills.
        .replace(
            'dirname "$HOME/.claude/skills/trinity"',
            "$HOME/.claude/skills",
        )
    )


class TestInstallFootprint:
    def test_every_write_lands_in_allowed_roots(self):
        """For each line in install.sh that writes a file / dir /
        symlink, the target path must start with one of the allowed
        roots. New targets outside the set break the "two visible
        homes" claim."""
        targets = _extract_write_targets()
        assert targets, (
            "Couldn't find any write targets in install.sh — the regex "
            "scanner is probably out of sync with the script's shape."
        )

        unexpected: list[tuple[str, str, str]] = []
        for line, raw_target in targets:
            resolved = _resolve_shell_vars(raw_target)
            # Match prefix OR equal-to-prefix-without-trailing-slash so
            # `mkdir -p "$HOME/.local/bin"` (the dir itself) is allowed.
            if not any(
                resolved.startswith(p) or resolved == p.rstrip("/")
                for p in ALLOWED_PREFIXES
            ):
                unexpected.append((line.strip(), raw_target, resolved))

        assert not unexpected, (
            "install.sh writes outside the allowed footprint ($HOME/.trinity/, "
            "$HOME/.local/bin/, $HOME/.claude/skills/ for the symlink). "
            "Each new target outside this set breaks the 'two visible "
            "homes' claim:\n"
            + "\n".join(
                f"  - line: {line!r}\n    raw: {raw}\n    resolved: {resolved}"
                for line, raw, resolved in unexpected
            )
        )

    def test_canonical_install_location_is_dot_trinity_code(self):
        """The default value of TRINITY_SKILL_DIR is the canonical
        install location. Pin it to ~/.trinity/code/ — anything else
        means the footprint pivot regressed."""
        content = INSTALL_SH.read_text()
        m = re.search(
            r'TRINITY_SKILL_DIR=\s*"\$\{TRINITY_SKILL_DIR:-(\$HOME[^}"]+)\}"',
            content,
        )
        assert m, "Couldn't find TRINITY_SKILL_DIR default in install.sh"
        assert m.group(1) == "$HOME/.trinity/code", (
            f"Canonical install location must be $HOME/.trinity/code; "
            f"got {m.group(1)}."
        )

    def test_legacy_skill_path_is_symlink_only(self):
        """The legacy ~/.claude/skills/trinity/ path must be created
        as a symlink to the canonical install — never as a real cloned
        directory. Two real directories on disk means the footprint
        claim ("two visible homes") is a lie."""
        content = INSTALL_SH.read_text()
        # ln -s of the canonical → legacy must exist.
        assert re.search(
            r'ln\s+-s\s+"\$TRINITY_SKILL_DIR"\s+"\$TRINITY_SKILL_LEGACY"',
            content,
        ), (
            "install.sh must create the legacy skill path as a symlink "
            "to the canonical install (ln -s $TRINITY_SKILL_DIR "
            "$TRINITY_SKILL_LEGACY)."
        )
        # A direct git clone to the legacy path is forbidden.
        assert not re.search(
            r'git\s+clone[^\n]+TRINITY_SKILL_LEGACY',
            content,
        ), (
            "install.sh must NOT clone into the legacy skill path — "
            "that would put two real copies of the source on disk."
        )

    def test_resolver_lands_in_local_bin(self):
        """The launcher_path_resolver.sh shell helper must live in
        ~/.local/bin/ alongside the wrappers — not in ~/.trinity/code/
        or some other location, since the wrappers reference it by
        a stable path."""
        content = INSTALL_SH.read_text()
        # RESOLVER_DST is defined as $TRINITY_BIN_DIR/trinity-path-resolver.sh.
        assert re.search(
            r'RESOLVER_DST=\s*"\$TRINITY_BIN_DIR/trinity-path-resolver\.sh"',
            content,
        ), (
            "Resolver must be installed at $TRINITY_BIN_DIR/"
            "trinity-path-resolver.sh — the wrappers reference that path."
        )

    def test_only_two_wrapper_binaries_in_bin(self):
        """The user-facing CLI surface in ~/.local/bin/ is just two
        wrappers: trinity-local and trinity-local-capture-host. The
        resolver is a helper, not a user-facing CLI. New top-level
        wrappers would expand the surface unexpectedly."""
        content = INSTALL_SH.read_text()
        chmod_targets = re.findall(
            r'chmod\s+\+x\s+"\$TRINITY_BIN_DIR/([^"]+)"',
            content,
        )
        # Expected set: the two wrappers + the resolver helper.
        expected = {"trinity-local", "trinity-local-capture-host", "trinity-path-resolver.sh"}
        unexpected = set(chmod_targets) - expected
        assert not unexpected, (
            f"install.sh chmod's unexpected files into $TRINITY_BIN_DIR: "
            f"{unexpected!r}. The user-facing surface should be just "
            f"trinity-local + trinity-local-capture-host (+ the resolver "
            f"helper)."
        )
