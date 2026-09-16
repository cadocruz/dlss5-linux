from pathlib import Path

from linuxport import nvapi


def test_disabled_appids_reads_only_direct_proton_policy(tmp_path):
    script = tmp_path / "proton"
    script.write_text('''
def default_compat_config(appid):
    ret = set()
    if appid in ["1088850", "1418100"]:
        ret.add("disablenvapi")
    if appid in ["999"]:
        try:
            if no_nvidia_driver():
                ret.add("disablenvapi")
        except OSError:
            pass
    return ret
''', encoding="utf8")
    assert nvapi.disabled_appids(script) == {"1088850", "1418100"}


def test_disabled_appids_is_safe_on_invalid_script(tmp_path):
    script = tmp_path / "proton"
    script.write_text("not valid python (", encoding="utf8")
    assert nvapi.disabled_appids(script) == set()
