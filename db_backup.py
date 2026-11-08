# -*- coding: utf-8 -*-
"""
Бэкап БД в GitHub-репозиторий — обход эфемерного диска Render Free.

Render Free Web Service стирает диск при каждом деплое/рестарте/просыпании.
Сам репозиторий на GitHub — нет. Этот модуль синхронизирует game.db (упакованную
в zip) с файлом в репозитории через GitHub Contents API (только stdlib,
ничего pip-ить не нужно):

  - при старте бота, если локальной БД ещё нет (свежий деплой) — скачивает
    последний бэкап из репозитория и распаковывает его в DB_PATH;
  - затем, пока бот работает, раз в DB_BACKUP_INTERVAL_SEC секунд (и ещё раз
    при остановке процесса) коммитит текущую game.db обратно в репозиторий.

Нужные переменные окружения (Render Dashboard -> Environment, НЕ в setting.env
и НЕ в git):
  GITHUB_TOKEN   - personal access token с правом Contents: Read and write
                   ТОЛЬКО на этот репозиторий (fine-grained token на GitHub,
                   Settings -> Developer settings -> Fine-grained tokens).
  GITHUB_REPO    - "владелец/репозиторий", например "datzik016/card-bot".
Необязательные:
  GITHUB_BRANCH         - ветка, по умолчанию "main".
  DB_BACKUP_PATH        - путь файла бэкапа в репозитории, по умолчанию
                          "game_db_backup.zip" (лежит прямо в корне репо,
                          без подпапок — удобно загружать с телефона).
  DB_BACKUP_INTERVAL_SEC - период автосохранения в секундах, по умолчанию 300.

Если GITHUB_TOKEN/GITHUB_REPO не заданы — модуль тихо ничего не делает
(бот работает как раньше, просто без защиты от обнуления).
"""
import base64
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import zipfile

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl.create_default_context()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or ""
GITHUB_REPO = os.environ.get("GITHUB_REPO") or ""
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH") or "main"
BACKUP_PATH = os.environ.get("DB_BACKUP_PATH") or "game_db_backup.zip"
INTERVAL_SEC = int(os.environ.get("DB_BACKUP_INTERVAL_SEC") or "300")

_API_URL = "https://api.github.com/repos/{}/contents/{}".format(GITHUB_REPO, BACKUP_PATH)


def enabled():
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def _request(method, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(_API_URL, data=body, method=method)
    req.add_header("Authorization", "Bearer " + GITHUB_TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "card-bot-db-backup")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=30, context=_CTX)


def _fetch_existing():
    """Возвращает (sha, raw_bytes) текущего файла бэкапа в репо, или (None, None)."""
    try:
        with _request("GET") as r:
            info = json.loads(r.read().decode("utf-8"))
        return info.get("sha"), base64.b64decode(info["content"])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise


def restore(db_path, log=print):
    """Если локальной БД нет/пустая — восстанавливает её из бэкапа в GitHub."""
    if not enabled():
        log("[db_backup] GITHUB_TOKEN/GITHUB_REPO не заданы — восстановление пропущено")
        return False
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        return False

    try:
        sha, raw = _fetch_existing()
    except Exception as e:
        log("[db_backup] не удалось обратиться к GitHub:", e)
        return False
    if raw is None:
        log("[db_backup] бэкапа в репозитории ещё нет — стартуем с чистой БД")
        return False

    tmp_zip = db_path + ".restore.zip"
    try:
        with open(tmp_zip, "wb") as f:
            f.write(raw)
        with zipfile.ZipFile(tmp_zip) as z:
            name = z.namelist()[0]
            with z.open(name) as src, open(db_path, "wb") as dst:
                dst.write(src.read())
        log("[db_backup] БД восстановлена из GitHub (" + BACKUP_PATH + ")")
        return True
    except Exception as e:
        log("[db_backup] распаковка бэкапа не удалась:", e)
        return False
    finally:
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)


_lock = threading.Lock()


def backup(db_path, log=print):
    """Пакует db_path в zip и коммитит в репозиторий (create или update файла)."""
    if not enabled():
        return False
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return False

    with _lock:
        tmp_zip = db_path + ".backup.zip"
        try:
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(db_path, arcname=os.path.basename(db_path))
            with open(tmp_zip, "rb") as f:
                content_b64 = base64.b64encode(f.read()).decode("ascii")
        finally:
            if os.path.exists(tmp_zip):
                os.remove(tmp_zip)

        try:
            sha, _ = _fetch_existing()
        except Exception as e:
            log("[db_backup] не удалось прочитать текущий бэкап:", e)
            sha = None

        payload = {
            "message": "auto-backup game.db " + time.strftime("%Y-%m-%d %H:%M:%S"),
            "content": content_b64,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        try:
            with _request("PUT", payload) as r:
                r.read()
            return True
        except urllib.error.HTTPError as e:
            # 409/422 обычно значит "sha устарел" — попробуем ещё раз с актуальным sha
            if e.code in (409, 422):
                try:
                    sha2, _ = _fetch_existing()
                    if sha2:
                        payload["sha"] = sha2
                    with _request("PUT", payload) as r:
                        r.read()
                    return True
                except Exception as e2:
                    log("[db_backup] сохранение в GitHub не удалось (повтор):", e2)
                    return False
            log("[db_backup] сохранение в GitHub не удалось:", e)
            return False
        except Exception as e:
            log("[db_backup] сохранение в GitHub не удалось:", e)
            return False


def start_periodic(db_path, interval_sec=None, log=print):
    """Фоновый поток: автосохранение раз в interval_sec секунд, пока процесс жив."""
    if not enabled():
        log("[db_backup] автосохранение выключено (нет GITHUB_TOKEN/GITHUB_REPO)")
        return None
    interval_sec = interval_sec or INTERVAL_SEC

    def _loop():
        while True:
            time.sleep(interval_sec)
            try:
                if backup(db_path, log=log):
                    log("[db_backup] game.db сохранена в GitHub")
            except Exception as e:
                log("[db_backup] периодический бэкап упал:", e)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    log("[db_backup] автосохранение каждые {} сек в {} -> {}".format(
        interval_sec, GITHUB_REPO, BACKUP_PATH))
    return t
