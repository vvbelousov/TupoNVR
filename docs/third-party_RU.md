# Стороннее ПО

[Документация](README_RU.md) · [English](third-party.md)

Apache-2.0 распространяется на собственный код TupoNVR. Зависимости и компоненты контейнеров сохраняют свои лицензии; OCI license label образа описывает приложение, а не все пакеты.

- Включённый WebRTC reader MediaMTX сохраняет MIT-текст в `frontend/public/MEDIAMTX-LICENSE.txt`; сервер также отдаёт `/MEDIAMTX-LICENSE.txt`.
- Сборка frontend извлекает тексты лицензий установленных runtime npm packages в `THIRD_PARTY_LICENSES.txt` и включает в static assets. Это React, React DOM, зависимости layout и runtime transitives. Отсутствие notice вызывает ошибку сборки; перед релизом выясните причину вместо подавления проверки. Генерируемый файл игнорируется локально, пересоздаётся из lockfile/install.
- Python distributions сохраняют метаданные лицензий в site-packages. Requirements исходников и SBOM образа определяют установленные версии.
- MediaMTX поставляется отдельным upstream image; сохраняйте notices и следуйте его лицензии.
- Debian FFmpeg и связанные codec/system libraries имеют свои условия, включая GPL-компоненты сборки Debian. Package copyright files сохраняются в образе. До распространения проверьте точные binary packages и требования corresponding source; Apache проекта не заменяет их. Debian source packages соответствующих версий доступны в архивах Debian. Публикующий образ отвечает за доступность исходников и необходимые notices.

Base images, OS packages и разрешение транзитивных Python-зависимостей могут меняться между сборками даже при фиксированных прямых зависимостях. Release SBOM/provenance и неизменяемые digests опубликованных образов фиксируют конкретный результат: это повторяемый процесс, не обещание побитового совпадения будущих сборок. Используйте фиксированные теги/digests, проверяйте Dependabot и upstream security advisories.
