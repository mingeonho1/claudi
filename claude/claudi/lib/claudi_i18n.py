#!/usr/bin/env python3
# ~/.claude/claudi/lib/claudi_i18n.py
#
# Shared language contract for claudi scripts (stdlib only, Python 3.9+).
# claudi 스크립트가 공유하는 언어 계약(표준 라이브러리만, Python 3.9+).
#
# Language resolution order (first match wins):
#   1. env CLAUDI_LANG, when it is "en" or "ko".
#   2. <config>/claudi/config.json key "lang" ("en" | "ko"), written by the installer.
#   3. default "en".
# 언어 결정 순서(먼저 맞으면 멈춤): 1) 환경변수 CLAUDI_LANG, 2) config.json 의 "lang",
# 3) 기본값 "en".
#
# CONFIG_DIR = $CLAUDE_CONFIG_DIR or ~/.claude.
# CONFIG_DIR 는 $CLAUDE_CONFIG_DIR(없으면 ~/.claude).
#
# This module never raises: any error falls back to English.
# 이 모듈은 예외를 던지지 않는다 — 어떤 오류든 영어로 폴백한다.

import json
import os

LANGS = ("en", "ko")
DEFAULT_LANG = "en"

CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
CONFIG_JSON = os.path.join(CONFIG_DIR, "claudi", "config.json")


def get_lang():
    """Return the current language: env CLAUDI_LANG > config.json "lang" > "en".
    현재 언어를 돌려준다: 환경변수 > config.json > 기본 영어. 어떤 오류든 영어로 폴백."""
    env = os.environ.get("CLAUDI_LANG")
    if env in LANGS:
        return env
    try:
        with open(CONFIG_JSON, encoding="utf-8") as f:
            lang = json.load(f).get("lang")
        if lang in LANGS:
            return lang
    except Exception:
        pass
    return DEFAULT_LANG


def t(en, ko):
    """Return the value for the current language (English by default).
    현재 언어에 해당하는 값을 돌려준다(기본 영어)."""
    return ko if get_lang() == "ko" else en
