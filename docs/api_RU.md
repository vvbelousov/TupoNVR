# Интерфейс и API

[Документация](README_RU.md) · [English](api.md)

- **Аккаунт:** язык, пояс установки, настроенное имя пользователя и выход. `POST /api/logout` отзывает серверную сессию, удаляет её WebRTC-ресурсы, очищает cookie браузера; кэшированные данные HTTP Basic не дают доступа. Пароль задаётся окружением, UI смены нет.
- **Обзор:** число камер, доступность, прогресс записи, место и ошибки. Смотреть открывает `/cameras/<id>/live` с основным потоком. Обновление/навигация сохраняют камеру; выбор Multiview независим.
- **Камеры:** создание, правка, отключение, проверка и удаление. Пустые пароль/RTSP URL сохраняют прежнее значение при правке; отдельный переключатель убирает дополнительный поток. URL в списке скрывают пути/query с потенциальными секретами.
- **Мультиэкран:** добавление live-камер, перемещение за заголовок, изменение размера за угол, `contain`/`cover`. Отдельный просмотр использует основной поток.
- **Архив:** одна, несколько или все камеры на общей местной дате/времени. Дорожки показывают пропуски; общие play/pause, seek и скорость. Каждая камера независимо входит в fullscreen и переходит между сегментами. Доступны previous/next для одной камеры, automatic-next, страницы и скачивание.
- **Хранилище:** доступность, запись, резерв места, защита от пропавших mounts и состояние доставки вебхуков.

OpenAPI доступен по `/docs`. Основные маршруты:

| Область | Маршруты |
|---|---|
| Камеры | `GET/POST /api/cameras`, `GET/PUT/PATCH/DELETE /api/cameras/{id}`, `GET /api/cameras/{id}/edit`, `POST /api/cameras/{id}/start`, `/stop`, `/check`, `GET /api/cameras/{id}/status` |
| Файлы архива | `GET /api/recordings?camera_id=&date=YYYY-MM-DD&limit=&offset=`, `GET/DELETE /api/recordings/{id}`, `GET /api/recordings/{id}/download` |
| Поиск архива | `GET /api/recordings/timeline?camera_id=&start=&end=`, `GET /api/recordings/at?camera_id=&time=`, `GET /api/recordings/{id}/adjacent?direction=next\|previous`, `POST /api/recordings/timelines`, `POST /api/recordings/resolve` |
| Время и язык | `GET /api/config`, `GET/PUT /api/time`, `GET /api/time/day?date=YYYY-MM-DD`, `POST /api/time/resolve`, `GET /api/language` |
| Раскладка и обзор | `GET/PUT /api/layout`, `GET /api/dashboard` |
| Хранилища и уведомления | `GET /api/storage/status`, `GET /api/storage/destinations`, `PUT /api/storage/destinations/{name}`, `POST /api/storage/destinations/{name}/protection`, `GET /api/notifications/status` |
| Состояние служб | `GET /health`, `GET /ready`, `GET /metrics` |

При включённом входе `POST /api/login` создаёт HttpOnly cookie сессии. CLI используют тот же login endpoint и сохраняют cookie. Защищённые API без входа возвращают 401 без HTTP Basic challenge. `/health` и `/ready` публичные; `/metrics` требует входа, если он настроен. OpenAPI описывает актуальные схемы; имена `{id}` здесь условные.

API [экспорта и времени](time-and-archive_RU.md#api-времени-и-архива) и [ручной очистки](recording-cleanup_RU.md#архитектура-и-api) описаны в соответствующих руководствах.

Настройки камер через YAML: [схема, bootstrap и API импорта/экспорта](camera-configuration_RU.md).
