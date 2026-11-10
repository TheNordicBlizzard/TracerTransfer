import pytest

from scripts.sign_update_release import main


def test_signing_cli_requires_explicit_private_key(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(['--version', '0.1.3', '--commit', '0' * 40,
              '--client', str(tmp_path / 'missing-installer.exe'),
              '--output', str(tmp_path / 'feed.json')])
    assert error.value.code == 2
    assert '--key-file' in capsys.readouterr().err
    assert not (tmp_path / 'feed.json').exists()
