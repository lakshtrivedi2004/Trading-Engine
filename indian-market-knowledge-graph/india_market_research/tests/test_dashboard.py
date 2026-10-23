"""Milestone 3 (Flask): every page renders on demo data with the demo banner and disclaimer; the Pattern lab
runs and records tests; a missing database gives a plain error. Offline (Flask test client)."""
import re
import shutil

import pytest

from imr.data import db
from imr.data.demo import build_demo_db
from imr.data.update import load_config_files
from imr.paths import get_paths
from imr.settings import load_settings
from imr.web.app import create_app

from .conftest import REPO

PAGES = ["/", "/moves", "/movers", "/movers?tab=unusual", "/forecast", "/forecast?index=FinNifty", "/regime", "/events",
         "/patterns", "/flows", "/updates"]


@pytest.fixture(scope="module")
def demo_paths(tmp_path_factory):
    root = tmp_path_factory.mktemp("proj")
    shutil.copytree(REPO / "config", root / "config")
    paths = get_paths(root).ensure()
    with db.connect(paths.db_file(demo=True)) as c:
        build_demo_db(c, load_settings(paths.settings_file))
        load_config_files(c, paths)
    return paths


@pytest.fixture(scope="module")
def client(demo_paths):
    app = create_app(demo_paths)
    app.testing = True
    return app.test_client()


@pytest.mark.parametrize("url", PAGES)
def test_page_renders_with_demo_banner_and_disclaimer(client, url):
    r = client.get(url)
    html = r.get_data(as_text=True)
    assert r.status_code == 200, html[:500]
    assert "SIMULATED DEMO DATA" in html
    assert "not buy/sell advice" in html
    assert "Traceback" not in html


def test_charts_are_embedded_as_valid_json(client):
    import json
    html = client.get("/moves").get_data(as_text=True)
    specs = re.findall(r"data-chart='([^']*)'", html)
    assert specs
    for s in specs:
        spec = json.loads(s.replace("&#34;", '"').replace("&amp;", "&"))
        assert spec["datasets"] and spec["labels"]


def test_page_parameters(client):
    assert client.get("/moves?index=Bank%20Nifty&n=30").status_code == 200
    assert client.get("/regime?index=Sensex&years=10").status_code == 200
    assert client.get("/events?index=Nifty%2050&cat=Market%20shocks").status_code == 200
    assert client.get("/moves?index=Nope&day=1900-01-01&n=abc").status_code == 200


def test_pattern_lab_runs_and_counts_tests(client, demo_paths):
    r = client.post("/patterns", data={"cost_bp": "3", "train": "0.7", "gap": "1"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Tests in this run" in html and "Near-duplicates merged" in html
    total = int(re.search(r"all time</div><div class=\"value\">(\d+)", html).group(1))
    with db.connect(demo_paths.db_file(demo=True)) as c:
        assert db.count_tests(c) == total > 0
    assert client.get("/patterns?s_index=Sensex&s_rule=tom").status_code == 200


def test_pattern_lab_rejects_bad_settings(client):
    r = client.post("/patterns", data={"cost_bp": "-5", "train": "0.99", "gap": "0"}, follow_redirects=True)
    assert "Settings out of range" in r.get_data(as_text=True)


def test_missing_database_gives_plain_error(tmp_path):
    shutil.copytree(REPO / "config", tmp_path / "config")
    app = create_app(get_paths(tmp_path).ensure())
    r = app.test_client().get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 503 and "What to do next" in html and "imr update" in html
