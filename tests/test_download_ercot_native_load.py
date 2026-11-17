from pathlib import Path

from scripts.load import download_ercot_native_load as dl


def test_archive_urls_cover_2014_through_2025():
    assert set(dl.ARCHIVE_URLS) == set(range(2014, 2026))
    for year, url in dl.ARCHIVE_URLS.items():
        assert str(year) in url
        assert url.startswith("https://www.ercot.com/files/docs/")


def test_target_filename_matches_url_basename():
    assert dl.target_filename(2014) == "2014_ercot_hourly_load_data.xls"
    assert dl.target_filename(2025) == "Native_Load_2025.zip"


def test_dry_run_does_not_download(tmp_path, monkeypatch, capsys):
    called = False

    def fail_download(url, destination):
        nonlocal called
        called = True

    monkeypatch.setattr(dl, "download_file", fail_download)
    monkeypatch.setattr(
        "sys.argv",
        ["download_ercot_native_load.py", "--start-year", "2014", "--end-year", "2015", "--output-dir", str(tmp_path)],
    )
    dl.main()
    assert not called
    out = capsys.readouterr().out
    assert "would download" in out
