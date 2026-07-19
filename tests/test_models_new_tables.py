# tests/test_models_new_tables.py
# -*- coding: utf-8 -*-
from data import models


def test_st_list_fields():
    assert models.ST_LIST_FIELDS == {"code", "name", "st_type",
                                    "latest_price", "change_pct"}


def test_research_report_fields():
    assert models.RESEARCH_REPORT_FIELDS == {
        "code", "name", "rating", "title", "org",
        "analyst", "pub_date", "target_price", "ts"}


def test_fundamentals_cache_fields():
    assert models.FUNDAMENTALS_CACHE_FIELDS == {"code", "source",
                                                "payload_json", "ts"}


def test_schema_has_new_tables():
    assert "CREATE TABLE IF NOT EXISTS st_list" in models.SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS research_report" in models.SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS fundamentals_cache" in models.SCHEMA_SQL


def test_table_fields_registered():
    assert "st_list" in models.TABLE_FIELDS
    assert "research_report" in models.TABLE_FIELDS
    assert "fundamentals_cache" in models.TABLE_FIELDS


def test_st_list_aliases():
    assert models.ST_LIST_ALIASES["代码"] == "code"
    assert models.ST_LIST_ALIASES["涨跌幅"] == "change_pct"


def test_research_report_aliases():
    assert models.RESEARCH_REPORT_ALIASES["投资评级"] == "rating"
    assert models.RESEARCH_REPORT_ALIASES["目标价"] == "target_price"
