from jump import config


def test_load_keys(tmp_path):
    f = tmp_path / "keys"
    f.write_text("# my keys\nJEV_API_KEY=abc\nGEMINI_API_KEY = 'xyz'\n\nnot a key line\n")
    assert config.load_keys(f) == {"JEV_API_KEY": "abc", "GEMINI_API_KEY": "xyz"}
    assert config.load_keys(tmp_path / "missing") == {}
