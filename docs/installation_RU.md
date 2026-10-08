# Установка

[Документация](README_RU.md) · [English](installation.md)

## Требования

Git, Docker Engine и Compose v2; доступ к камерам по сети; доступное для записи хранилище со свободным местом сверх `MIN_FREE_SPACE_GB` (по умолчанию 5 GiB). Docker включает интерфейс, Python, FFmpeg/ffprobe, сертификаты CA и базу часовых поясов. Нужен современный браузер с WebRTC/MP4 и совместимый кодек камеры; начните с H.264. Предустановленных камер и ручной настройки базы нет.

[Быстрый старт README](../README_RU.md#быстрый-старт) собирает исходники `dev` на Linux Engine с сетью хоста. Нужны **Compose 2.24.4+** и свободные TCP-порты хоста 8554, 8889, 9997; этот вариант не предназначен для Docker Desktop. Выбор режима описан в [сетевом руководстве](networking_RU.md).

## Подготовка

```sh
git clone --branch dev https://github.com/vvbelousov/TupoNVR.git
cd TupoNVR
cp .env.example .env
```

Измените `.env` до запуска:

- LAN: задайте `NVR_BIND=0.0.0.0`, обе переменные `AUTH_USERNAME` и `AUTH_PASSWORD`. Для доступа только с localhost оставьте `NVR_BIND=127.0.0.1`; пустые учётные данные отключают вход. Только одно заполненное значение вызывает ошибку запуска.
- Linux LAN overlay: оставьте `WEBRTC_HOST` пустым для обнаружения интерфейсов хоста. В bridge-режиме для удалённых зрителей задайте LAN IP хоста, для localhost оставьте пустым.
- Оставьте `DATA_DIR=./data`, `DEFAULT_RECORDING_PATH=./recordings` или выберите каталоги хоста. Внешнее хранилище сначала смонтируйте и проверьте его видимость в контейнере.
- При стандартном запуске от root Compose создаёт каталоги bind mounts, приложение — локальный каталог назначения `default`. Для non-root заранее создайте доступные для записи каталоги с владельцем `NVR_UID`/`NVR_GID`; см. [конфигурацию](configuration_RU.md#права-контейнера).

## Сборка исходников

Linux LAN:

```sh
NVR_IMAGE=tuponvr:local docker compose -f docker-compose.yml -f compose.lan.yml up -d --build
```

Bridge / localhost:

```sh
NVR_IMAGE=tuponvr:local docker compose up -d --build
```

Значение перед командой переопределяет ссылку на опубликованный образ в `.env`. Для последующих сборок задайте `NVR_IMAGE=tuponvr:local` в `.env` или повторяйте префикс. Невыпущенные изменения требуют сборки исходников.

## Запуск готового образа

Используйте файлы того же проверенного релиза, что и выбранный образ: `docker-compose.yml`, `mediamtx.yml`, `.env.example` и нужные overlays. В примере задано `NVR_IMAGE=vvbelousov/tuponvr:0.2.0`; проверьте наличие в [тегах Docker Hub](https://hub.docker.com/r/vvbelousov/tuponvr/tags) и выберите фиксированную версию или digest. Если образ недоступен, соберите исходники. Тег `latest`, если опубликован, может меняться при обновлениях.

Linux LAN:

```sh
docker compose -f docker-compose.yml -f compose.lan.yml up -d --no-build --pull always
```

Bridge:

```sh
docker compose up -d --no-build --pull always
```

Необязательный `compose.image.yml` требует непустой `NVR_IMAGE` и задаёт `pull_policy: always`:

```sh
docker compose -f docker-compose.yml -f compose.image.yml up -d --no-build
```

Для LAN добавьте `-f compose.lan.yml` после основного файла и перед overlay образа. Готовый образ не требует Node/Python на хосте или локальной сборки.

## Первый запуск

Откройте `http://<host-address>:8080` (`http://localhost:8080` для localhost), войдите при включённой аутентификации, выберите часовой пояс установки в **Аккаунт → Предпочтения** до использования расписаний и местного времени архива.

1. Добавьте камеру в **Камерах** или пустом **Обзоре**: имя, основной RTSP URL и учётные данные. Отдельные поля учётных данных приоритетнее данных в URL.
2. Нажмите **Проверить**; отключённые камеры также можно проверить. Успешная проверка не гарантирует совместимости кодека с браузером или MP4.
3. По умолчанию камера и запись включены: RTSP по TCP, только видео без перекодирования, сегменты 600 секунд, хранение семь дней. Проверьте **Пишет** в Обзоре. При блокировке откройте Хранилище; не уменьшайте резерв ради скрытия предупреждения. До записи на внешний ресурс включите [защиту по идентификатору](storage_RU.md#защита-каталогов-назначения).
4. Откройте **Смотреть**, затем **Архив**. Сегмент должен завершиться (обычно около десяти минут или ближайшая граница часа) и пройти индексирование (проверка каждые пять минут или после штатной остановки записи).

## Управление запуском

Всегда используйте одни и те же Compose-файлы. Изменение `.env` требует `up -d` для пересоздания окружения; `restart` недостаточно. Для исходников нужен `--build`, для выбранного опубликованного образа — `--no-build --pull always`.

Примеры Linux LAN:

```sh
docker compose -f docker-compose.yml -f compose.lan.yml ps
docker compose -f docker-compose.yml -f compose.lan.yml logs --tail=100 nvr-app
docker compose -f docker-compose.yml -f compose.lan.yml stop nvr-app
docker compose -f docker-compose.yml -f compose.lan.yml down
```

Контейнеры используют `restart: unless-stopped`. `/health` проверяет работу процесса, а не камер/хранилища; `/ready` — доступность MediaMTX. См. [резервирование и обновления](storage_RU.md#обновление-резервирование-и-восстановление), [безопасность](security-hardening_RU.md), [диагностику](troubleshooting_RU.md).
