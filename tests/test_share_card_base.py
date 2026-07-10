

class TestPilDemotedToExtra:
    """Pillow left core deps 2026-07-10 (council_25c534c5f1bf826c: share cards
    measured-dormant). Every card command must degrade with the install hint,
    never a raw ImportError."""

    def test_require_pil_hints_when_absent(self, monkeypatch, capsys):
        import builtins
        real_import = builtins.__import__
        def fake(name, *a, **k):
            if name == "PIL":
                raise ImportError("No module named 'PIL'")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake)
        from trinity_local.share_card_base import require_pil
        assert require_pil() is False
        out = capsys.readouterr().out
        assert "trinity-local[share]" in out

    def test_require_pil_true_when_present(self):
        from trinity_local.share_card_base import require_pil
        assert require_pil() is True  # dev/test env installs the [share] extra
