import importlib.util
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "download_asos_weather_la.py"
SPEC = importlib.util.spec_from_file_location("download_asos_weather_la", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to import downloader from {SCRIPT_PATH}")
download_asos_weather_la = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(download_asos_weather_la)


def test_land_station_selection_excludes_offshore_platforms():
    station_ids = download_asos_weather_la.LAND_STATION_IDS

    assert len(station_ids) == 33
    assert len(station_ids) == len(set(station_ids))
    assert {"ATP", "GRY", "GBK", "CYD", "GHB", "SPR"}.isdisjoint(station_ids)


def test_request_covers_one_utc_calendar_year_for_selected_stations():
    parameters = parse_qs(
        urlparse(download_asos_weather_la.build_request_url(date(2014, 1, 1), date(2014, 2, 1))).query
    )

    assert parameters["station"] == list(download_asos_weather_la.LAND_STATION_IDS)
    assert parameters["year1"] == ["2014"]
    assert parameters["year2"] == ["2014"]
    assert parameters["month2"] == ["2"]
    assert parameters["tz"] == ["UTC"]
    assert parameters["format"] == ["onlycomma"]
    assert parameters["data"] == list(download_asos_weather_la.WEATHER_COLUMNS)
